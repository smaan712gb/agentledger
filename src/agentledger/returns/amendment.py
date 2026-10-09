"""Form 1040-X, the amended individual return, computed from two returns.

Column A is the return as originally filed: the sealed version of the original return that its approval pinned
(workflow facts approved_version and approved_hash, the hash the taxpayer's signature and the filing were bound to),
read by Returns._original_as_filed and never recomputed, so a rule change after filing cannot move it. Column C is the
amended return, computed from this return's inputs by returns/individual.py like any other return. Column B is C less
A, the net change. When the IRS changed the original (a math error notice, an examination), the stated adjusted amounts
(Amendment.as_previously_adjusted) replace the as-filed figures in column A, line by line.

Lines are stored by Form 1040 line, not by 1040-X line: the sheet `f1040x` has one key "<line>.<column>" for every Form
1040 line the 1040-X carries ("11a.A", "11a.B", "11a.C", ...; LINES) and one named key for each line the 1040-X alone
has (paid_with_extension, paid_with_original, paid_after_filing, total_payments, original_overpayment, net_payments,
amount_owed, overpayment, refund, applied_to_estimated_tax). The 2026 Form 1040-X line map (line 1 adjusted gross income
... line 23 applied to estimated tax) is applied by the renderer (T1-07) once the 2026 draft posts; nothing here depends
on it. Part I (dependents), the filing status and Part III (the explanation) are facts of the sheet.

Dates: IRC §6072(a) and §6081 (the due date and its extension), §7503 and Reg. §301.7503-1(b) (a due date on a Saturday,
Sunday or District of Columbia legal holiday moves to the next business day), §6513(a) and (b) (an early return, and
withholding and estimated tax, are deemed filed and paid on the due date), §6511(a) (a refund claim is timely within 3
years of filing or 2 years of payment, whichever is later), §6013(b) and Reg. §1.6013-1(a)(1) (a joint return cannot be
changed to separate returns after the due date).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from ..evidence.records import add_years
from .individual import Result
from .model import Amendment
from .sheet import pos, whole

FORM = "f1040x"
Z = Decimal(0)
EXPLANATION_MIN = 10          # characters: Part III, like every reason a person records on a return
EXPLANATION_CODE = "amendment_explanation_missing"

# Form 1040 lines carried in columns A, B and C, in the order the 1040-X takes them: what Form 1040-X (2025) asks for on
# lines 1-15, by the 2026 Form 1040 draft's keys. Lines 34-37 of Form 1040 (the original's overpayment, refund, amount
# applied and amount owed) are not columns: they feed line 18 and the checks below (facts original_overpayment, ...).
LINES: tuple[tuple[str, str], ...] = (
    ("11a", "Adjusted gross income"),
    ("12e", "Standard deduction or itemized deductions"),
    ("12f", "Charitable deduction for non-itemizers"),
    ("13a", "Schedule 1-A deductions"),
    ("13b", "Qualified business income deduction"),
    ("14", "Total deductions"),
    ("15", "Taxable income"),
    ("16", "Tax"),
    ("17", "Schedule 2, line 3"),
    ("18", "Tax plus Schedule 2, line 3"),
    ("19", "Child tax credit or credit for other dependents"),
    ("20", "Schedule 3, line 8"),
    ("21", "Total nonrefundable credits"),
    ("22", "Tax after nonrefundable credits"),
    ("23", "Other taxes (Schedule 2, line 21)"),
    ("24c", "Total tax"),
    ("25d", "Federal income tax withheld"),
    ("26", "Estimated tax payments and prior-year overpayment applied"),
    ("27a", "Earned income credit"),
    ("28", "Additional child tax credit"),
    ("29", "American opportunity credit (refundable part)"),
    ("31", "Schedule 3, line 15 (other payments and refundable credits)"),
    ("32a", "Total other payments and refundable credits"),
    ("32b", "Schedule 3-A reduction"),
    ("32c", "Other payments and refundable credits after Schedule 3-A"),
    ("33", "Total payments"),
)
MEMO_LINES = ("34", "35a", "36", "37")      # the original's bottom line, usable in as_previously_adjusted and in facts
ADJUSTABLE = frozenset(line for line, _ in LINES) | frozenset(MEMO_LINES)
SEPARATE = ("mfs", "single", "hoh")         # what a joint return cannot be changed to after the due date (§6013(b))


def _dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return Z


def filing_due_date(tax_year: int, *, extended: bool = False) -> date:
    """The day a calendar-year Form 1040 is due: April 15 of the next year (IRC §6072(a)), October 15 with an extension
    (§6081), moved past a Saturday, Sunday or legal holiday in the District of Columbia (§7503; Reg. §301.7503-1(b)).
    Emancipation Day, April 16, is such a holiday, observed on the Friday or Monday when it falls on a weekend (D.C. Code
    §1-612.02): the 2022 and 2023 due dates were April 18 for that reason. Columbus Day, the second Monday of October,
    never falls on the 15th."""
    d = date(tax_year + 1, 10, 15) if extended else date(tax_year + 1, 4, 15)
    holidays: set[date] = set()
    if not extended:
        e = date(tax_year + 1, 4, 16)
        if e.weekday() == 5:
            e -= timedelta(days=1)
        elif e.weekday() == 6:
            e += timedelta(days=1)
        holidays.add(e)
    while d.weekday() >= 5 or d in holidays:
        d += timedelta(days=1)
    return d


def routing_number_valid(routing: str) -> bool:
    """An ABA routing transit number: nine digits whose weighted sum (3, 7, 1) is a multiple of 10."""
    if len(routing) != 9 or not routing.isdigit():
        return False
    digits = [int(ch) for ch in routing]
    return sum(d * w for d, w in zip(digits, (3, 7, 1, 3, 7, 1, 3, 7, 1))) % 10 == 0


@dataclass
class Original:
    """The amended return as filed: the forms and facts of the pinned, hash-verified version Returns._original_as_filed
    read, the date it was filed and how, or the reasons it cannot be used (problems: (code, message), each a blocking
    diagnostic on the 1040-X). A snapshot without problems and with forms is usable; column A is never taken from
    anything else."""
    return_id: str | None = None
    version: int | None = None
    status: str | None = None               # accepted, or paper_filed
    filed_on: date | None = None            # the electronic postmark (the transmission) or the paper filing as recorded
    package_hash: str | None = None         # the approved hash the version reproduces
    filing_status: str | None = None
    forms: dict[str, dict[str, Decimal]] = field(default_factory=dict)
    facts: dict[str, Any] = field(default_factory=dict)
    problems: list[tuple[str, str]] = field(default_factory=list)

    @classmethod
    def from_result(cls, result: Mapping[str, Any], **kw: Any) -> Original:
        """From a computed result as stored (Result.to_dict(), whole-dollar strings)."""
        forms = {f: {k: _dec(v) for k, v in lines.items()} for f, lines in (result.get("forms") or {}).items()}
        return cls(forms=forms, facts=dict(result.get("facts") or {}), filing_status=result.get("filing_status"), **kw)

    @property
    def usable(self) -> bool:
        return not self.problems and bool(self.forms)

    def line(self, form: str, line: str) -> Decimal:
        return self.forms.get(form, {}).get(line, Z)


def compute_1040x(original: Original, corrected: Result, amendment: Amendment | None, *, today: date) -> None:
    """Add the `f1040x` sheet to the corrected return: columns A, B and C per Form 1040 line, the 1040-X's own lines,
    Part I and Part III facts, and the diagnostics (every scope limit and missing fact is an error with a stable code;
    the statute and consistency checks are warnings). `today` decides the superseding and §6511 questions."""
    s = corrected.sheets
    s.forms.setdefault(FORM, {})                 # the sheet exists (coverage names it) even when nothing can be computed
    a = amendment or Amendment()
    y = corrected.tax_year
    s.fact(FORM, "tax_year", y)
    s.fact(FORM, "amends", original.return_id)
    s.fact(FORM, "original_version", original.version)
    s.fact(FORM, "original_status", original.status)
    s.fact(FORM, "original_package_hash", original.package_hash)
    for code, message in original.problems:
        s.diag("error", code, message, FORM)
    # Part III: the explanation of changes, a review blocker until stated (returns/store.py names it).
    explanation = a.explanation.strip()
    if len(explanation) < EXPLANATION_MIN:
        s.diag("error", EXPLANATION_CODE, "Form 1040-X Part III: explain the changes (amendment.explanation, at least "
                                          f"{EXPLANATION_MIN} characters: what changed, why, and the documents that show it).", FORM, "part_iii")
    else:
        s.fact(FORM, "explanation", explanation)

    # Dates: the due date (§7503), the filing date on record, superseding or amending.
    due, due_ext = filing_due_date(y), filing_due_date(y, extended=True)
    deadline = due_ext if a.extension_filed else due
    filed_on = a.original_filed_on or original.filed_on
    s.fact(FORM, "as_of", today.isoformat())
    s.fact(FORM, "due_date", due.isoformat())
    s.fact(FORM, "deadline_for_superseding", deadline.isoformat())
    s.fact(FORM, "original_filed_on", filed_on.isoformat() if filed_on else None)
    if a.superseding is None:
        superseding = today <= deadline
    else:
        superseding = a.superseding
        if superseding and today > deadline:
            s.diag("error", "amendment_superseding_after_due_date",
                   f"A superseding return is filed by the due date including extensions, {deadline.isoformat()}"
                   f"{' (Form 4868 filed)' if a.extension_filed else ''}; today is {today.isoformat()}: this is an amended return "
                   "(amendment.superseding).", FORM)
        elif not superseding and today <= deadline:
            s.diag("warning", "amendment_superseding_possible",
                   f"Filed by {deadline.isoformat()} this return supersedes the original instead of amending it; "
                   "amendment.superseding says it does not.", FORM)
    s.fact(FORM, "superseding", superseding)

    # The filing status, and Part I (dependents): as amended, with the original for the change.
    fs_orig = original.filing_status
    s.fact(FORM, "filing_status", corrected.filing_status)
    s.fact(FORM, "original_filing_status", fs_orig)
    fs_changed = bool(fs_orig) and fs_orig != corrected.filing_status
    s.fact(FORM, "filing_status_changed", fs_changed)
    if fs_orig == "mfj" and corrected.filing_status in SEPARATE and not superseding:
        s.diag("error", "amendment_joint_to_separate",
               "A joint return cannot be changed to a separate return after the due date (IRC §6013(b); Reg. §1.6013-1(a)(1)); "
               f"the original was filed jointly and {deadline.isoformat()} has passed.", FORM)
    deps_now = list((s.facts.get("f1040") or {}).get("dependents") or [])
    deps_orig = list((original.facts.get("f1040") or {}).get("dependents") or [])
    s.fact(FORM, "dependents", deps_now)
    s.fact(FORM, "original_dependents", deps_orig)
    s.fact(FORM, "dependents_changed", deps_now != deps_orig)

    # As previously adjusted by the IRS: stated lines replace the as-filed figures in column A, with the reason on record.
    adjusted: dict[str, Decimal] = {}
    if a.as_previously_adjusted is not None:
        reason = a.as_previously_adjusted.reason.strip()
        if len(reason) < EXPLANATION_MIN:
            s.diag("error", "amendment_adjusted_reason_missing",
                   "Say what the IRS changed and the notice or transcript it comes from (amendment.as_previously_adjusted.reason).", FORM)
        for key, value in a.as_previously_adjusted.lines.items():
            if key not in ADJUSTABLE:
                s.diag("error", "amendment_adjusted_line_unknown",
                       f"as_previously_adjusted.lines.{key} is not a Form 1040 line column A carries "
                       f"({', '.join(line for line, _ in LINES)}, {', '.join(MEMO_LINES)}).", FORM)
            else:
                adjusted[key] = whole(value)
        s.fact(FORM, "as_previously_adjusted", {"reason": reason, "lines": {k: str(v) for k, v in sorted(adjusted.items())}})

    def col_a(line: str) -> Decimal:
        return adjusted[line] if line in adjusted else original.line("f1040", line)

    # Columns A, B and C by Form 1040 line.
    pinned = original.usable
    s.fact(FORM, "lines", [line for line, _ in LINES])
    changed = False
    for line, label in LINES:
        c = s.set(FORM, f"{line}.C", corrected.line("f1040", line), f"{label}: correct amount (Form 1040 line {line} as amended)")
        if not pinned:
            continue
        a_val = s.set(FORM, f"{line}.A", col_a(line),
                      f"{label}: " + (f"as adjusted by the IRS ({a.as_previously_adjusted.reason.strip()})" if line in adjusted
                                      and a.as_previously_adjusted is not None else
                                      f"as originally filed (return {original.return_id}, version {original.version})"))
        b = s.set(FORM, f"{line}.B", c - a_val, f"{label}: net change")
        changed = changed or b != 0

    # The 1040-X's own lines (single column; the 2025 form's lines 16-23). Without the original as filed there is no line 18
    # and so no bottom line: the figures stay unset rather than showing the corrected return's own refund as the 1040-X's.
    paid_after = whole(a.paid_after_filing)
    overpaid = refund = Z
    if pinned:
        s.set(FORM, "paid_with_extension", corrected.line("sch_3", "10"),
              "Amount paid with the request for extension (Schedule 3, line 10): part of line 16, not again in line 15")
        owed_orig = col_a("37")
        if a.paid_with_original_return is None:
            paid_orig = Z
            if owed_orig > 0:
                s.diag("error", "amendment_paid_with_return_unknown",
                       f"The original return showed {owed_orig} owed (Form 1040 line 37): state the tax paid with it "
                       "(amendment.paid_with_original_return; 0 if it was not paid). A missing amount is never taken as zero.", FORM)
        else:
            paid_orig = whole(a.paid_with_original_return)
            if paid_orig > owed_orig:
                s.diag("warning", "amendment_paid_with_return_exceeds_owed",
                       f"{paid_orig} is stated as paid with the original return, which showed {owed_orig} owed (line 37); "
                       "interest and penalties are not tax paid with the return.", FORM)
        s.set(FORM, "paid_with_original", paid_orig, "Tax paid with the original return (line 16)")
        s.set(FORM, "paid_after_filing", paid_after, "Additional tax paid after the original return was filed (line 16)")
        total = s.set(FORM, "total_payments", corrected.line("f1040", "33") + paid_orig + paid_after,
                      "Total payments: lines 12 through 15, column C (Form 1040 line 33 as amended, the extension payment included once), "
                      "and line 16")
        over_orig = s.set(FORM, "original_overpayment", col_a("34"),
                          "Overpayment on the original return (Form 1040 line 34) or as previously adjusted by the IRS: the refund and "
                          "the amount applied to estimated tax")
        net = s.set(FORM, "net_payments", total - over_orig, "Line 17 less line 18 (negative when the original's overpayment exceeds the payments)")
        tax_c = corrected.line("f1040", "24c")
        s.set(FORM, "amount_owed", pos(tax_c - net), "Amount you owe: total tax (column C) over line 19")
        overpaid = s.set(FORM, "overpayment", pos(net - tax_c), "Amount overpaid on this return: line 19 over total tax (column C)")
        applied = s.set(FORM, "applied_to_estimated_tax", min(whole(a.apply_to_estimated_tax), overpaid),
                        f"Part of line 21 applied to {y + 1} estimated tax")
        refund = s.set(FORM, "refund", overpaid - applied, "Amount of line 21 refunded")
        s.fact(FORM, "original_refund", str(col_a("35a")))
        s.fact(FORM, "original_applied_to_estimated_tax", str(col_a("36")))
        s.fact(FORM, "original_amount_owed", str(owed_orig))
        if a.original_refund_received is not None and whole(a.original_refund_received) != col_a("35a"):
            s.diag("warning", "amendment_refund_received_differs",
                   f"The refund received, {whole(a.original_refund_received)}, differs from the original return's line 35a, "
                   f"{col_a('35a')}; if the IRS changed the return, state the adjusted amounts (amendment.as_previously_adjusted).", FORM)
        if a.original_overpayment_applied is not None and whole(a.original_overpayment_applied) != col_a("36"):
            s.diag("warning", "amendment_overpayment_applied_differs",
                   f"The overpayment applied to estimated tax, {whole(a.original_overpayment_applied)}, differs from the original "
                   f"return's line 36, {col_a('36')}; if the IRS changed the return, state the adjusted amounts.", FORM)
        if not changed and not fs_changed and deps_now == deps_orig:
            s.diag("warning", "amendment_no_change", "Nothing changes: every column B is zero and the filing status and dependents "
                                                     "are as filed. A Form 1040-X with no change is not filed.", FORM)

    # §6511(a): a refund claim within 3 years of the return (deemed filed no earlier than the due date, §6513(a)) or 2
    # years of the payment, whichever is later; withholding and estimated tax are deemed paid on the due date (§6513(b)).
    if filed_on is not None:
        claim_by = add_years(max(filed_on, due), 3)
        if a.last_payment_on is not None:
            claim_by = max(claim_by, add_years(a.last_payment_on, 2))
        s.fact(FORM, "refund_claim_by", claim_by.isoformat())
        if paid_after > 0 and a.last_payment_on is None:
            s.diag("warning", "amendment_payment_date_unknown",
                   "Tax was paid after the original was filed but the date of the last payment is not stated "
                   "(amendment.last_payment_on): the 2-year period of IRC §6511(a) cannot be figured.", FORM)
        if overpaid > 0 and today > claim_by:
            s.diag("warning", "amendment_refund_statute_expired",
                   f"The period for claiming this refund ended {claim_by.isoformat()} (IRC §6511(a): 3 years from the return "
                   f"deemed filed {max(filed_on, due).isoformat()}"
                   + (f", 2 years from the last payment {a.last_payment_on.isoformat()}" if a.last_payment_on else "")
                   + f"); today is {today.isoformat()}. The refund is barred unless an exception applies (§6511(d)).", FORM)
    elif pinned:
        s.diag("warning", "amendment_filing_date_unknown",
               "The date the original return was filed is not on record (amendment.original_filed_on): the §6511 period and "
               "the superseding question cannot be checked.", FORM)

    # Direct deposit of the refund (only on an electronically filed 1040-X; the renderer prints it).
    if a.direct_deposit is not None:
        dd = a.direct_deposit
        if not routing_number_valid(dd.routing_number.strip()):
            s.diag("error", "amendment_direct_deposit_invalid", "The routing number is not a valid 9-digit ABA number.", FORM)
        if not dd.account_number.strip() or dd.account_type is None:
            s.diag("error", "amendment_direct_deposit_invalid", "Direct deposit needs the account number and its type (checking or savings).", FORM)
        if refund <= 0:
            s.diag("warning", "amendment_direct_deposit_without_refund", "Direct deposit facts are stated but this return shows no refund.", FORM)
        s.fact(FORM, "direct_deposit", {"routing_number": dd.routing_number.strip(), "account_number_last4": dd.account_number.strip()[-4:],
                                        "account_type": dd.account_type})
