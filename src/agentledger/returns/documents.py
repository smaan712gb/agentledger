"""Build return inputs from the client's filed tax documents (no re-keying).

Each mapped amount carries provenance: which document and which box it came from. Values
come only from fields intake already grounded against the document text. Anything that
cannot be placed with certainty (an unknown owner, a 1099-NEC with no business to attach it
to) is reported for the preparer and never guessed. A box the document carries but that could
not be read (not a number, not a valid code) is listed in `unreadable`: the item goes on the
return without it, and it blocks review as a missing amount until a person enters it
(facts.missing_required); it is never dropped or taken as zero.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from . import facts as fx

Z = Decimal(0)

# document type -> {box key: (input list, input field)}
BOXES: dict[str, dict[str, tuple[str, str]]] = {
    "W-2": {"box1": ("w2s", "wages"), "box2": ("w2s", "federal_withholding"), "box3": ("w2s", "ss_wages"),
            "box4": ("w2s", "ss_tax"), "box5": ("w2s", "medicare_wages"), "box6": ("w2s", "medicare_tax"),
            "box7": ("w2s", "ss_tips"), "box10": ("w2s", "dependent_care_benefits")},
    "1099-INT": {"box1": ("interest", "interest"), "box2": ("interest", "early_withdrawal_penalty"),
                 "box3": ("interest", "us_savings_bond_interest"), "box4": ("interest", "federal_withholding"),
                 "box6": ("interest", "foreign_tax_paid"), "box8": ("interest", "tax_exempt_interest"),
                 "box9": ("interest", "private_activity_bond_interest")},
    "1099-DIV": {"box1a": ("dividends", "ordinary"), "box1b": ("dividends", "qualified"),
                 "box2a": ("dividends", "capital_gain_distributions"), "box2b": ("dividends", "unrecaptured_1250_gain"),
                 "box2c": ("dividends", "section_1202_gain"), "box2d": ("dividends", "collectibles_gain"),
                 "box3": ("dividends", "nondividend_distributions"), "box4": ("dividends", "federal_withholding"),
                 "box5": ("dividends", "section_199a_dividends"), "box7": ("dividends", "foreign_tax_paid"),
                 "box12": ("dividends", "exempt_interest_dividends"), "box13": ("dividends", "private_activity_bond_dividends")},
    "1099-R": {"box1": ("retirement", "gross_distribution"), "box2a": ("retirement", "taxable_amount"),
               "box4": ("retirement", "federal_withholding")},
    "SSA-1099": {"box5": ("social_security", "net_benefits"), "box6": ("social_security", "federal_withholding")},
    "1099-G": {"box1": ("unemployment", "amount"), "box4": ("unemployment", "federal_withholding")},
}
NAME_FIELDS = {"W-2": ("employer_name", "employer_name"), "1099-INT": ("payer_name", "payer"), "1099-DIV": ("payer_name", "payer"),
               "1099-R": ("payer_name", "payer")}
# Loose names a model might still use, mapped to the canonical box.
ALIASES = {
    "W-2": {"wages": "box1", "wages_tips_other_compensation": "box1", "federal_income_tax_withheld": "box2",
            "social_security_wages": "box3", "social_security_tax_withheld": "box4", "medicare_wages_and_tips": "box5",
            "medicare_tax_withheld": "box6"},
    "1099-INT": {"interest_income": "box1", "federal_income_tax_withheld": "box4", "tax_exempt_interest": "box8"},
    "1099-DIV": {"total_ordinary_dividends": "box1a", "ordinary_dividends": "box1a", "qualified_dividends": "box1b",
                 "total_capital_gain_distributions": "box2a", "federal_income_tax_withheld": "box4"},
    "1099-R": {"gross_distribution": "box1", "taxable_amount": "box2a", "federal_income_tax_withheld": "box4",
               "distribution_code": "box7"},
    "SSA-1099": {"net_benefits": "box5", "benefits_paid": "box5"},
    "1099-G": {"unemployment_compensation": "box1"},
}
BOX = re.compile(r"^box_?(\d{1,2}[a-z]?)(?:_[a-z_]+)?$")


@dataclass
class Populated:
    inputs: dict[str, Any] = field(default_factory=dict)          # partial IndividualReturn
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)  # "w2s[0].wages" -> {document_id, box, value}
    issues: list[dict[str, str]] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    unreadable: list[dict[str, str]] = field(default_factory=list)   # {document_id, list, field, box, value}

    def cannot_read(self, doc_id: str, name: str, lst: str, fld: str, box: str, value: Any) -> None:
        self.unreadable.append({"document_id": doc_id, "list": lst, "field": fld, "box": box, "value": str(value)[:40]})
        self.issues.append({"document_id": doc_id, "code": "unreadable", "blocking": "true",
                            "message": f"{name}: {box} = {str(value)[:40]!r} could not be read; enter it from the document "
                                       "(it is never taken as zero)"})


def money(v: Any) -> Decimal | None:
    s = str(v).strip().replace("$", "").replace(",", "")
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def canonical(doc_type: str, name: str) -> str | None:
    key = re.sub(r"[^a-z0-9_]", "_", name.strip().lower()).strip("_")
    key = ALIASES.get(doc_type, {}).get(key, key)
    m = BOX.match(key)
    return f"box{m.group(1)}" if m else None


def owner_of(fields: dict[str, Any], facts: dict[str, Any]) -> str | None:
    """'taxpayer' or 'spouse' from the recipient's last-4 TIN or name; None when it cannot be told apart."""
    last4 = str(fields.get("recipient_tin_last4", "")).strip()[-4:]
    if last4:
        if last4 == str(facts.get("taxpayer_ssn_last4", "")):
            return "taxpayer"
        if last4 == str(facts.get("spouse_ssn_last4", "")):
            return "spouse"
    name = str(fields.get("recipient_name", "")).lower()
    sp = str(facts.get("spouse_name", "")).lower()
    tp = str(facts.get("taxpayer_name", "")).lower()
    if name and sp and sp.split()[0] in name and not (tp and tp.split()[0] in name):
        return "spouse"
    if name and tp and tp.split()[0] in name:
        return "taxpayer"
    return None


