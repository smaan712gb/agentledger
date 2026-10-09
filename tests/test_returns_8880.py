"""Form 8880, credit for qualified retirement savings contributions, IRC §25B (T1-01, S1).

Expected values are worked by hand from Form 8880 (2025) lines 1-12 and its instructions (the line 4 example and the
Credit Limit Worksheet, read on 2026-10-09), the 2026 adjusted gross income limits of Notice 2025-67 (pages 3-4: joint
48,500 / 52,500 / 80,500; head of household 36,375 / 39,375 / 60,375; all other 24,250 / 26,250 / 40,250), the
2026 standard deduction and rate schedules of Rev. Proc. 2025-32 (single 16,100, joint 32,200; 10% to 12,400 single,
24,800 joint, then 12%) and the Tax Table midpoint convention. Nothing below was taken from the engine.
"""

from datetime import date

import pytest
from test_returns_1040 import ctx, run, spouse, you

from agentledger.returns.individual import compute_individual
from agentledger.returns.model import Dependent, IRAAccount, IndividualReturn, Person, Retirement, RetirementSavings, W2

STATED_NONE = [RetirementSavings(owner="taxpayer", testing_period_distributions=0)]


def lines(r):
    return {k: int(v) for k, v in r.forms["f8880"].items()}


def test_single_filer_in_the_ten_percent_band():
    """Wages 30,000 (box 1) with 3,000 of 401(k) deferrals (box 12 code D), no distributions: line 2 = 3,000, 3 = 3,000,
    4 = 0, 5 = 3,000, 6 = 2,000 (the $2,000 cap), 7 = 2,000; AGI 30,000 is over 26,250 and not over 40,250, so line 9 =
    0.1 and line 10 = 200. Tax: taxable income 13,900, Tax Table row midpoint 13,925: 1,240 + 12% x 1,525 = 1,423 =
    line 11; line 12 = 200 to Schedule 3 line 4; Form 1040 line 22 = 1,223."""
    r = run(filing_status="single", taxpayer=you(full_time_student=False), w2s=[W2(wages=30000, box12={"D": 3000})],
            retirement_savings=STATED_NONE)
    assert lines(r) == {"1a": 0, "2a": 3000, "3a": 3000, "4a": 0, "5a": 3000, "6a": 2000, "7": 2000, "8": 30000, "10": 200,
                        "11": 1423, "12": 200}
    assert r.sheets.facts["f8880"]["9"] == "0.1" and r.sheets.facts["f8880"]["eligible"] == {"taxpayer": "eligible"}
    assert r.line("sch_3", "4") == 200 and r.line("f1040", "20") == 200 and r.line("f1040", "22") == 1223
    assert not r.blocking and "us_fed.individual.savers_credit_agi_limits" in {s["rule_id"] for s in r.sources}


def test_joint_return_both_spouses_distributions_in_both_columns():
    """The Form 8880 instructions' line 4 example, with both years filed jointly: you received a 5,000 distribution from
    a qualified plan in 2026 (a 1099-R on this return), your spouse received 2,000 from a Roth IRA in 2024 (a stated
    testing-period fact). Line 4 = 7,000 in both columns. You deferred 6,000 (code D): 3a = 6,000, 5a = 0, 6a = 0. Your
    spouse made 5,000 of Roth IRA contributions (Form 5498 box 10) and 4,000 of designated Roth deferrals (code AA):
    3b = 9,000, 5b = 2,000, 6b = 2,000; line 7 = 2,000. AGI 40,000 + 20,000 + 5,000 = 65,000: joint, over 52,500 and
    not over 80,500, line 9 = 0.1, line 10 = 200. Tax: taxable income 65,000 - 32,200 = 32,800, midpoint 32,825:
    2,480 + 12% x 8,025 = 3,443 = line 11; line 12 = 200."""
    r = run(filing_status="mfj", taxpayer=you(full_time_student=False), spouse=spouse(full_time_student=False),
            w2s=[W2(owner="taxpayer", wages=40000, box12={"D": 6000}), W2(owner="spouse", wages=20000, box12={"AA": 4000})],
            retirement=[Retirement(owner="taxpayer", payer="Plan", gross_distribution=5000, taxable_amount=5000)],
            ira_accounts=[IRAAccount(owner="spouse", roth_contributions=5000, fmv=20000)],
            retirement_savings=[RetirementSavings(owner="taxpayer", testing_period_distributions=0),
                                RetirementSavings(owner="spouse", testing_period_distributions=2000)])
    assert lines(r) == {"1a": 0, "2a": 6000, "3a": 6000, "4a": 7000, "5a": 0, "6a": 0,
                        "1b": 5000, "2b": 4000, "3b": 9000, "4b": 7000, "5b": 2000, "6b": 2000,
                        "7": 2000, "8": 65000, "10": 200, "11": 3443, "12": 200}
    assert r.line("sch_3", "4") == 200 and not r.blocking


