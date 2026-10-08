"""Build return inputs from the client's filed tax documents (no re-keying).

Each mapped amount carries provenance: which document and which box it came from. Values
come only from fields intake already grounded against the document text. Anything that
cannot be placed with certainty (an unknown owner, a 1099-NEC with no business to attach it
to, a field that is not a number) is reported for the preparer and never guessed.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

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


def populate(conn: sqlite3.Connection, client_id: str, tax_year: int, *, joint: bool) -> Populated:
    facts = json.loads((conn.execute("SELECT facts FROM clients WHERE id = ?", (client_id,)).fetchone() or ["{}"])[0] or "{}")
    out = Populated()
    rows = conn.execute("SELECT id, doc_type, fields, original_name FROM documents WHERE client_id = ? AND tax_year = ? "
                        "AND status = 'filed' ORDER BY received_at", (client_id, tax_year)).fetchall()
    for r in rows:
        doc_type, doc_id, fields = r[1], r[0], json.loads(r[2] or "{}")
        if doc_type == "1099-NEC":
            amt = money(fields.get("box1", fields.get("nonemployee_compensation", "")))
            out.issues.append({"document_id": doc_id, "code": "nec_needs_business",
                               "message": f"{r[3]}: nonemployee compensation of {amt} needs a Schedule C business; "
                                          "attach it to a business on the return."})
            continue
        if doc_type == "1098":
            amt = money(fields.get("box1", ""))
            if amt is not None:
                it = out.inputs.setdefault("itemized", {})
                it["mortgage_interest_1098"] = str(Decimal(it.get("mortgage_interest_1098", "0")) + amt)
                out.provenance["itemized.mortgage_interest_1098"] = {"document_id": doc_id, "box": "box1", "value": str(amt)}
                mip = money(fields.get("box5", ""))
                if mip:
                    it["mortgage_insurance_premiums"] = str(Decimal(it.get("mortgage_insurance_premiums", "0")) + mip)
                    out.provenance["itemized.mortgage_insurance_premiums"] = {"document_id": doc_id, "box": "box5", "value": str(mip)}
                out.documents.append(doc_id)
            continue
        if doc_type == "1098-E":
            amt = money(fields.get("box1", ""))
            if amt is not None:
                adj = out.inputs.setdefault("adjustments", {})
                adj["student_loan_interest_paid"] = str(Decimal(adj.get("student_loan_interest_paid", "0")) + amt)
                out.provenance["adjustments.student_loan_interest_paid"] = {"document_id": doc_id, "box": "box1", "value": str(amt)}
                out.documents.append(doc_id)
            continue
        boxes = BOXES.get(doc_type)
        if not boxes:
            continue
        owner = owner_of(fields, facts) or ("taxpayer" if not joint else None)
        if owner is None:
            out.issues.append({"document_id": doc_id, "code": "owner_unknown",
                               "message": f"{r[3]}: cannot tell whether this belongs to the taxpayer or the spouse; "
                                          "it was attributed to the taxpayer. Confirm."})
            owner = "taxpayer"
        item: dict[str, Any] = {"owner": owner}
        lst = next(iter(boxes.values()))[0]
        idx = len(out.inputs.get(lst, []))
        mapped = 0
        for name, value in fields.items():
            box = canonical(doc_type, name)
            if doc_type == "W-2" and name.lower().startswith("box12_"):
                code = name.split("_", 1)[1].upper()
                amt = money(value)
                if amt is not None:
                    item.setdefault("box12", {})[code] = str(amt)
                    out.provenance[f"{lst}[{idx}].box12.{code}"] = {"document_id": doc_id, "box": f"box12 {code}", "value": str(amt)}
                    mapped += 1
                continue
            if doc_type == "W-2" and box == "box14b":
                if str(value).strip().isdigit():
                    item["tipped_occupation_code"] = int(str(value).strip())
                continue
            if doc_type == "1099-R" and box == "box7":
                item["distribution_code"] = str(value).strip().upper()
                continue
            if box is None or box not in boxes:
                continue
            amt = money(value)
            if amt is None:
                out.issues.append({"document_id": doc_id, "code": "not_a_number", "message": f"{r[3]}: {name} = {value!r} is not an amount."})
                continue
            fld = boxes[box][1]
            item[fld] = str(amt)
            out.provenance[f"{lst}[{idx}].{fld}"] = {"document_id": doc_id, "box": box, "value": str(amt)}
            mapped += 1
        if doc_type in NAME_FIELDS and fields.get(NAME_FIELDS[doc_type][0]):
            item[NAME_FIELDS[doc_type][1]] = str(fields[NAME_FIELDS[doc_type][0]])
        if doc_type == "1099-R":
            item["ira_sep_simple"] = str(fields.get("ira_sep_simple", "")).strip().lower() in ("x", "true", "yes", "1", "checked")
        if mapped:
            out.inputs.setdefault(lst, []).append(item)
            out.documents.append(doc_id)
        else:
            out.issues.append({"document_id": doc_id, "code": "no_amounts", "message": f"{r[3]}: no box amounts could be read."})
    return out
