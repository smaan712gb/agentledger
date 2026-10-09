"""Build return inputs from the client's filed tax documents (no re-keying).

Each mapped amount carries provenance: which document and which box it came from. Values
come only from fields intake already grounded against the document text. Anything that
cannot be placed with certainty (an unknown owner, a 1099-NEC with no business to attach it
to) is reported for the preparer and never guessed. A box the document carries but that could
not be read (not a number, not a valid code) is listed in `unreadable`: the item goes on the
return without it, and it blocks review as a missing amount until a person enters it
(facts.missing_required); it is never dropped or taken as zero.

The prior-year return (a filed Form 1040 of the year before) is the document behind the
`prior_year` group: its lines are placed one by one with provenance, like the amounts of a 1098
but never summed, and two prior-year returns that disagree leave the line for a person to enter.
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
PRIOR_YEAR_RETURN = "Prior-year return"
MONTHS = [f"{m:02d}" for m in range(1, 13)]

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
    "1099-SA": {"box1": ("hsa_distributions", "gross_distribution"), "box2": ("hsa_distributions", "earnings_on_excess"),
                "box4": ("hsa_distributions", "fmv_on_date_of_death")},
    "5498": {"box1": ("ira_accounts", "ira_contributions"), "box2": ("ira_accounts", "rollover_contributions"),
             "box3": ("ira_accounts", "roth_conversion"), "box4": ("ira_accounts", "recharacterized_contributions"),
             "box5": ("ira_accounts", "fmv"), "box8": ("ira_accounts", "sep_contributions"),
             "box9": ("ira_accounts", "simple_contributions"), "box10": ("ira_accounts", "roth_contributions")},
    "5498-SA": {"box1": ("hsa_contributions", "archer_msa_contributions"), "box2": ("hsa_contributions", "total_contributions"),
                "box3": ("hsa_contributions", "following_year_contributions"),
                "box4": ("hsa_contributions", "rollover_contributions"), "box5": ("hsa_contributions", "fmv")},
    # Form 1095-A Part III: one key per month and column, plus the line 33 annual totals.
    "1095-A": {**{f"{col}_{m}": ("marketplace_coverage", f"{col}_{m}") for col in ("premium", "slcsp", "aptc") for m in MONTHS},
               "annual_premium": ("marketplace_coverage", "annual_premium"), "annual_slcsp": ("marketplace_coverage", "annual_slcsp"),
               "annual_aptc": ("marketplace_coverage", "annual_aptc")},
}
# Lines of a filed Form 1040 (and its worksheets) that feed the `prior_year` group of the following year's return.
PRIOR_YEAR_LINES: dict[str, str] = {"agi": "agi", "tax": "tax", "filing_status": "filing_status",
                                    "capital_loss_carryover_short": "capital_loss_carryover_short",
                                    "capital_loss_carryover_long": "capital_loss_carryover_long",
                                    "traditional_ira_basis": "traditional_ira_basis", "roth_ira_basis": "roth_ira_basis"}
NAME_FIELDS = {"W-2": ("employer_name", "employer_name"), "1099-INT": ("payer_name", "payer"), "1099-DIV": ("payer_name", "payer"),
               "1099-R": ("payer_name", "payer"), "1099-SA": ("payer_name", "trustee"), "5498": ("payer_name", "trustee"),
               "5498-SA": ("payer_name", "trustee"), "1095-A": ("issuer_name", "issuer")}
# Text attributes copied as printed (no amount, no code), by document type: extraction key -> input field.
TEXT_FIELDS = {"1095-A": {"policy_number": "policy_number", "marketplace": "marketplace", "marketplace_identifier": "marketplace"}}
# Boxes that name a foreign country or U.S. territory (text, with provenance): 1099-INT box 7, 1099-DIV box 8 (Form 1116 Part I line g).
COUNTRY_BOXES: dict[tuple[str, str], str] = {("1099-INT", "box7"): "foreign_country", ("1099-DIV", "box8"): "foreign_country"}
# Checkbox boxes: the label that is checked, read into a typed field; anything else is unreadable, never a default.
CHECKBOX_FIELDS: dict[tuple[str, str], tuple[str, dict[str, str]]] = {
    ("1099-SA", "box5"): ("account_type", {"hsa": "hsa", "archer": "archer_msa", "ma msa": "ma_msa", "ma_msa": "ma_msa",
                                           "medicare advantage": "ma_msa"}),
    ("5498-SA", "box6"): ("account_type", {"hsa": "hsa", "archer": "archer_msa", "ma msa": "ma_msa", "ma_msa": "ma_msa",
                                           "medicare advantage": "ma_msa"}),
    ("5498", "box7"): ("account_type", {"roth": "roth", "sep": "sep", "simple": "simple", "ira": "ira"}),
}
FILING_STATUSES = {"single": "single", "married filing jointly": "mfj", "mfj": "mfj", "married filing separately": "mfs",
                   "mfs": "mfs", "head of household": "hoh", "hoh": "hoh", "qualifying surviving spouse": "qss", "qss": "qss",
                   "qualifying widow(er)": "qss"}
# Loose names a model might still use, mapped to the canonical box.
ALIASES = {
    "W-2": {"wages": "box1", "wages_tips_other_compensation": "box1", "federal_income_tax_withheld": "box2",
            "social_security_wages": "box3", "social_security_tax_withheld": "box4", "medicare_wages_and_tips": "box5",
            "medicare_tax_withheld": "box6"},
    "1099-INT": {"interest_income": "box1", "federal_income_tax_withheld": "box4", "foreign_tax_paid": "box6",
                 "foreign_country": "box7", "foreign_country_or_us_territory": "box7", "tax_exempt_interest": "box8"},
    "1099-DIV": {"total_ordinary_dividends": "box1a", "ordinary_dividends": "box1a", "qualified_dividends": "box1b",
                 "total_capital_gain_distributions": "box2a", "federal_income_tax_withheld": "box4", "foreign_tax_paid": "box7",
                 "foreign_country": "box8", "foreign_country_or_us_territory": "box8"},
    "1099-R": {"gross_distribution": "box1", "taxable_amount": "box2a", "federal_income_tax_withheld": "box4",
               "distribution_code": "box7"},
    "SSA-1099": {"net_benefits": "box5", "benefits_paid": "box5"},
    "1099-G": {"unemployment_compensation": "box1"},
    "1099-SA": {"gross_distribution": "box1", "earnings_on_excess_contributions": "box2", "distribution_code": "box3",
                "fmv_on_date_of_death": "box4", "account_type": "box5"},
    "5498": {"ira_contributions": "box1", "rollover_contributions": "box2", "roth_ira_conversion_amount": "box3",
             "recharacterized_contributions": "box4", "fair_market_value_of_account": "box5", "fmv_of_account": "box5",
             "sep_contributions": "box8", "simple_contributions": "box9", "roth_ira_contributions": "box10",
             "account_type": "box7"},
    "5498-SA": {"total_contributions_made_in_the_year": "box2", "total_contributions": "box2",
                "rollover_contributions": "box4", "fair_market_value_of_hsa": "box5", "fmv": "box5", "account_type": "box6"},
    "1095-A": {"annual_total_a": "annual_premium", "annual_total_b": "annual_slcsp", "annual_total_c": "annual_aptc",
               "annual_enrollment_premiums": "annual_premium", "annual_slcsp_premium": "annual_slcsp",
               "annual_advance_payment_of_ptc": "annual_aptc", "annual_advance_payment": "annual_aptc"},
    PRIOR_YEAR_RETURN: {"line11": "agi", "line_11": "agi", "line11a": "agi", "adjusted_gross_income": "agi",
                        "line24": "tax", "line_24": "tax", "total_tax": "tax",
                        "short_term_capital_loss_carryover": "capital_loss_carryover_short",
                        "capital_loss_carryover_worksheet_line8": "capital_loss_carryover_short",
                        "long_term_capital_loss_carryover": "capital_loss_carryover_long",
                        "capital_loss_carryover_worksheet_line13": "capital_loss_carryover_long",
                        "form_8606_line14": "traditional_ira_basis", "ira_basis": "traditional_ira_basis",
                        "basis_in_traditional_iras": "traditional_ira_basis", "roth_basis": "roth_ira_basis"},
}
BOX = re.compile(r"^box_?(\d{1,2}[a-z]?)(?:_[a-z_]+)?$")
MONTH_COLUMN = re.compile(r"^(premium|slcsp|aptc)_?(0?[1-9]|1[0-2])$")                 # premium_01, slcsp_7
PART_III_LINE = re.compile(r"^line_?(2[1-9]|3[0-3])_?(?:col(?:umn)?_?)?([abc])$")      # line21_a .. line33_c
COLUMNS = {"a": "premium", "b": "slcsp", "c": "aptc"}


@dataclass
class Populated:
    inputs: dict[str, Any] = field(default_factory=dict)          # partial IndividualReturn
    provenance: dict[str, dict[str, Any]] = field(default_factory=dict)  # "w2s[0].wages" -> {document_id, box, value}
    issues: list[dict[str, str]] = field(default_factory=list)
    documents: list[str] = field(default_factory=list)
    unreadable: list[dict[str, str]] = field(default_factory=list)   # {document_id, list, field, box, value}

    def cannot_read(self, doc_id: str, name: str, lst: str, fld: str, box: str, value: Any, *, group: bool = False) -> None:
        entry = {"document_id": doc_id, "list": lst, "field": fld, "box": box, "value": str(value)[:40]}
        if group:                                                    # a single-valued group amount (the prior-year return)
            entry["group"] = "true"
        self.unreadable.append(entry)
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
    if doc_type == "1095-A":
        m = MONTH_COLUMN.match(key)
        if m:
            return f"{m.group(1)}_{int(m.group(2)):02d}"
        m = PART_III_LINE.match(key)
        if m:
            line, col = int(m.group(1)), COLUMNS[m.group(2)]
            return f"annual_{col}" if line == 33 else f"{col}_{line - 20:02d}"
        return key if key in BOXES["1095-A"] else None
    if doc_type == PRIOR_YEAR_RETURN:
        return key if key in PRIOR_YEAR_LINES else None
    m = BOX.match(key)
    return f"box{m.group(1)}" if m else None


def checkbox(value: Any, labels: dict[str, str]) -> str | None:
    """The typed value of a checked label (longest label first, so "MA MSA" is not read as "HSA")."""
    text = re.sub(r"\s+", " ", str(value).strip().lower())
    for label in sorted(labels, key=len, reverse=True):
        if label in text:
            return labels[label]
    return None


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
    # return's source any more. The prior-year return is the one filed document of the year before.
    rows = conn.execute("SELECT id, doc_type, fields, original_name, tax_year FROM documents WHERE client_id = ? "
                        "AND (tax_year = ? OR (tax_year = ? AND doc_type = ?)) AND status = 'filed' AND deleted_at IS NULL "
                        "ORDER BY received_at, id", (client_id, tax_year, tax_year - 1, PRIOR_YEAR_RETURN)).fetchall()
    for r in rows:
        doc_type, doc_id, fields = r[1], r[0], json.loads(r[2] or "{}")
        if doc_id in exclude:
            continue
        if doc_type == PRIOR_YEAR_RETURN:
            if r[4] == tax_year - 1:                       # a filed return of this year is not its own prior-year return
                _prior_year(out, doc_id, r[3], fields)
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
            if (doc_type == "1099-R" and box == "box7") or (doc_type == "1099-SA" and box == "box3"):
                code = str(value).strip()
                # Codes are printed in capitals: a lower-case letter is a misreading ("l" for "1", "o" for "0"),
                # never taken as the code it resembles.
                if fx.valid_code(code, lst) and code == code.upper():
                    item["distribution_code"] = code
                    out.provenance[f"{lst}[{idx}].distribution_code"] = {"document_id": doc_id, "box": box, "value": code}
                    mapped += 1
                elif code:
                    out.cannot_read(doc_id, r[3], lst, "distribution_code", box, value)
                continue
            if box is not None and (doc_type, box) in COUNTRY_BOXES:
                country = str(value).strip()
                if country:
                    item[COUNTRY_BOXES[(doc_type, box)]] = country
                    out.provenance[f"{lst}[{idx}].{COUNTRY_BOXES[(doc_type, box)]}"] = {"document_id": doc_id, "box": box, "value": country}
                continue
            if box is not None and (doc_type, box) in CHECKBOX_FIELDS:
                fld, labels = CHECKBOX_FIELDS[(doc_type, box)]
                typed = checkbox(value, labels)
                if typed is not None:
                    item[fld] = typed
                    out.provenance[f"{lst}[{idx}].{fld}"] = {"document_id": doc_id, "box": box, "value": typed}
                elif str(value).strip():
                    out.cannot_read(doc_id, r[3], lst, fld, box, value)
                continue
            if doc_type == "5498" and box == "box11":
                flag = str(value).strip().lower()
                if flag in ("x", "true", "yes", "1", "checked"):
                    item["rmd_required_next_year"] = True
                    out.provenance[f"{lst}[{idx}].rmd_required_next_year"] = {"document_id": doc_id, "box": box, "value": "true"}
                elif flag:
                    out.cannot_read(doc_id, r[3], lst, "rmd_required_next_year", box, value)
                continue
            if doc_type == "1095-A":
                key = re.sub(r"[^a-z0-9_]", "_", name.strip().lower()).strip("_")
                if key in TEXT_FIELDS["1095-A"] and str(value).strip():
                    item[TEXT_FIELDS["1095-A"][key]] = str(value).strip()
                    continue
                if key == "covered_individuals" and str(value).strip():
                    item["covered_individuals"] = [x.strip() for x in re.split(r"[;,\n]", str(value)) if x.strip()]
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


def _prior_year(out: Populated, doc_id: str, name: str, fields: dict[str, Any]) -> None:
    """The lines of the prior-year return go into the `prior_year` group, each with its provenance. A line that could
    not be read, or that a second prior-year return states differently, is left for a person to enter."""
    target = out.inputs.setdefault("prior_year", {})
    for raw, value in (fields.get("_unverified") or {}).items():
        fld = canonical(PRIOR_YEAR_RETURN, raw)
        if fld:
            out.cannot_read(doc_id, name, "prior_year", fld, raw, value, group=True)
    for raw, value in fields.items():
        if raw == "_unverified":
            continue
        fld = canonical(PRIOR_YEAR_RETURN, raw)
        if fld is None or not str(value).strip():
            continue
        if fld == "filing_status":
            v = FILING_STATUSES.get(re.sub(r"\s+", " ", str(value).strip().lower()))
            if v is None:
                out.cannot_read(doc_id, name, "prior_year", fld, raw, value, group=True)
                continue
        else:
            amt = money(value)
            if amt is None:
                out.cannot_read(doc_id, name, "prior_year", fld, raw, value, group=True)
                continue
            v = str(amt)
        path = f"prior_year.{fld}"
        if fld in target and not fx.same(target[fld], v):
            other = out.provenance[path]["document_id"]
            out.unreadable.append({"document_id": doc_id, "list": "prior_year", "field": fld, "box": raw, "value": v, "group": "true"})
            out.issues.append({"document_id": doc_id, "code": "prior_year_disagrees", "blocking": "true",
                               "message": f"{name}: {raw} = {v} but prior-year return {other} says {target[fld]}; a person "
                                          "enters the amount from the return as filed (it is never taken as zero)"})
            continue
        target[fld] = v
        out.provenance[path] = {"document_id": doc_id, "box": raw, "value": v}
    out.documents.append(doc_id)


def _add_summed(out: Populated, key: str, field: str, doc_id: str, box: str, amount: Decimal) -> None:
    """An amount summed over several documents (every 1098, every 1098-E) names each one it came from."""
    target = out.inputs.setdefault(key, {})
    total = Decimal(target.get(field, "0")) + amount
    target[field] = str(total)
    path = f"{key}.{field}"
    sources = (out.provenance.get(path) or {}).get("documents", []) + [{"document_id": doc_id, "box": box, "value": str(amount)}]
    out.provenance[path] = {"document_id": doc_id, "box": box, "value": str(total), "documents": sources}