def test_credit_limited_by_tax_in_the_fifty_percent_band():
    """Wages 20,000, 2,000 deferred: line 6 = 2,000; AGI 20,000 is not over 24,250, line 9 = 0.5, line 10 = 1,000.
    Taxable income 3,900, midpoint 3,925 x 10% = 392.50, rounded 393 = line 11 (no other credits); line 12 = 393 and
    Form 1040 line 22 = 0."""
    r = run(filing_status="single", taxpayer=you(full_time_student=False), w2s=[W2(wages=20000, box12={"D": 2000})],
            retirement_savings=STATED_NONE)
    assert lines(r) == {"1a": 0, "2a": 2000, "3a": 2000, "4a": 0, "5a": 2000, "6a": 2000, "7": 2000, "8": 20000, "10": 1000,
                        "11": 393, "12": 393}
    assert r.line("f1040", "16") == 393 and r.line("f1040", "22") == 0


def test_qualifying_surviving_spouse_uses_the_all_other_column():
    """Form 8880's line 9 table lists a qualifying surviving spouse with single filers: AGI 40,000 is in the 10% band
    (over 26,250, not over 40,250), not the joint 50% band. 3,000 deferred: line 10 = 2,000 x 0.1 = 200. Taxable income
    40,000 - 32,200 = 7,800 (the joint standard deduction), midpoint 7,825 x 10% = 782.50, rounded 783: line 12 = 200."""
    r = run(filing_status="qss", taxpayer=you(full_time_student=False), w2s=[W2(wages=40000, box12={"D": 3000})],
            dependents=[Dependent(first_name="Ana", ssn="400-00-0100", dob=date(2020, 1, 1), relationship="daughter")],
            retirement_savings=STATED_NONE)
    assert r.sheets.facts["f8880"]["9"] == "0.1" and r.line("f8880", "12") == 200 and r.line("f1040", "16") == 783


def test_direct_rollovers_do_not_reduce_the_contributions():
    """A 1099-R with code G (direct rollover) is excluded from line 4 (instructions, line 4: distributions not taxable
    as the result of a rollover); a code 7 distribution with 4,000 rolled over within 60 days counts for the 1,000 kept."""
    r = run(filing_status="single", taxpayer=you(full_time_student=False), w2s=[W2(wages=30000, box12={"D": 3000})],
            retirement=[Retirement(gross_distribution=10000, taxable_amount=0, distribution_code="G"),
                        Retirement(gross_distribution=5000, taxable_amount=1000, rollover_amount=4000)],
            retirement_savings=STATED_NONE)
    assert r.line("f8880", "4a") == 1000 and r.line("f8880", "5a") == 2000 and r.line("f8880", "12") == 200


def person(**kw):
    return Person(first_name="Alex", last_name="Rivera", ssn="400-00-0001", **kw)


@pytest.mark.parametrize("who,reason", [
    (you(full_time_student=True), "a full-time student"),
    (person(full_time_student=False, dob=date(2009, 1, 2)), "under 18 at the end of 2026 (born after 2009-01-01)"),
    (you(full_time_student=False, can_be_claimed_as_dependent=True), "can be claimed as a dependent on someone else's return"),
], ids=["student", "under-18", "dependent"])
def test_ineligible_persons_get_no_credit_and_no_form(who, reason):
    """Form 8880, Caution: no credit for a person who was a student, born after January 1, 2009, or claimed as a
    dependent (2025 form: born after January 1, 2008)."""
    r = run(filing_status="single", taxpayer=who, w2s=[W2(wages=30000, box12={"D": 3000})], retirement_savings=STATED_NONE)
    assert "f8880" not in r.forms and r.line("sch_3", "4") == 0 and not r.blocking
    [d] = [d for d in r.diagnostics if d.code == "form_8880_no_credit"]
    assert d.severity == "info"