def populate(conn: sqlite3.Connection, client_id: str, tax_year: int, *, joint: bool,
             exclude: frozenset[str] | set[str] = frozenset()) -> Populated:
    """`exclude`: documents a person accounted for on this return (entered by hand, or not applicable); they are
    never put back on it."""
    facts = json.loads((conn.execute("SELECT facts FROM clients WHERE id = ?", (client_id,)).fetchone() or ["{}"])[0] or "{}")
    out = Populated()
    # Only live evidence: a document deleted under retention, moved to another client or re-dated is not this
    # return's source any more.
    rows = conn.execute("SELECT id, doc_type, fields, original_name FROM documents WHERE client_id = ? AND tax_year = ? "
                        "AND status = 'filed' AND deleted_at IS NULL ORDER BY received_at, id", (client_id, tax_year)).fetchall()
    for r in rows:
        doc_type, doc_id, fields = r[1], r[0], json.loads(r[2] or "{}")
        if doc_id in exclude:
            continue
        if doc_type == "1099-NEC":
            amt = money(fields.get("box1", fields.get("nonemployee_compensation", "")))
            out.issues.append({"document_id": doc_id, "code": "nec_needs_business",
                               "message": f"{r[3]}: nonemployee compensation of {amt} needs a Schedule C business; "
                                          "attach it to a business on the return."})
            continue
        if doc_type in ("1098", "1098-E"):
            for name in ("box1", "box5") if doc_type == "1098" else ("box1",):
                raw = fields.get(name, (fields.get("_unverified") or {}).get(name, ""))
                if str(raw).strip() and (money(raw) is None or name in (fields.get("_unverified") or {})):
                    key, fld = ("itemized", "mortgage_interest_1098" if name == "box1" else "mortgage_insurance_premiums") \
                        if doc_type == "1098" else ("adjustments", "student_loan_interest_paid")
                    out.unreadable.append({"document_id": doc_id, "list": key, "field": fld, "box": name, "value": str(raw)[:40],
                                           "summed": "true"})
        if doc_type == "1098":
            amt = money(fields.get("box1", ""))
            if amt is None and str(fields.get("box1", "")).strip():
                out.issues.append({"document_id": doc_id, "code": "unreadable", "blocking": "true",
                                   "message": f"{r[3]}: box1 = {str(fields['box1'])[:40]!r} could not be read; the 1098 is not "
                                              "on the return until a person enters it or accounts for it"})
            if amt is not None:
                _add_summed(out, "itemized", "mortgage_interest_1098", doc_id, "box1", amt)
                mip = money(fields.get("box5", ""))
                if mip and "box5" not in (fields.get("_unverified") or {}):
                    _add_summed(out, "itemized", "mortgage_insurance_premiums", doc_id, "box5", mip)
                out.documents.append(doc_id)
            continue
        if doc_type == "1098-E":
            amt = money(fields.get("box1", ""))
            if amt is not None and "box1" not in (fields.get("_unverified") or {}):
                _add_summed(out, "adjustments", "student_loan_interest_paid", doc_id, "box1", amt)
                out.documents.append(doc_id)
            continue
        boxes = BOXES.get(doc_type)
        if not boxes:
            continue
        named = owner_of(fields, facts)
        owner = named or ("taxpayer" if not joint else None)
        if owner is None:
            out.issues.append({"document_id": doc_id, "code": "owner_unknown",
                               "message": f"{r[3]}: cannot tell whether this belongs to the taxpayer or the spouse; "
                                          "it was attributed to the taxpayer. Confirm."})
            owner = "taxpayer"
        # The item carries its document: its identity survives reordering and hand edits, and leaves with the item.
        item: dict[str, Any] = {"owner": owner, "source_document": doc_id}
        lst = next(iter(boxes.values()))[0]
        idx = len(out.inputs.get(lst, []))
        if named:
            out.provenance[f"{lst}[{idx}].owner"] = {"document_id": doc_id, "box": "recipient", "value": named}
        unverified = fields.get("_unverified") or {}
        for name, value in unverified.items():               # dropped by intake: not confirmed against the text
            box = canonical(doc_type, name)
            if box in boxes:
                out.cannot_read(doc_id, r[3], lst, boxes[box][1], box, value)
        mapped = 0
        for name, value in fields.items():
            box = canonical(doc_type, name)
            if doc_type == "W-2" and name.lower().startswith("box12_"):
                code = name.split("_", 1)[1].upper()
                amt = money(value)
                if amt is None:
                    if str(value).strip():
                        out.cannot_read(doc_id, r[3], lst, f"box12.{code}", f"box12 {code}", value)
                    continue
                item.setdefault("box12", {})[code] = str(amt)
                out.provenance[f"{lst}[{idx}].box12.{code}"] = {"document_id": doc_id, "box": f"box12 {code}", "value": str(amt)}
                mapped += 1
                continue
            if doc_type == "W-2" and box == "box14b":
                if str(value).strip().isdigit():
                    item["tipped_occupation_code"] = int(str(value).strip())
                    out.provenance[f"{lst}[{idx}].tipped_occupation_code"] = {"document_id": doc_id, "box": "box14b",
                                                                              "value": str(value).strip()}
                elif str(value).strip():
                    out.cannot_read(doc_id, r[3], lst, "tipped_occupation_code", "box14b", value)
                continue
            if doc_type == "1099-R" and box == "box7":
                code = str(value).strip()
                # Box 7 codes are printed in capitals: a lower-case letter is a misreading ("l" for "1", "o" for "0"),
                # never taken as the code it resembles.
                if fx.valid_code(code) and code == code.upper():
                    item["distribution_code"] = code
                    out.provenance[f"{lst}[{idx}].distribution_code"] = {"document_id": doc_id, "box": "box7", "value": code}
                elif code:
                    out.cannot_read(doc_id, r[3], lst, "distribution_code", "box7", value)
                continue
            if box is None or box not in boxes:
                continue
            amt = money(value)
            if amt is None:
                if str(value).strip():
                    out.cannot_read(doc_id, r[3], lst, boxes[box][1], box, value)
                continue
            fld = boxes[box][1]
            item[fld] = str(amt)
            out.provenance[f"{lst}[{idx}].{fld}"] = {"document_id": doc_id, "box": box, "value": str(amt)}
            mapped += 1
        if doc_type in NAME_FIELDS and fields.get(NAME_FIELDS[doc_type][0]):
            item[NAME_FIELDS[doc_type][1]] = str(fields[NAME_FIELDS[doc_type][0]])
        if doc_type == "1099-R" and str(fields.get("ira_sep_simple", "")).strip():
            item["ira_sep_simple"] = str(fields["ira_sep_simple"]).strip().lower() in ("x", "true", "yes", "1", "checked")
            out.provenance[f"{lst}[{idx}].ira_sep_simple"] = {"document_id": doc_id, "box": "IRA/SEP/SIMPLE",
                                                              "value": str(item["ira_sep_simple"])}
        # Even with no readable amount the item goes on the return: its required amount is then missing, which
        # blocks review until a person enters it (a filed W-2 never silently drops off the return).
        out.inputs.setdefault(lst, []).append(item)
        out.documents.append(doc_id)
        if not mapped:
            out.issues.append({"document_id": doc_id, "code": "no_amounts", "message": f"{r[3]}: no box amounts could be read; "
                                                                                       "enter them from the document."})
    return out


def _add_summed(out: Populated, key: str, field: str, doc_id: str, box: str, amount: Decimal) -> None:
    """An amount summed over several documents (every 1098, every 1098-E) names each one it came from."""
    target = out.inputs.setdefault(key, {})
    total = Decimal(target.get(field, "0")) + amount
    target[field] = str(total)
    path = f"{key}.{field}"
    sources = (out.provenance.get(path) or {}).get("documents", []) + [{"document_id": doc_id, "box": box, "value": str(amount)}]
    out.provenance[path] = {"document_id": doc_id, "box": box, "value": str(total), "documents": sources}