def test_born_on_january_first_2009_is_eighteen():
    r = run(filing_status="single", taxpayer=person(full_time_student=False, dob=date(2009, 1, 1)),
            w2s=[W2(wages=30000, box12={"D": 3000})], retirement_savings=STATED_NONE)
    assert r.line("f8880", "12") == 200


def test_agi_above_the_limit_means_no_credit_and_no_questions():
    """AGI 60,000 single is over 40,250: no credit, and the unknown student status and testing period are not asked for."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000, box12={"D": 3000})])
    assert "f8880" not in r.forms and r.line("sch_3", "4") == 0 and not r.blocking
    assert any(d.code == "form_8880_no_credit" and "above the limit" in d.message for d in r.diagnostics)


@pytest.mark.parametrize("kw,code", [
    (dict(taxpayer=you(), retirement_savings=STATED_NONE), "form_8880_student_status_unknown"),
    (dict(taxpayer=you(full_time_student=False)), "form_8880_testing_period_unknown"),
    (dict(taxpayer=person(full_time_student=False), retirement_savings=STATED_NONE), "form_8880_age_unknown"),
    (dict(taxpayer=you(full_time_student=False), retirement_savings=STATED_NONE, retirement_savings_contributions={"taxpayer": 2000}),
     "form_8880_deprecated_input"),
], ids=["student-unknown", "testing-period-unknown", "dob-unknown", "deprecated-hand-total"])
def test_unknown_facts_block_instead_of_claiming_or_dropping_the_credit(kw, code):
    r = run(filing_status="single", w2s=[W2(wages=30000, box12={"D": 3000})], **kw)
    assert [d.code for d in r.blocking] == [code], [(d.code, d.message) for d in r.diagnostics]
    if code != "form_8880_deprecated_input":
        assert "f8880" not in r.forms and r.line("sch_3", "4") == 0           # never claimed while unknown


def test_the_spouses_unknown_testing_period_blocks_a_joint_return():
    """Both spouses' distributions go in both columns, so the spouse's testing period matters even without her own
    contributions."""
    r = run(filing_status="mfj", taxpayer=you(full_time_student=False), spouse=spouse(full_time_student=False),
            w2s=[W2(wages=40000, box12={"D": 3000})], retirement_savings=STATED_NONE)
    [d] = r.blocking
    assert d.code == "form_8880_testing_period_unknown" and "spouse" in d.message


def test_missing_published_figures_block_rather_than_guess(monkeypatch):
    c = ctx()
    original = c.try_param
    monkeypatch.setattr(c, "try_param", lambda rid, on: None if rid.startswith("us_fed.individual.savers_credit") else original(rid, on))
    ret = IndividualReturn(tax_year=2026, filing_status="single", taxpayer=you(full_time_student=False),
                           w2s=[W2(wages=30000, box12={"D": 3000})], retirement_savings=STATED_NONE)
    r = compute_individual(c, ret)
    assert [d.code for d in r.blocking] == ["savers_credit_rule_missing"] and r.line("sch_3", "4") == 0


def test_the_rule_values_end_with_2026():
    """IRC §25B is replaced by the saver's match for tax years after 2026 (SECURE 2.0 §103; Form 8880 (2025), What's
    New): a 2027 lookup finds no value, so nothing is guessed."""
    kb = ctx().kb
    for rid in ("us_fed.individual.savers_credit", "us_fed.individual.savers_credit_agi_limits"):
        assert kb.try_resolve(rid, date(2026, 12, 31)) is not None and kb.try_resolve(rid, date(2027, 1, 1)) is None
    limits = kb.resolve("us_fed.individual.savers_credit_agi_limits", date(2026, 6, 30))
    assert limits.value == {"mfj": [48500, 52500, 80500], "hoh": [36375, 39375, 60375], "other": [24250, 26250, 40250]}
    assert "Notice 2025-67" in limits.source and limits.url == "https://www.irs.gov/pub/irs-drop/n-25-67.pdf"
