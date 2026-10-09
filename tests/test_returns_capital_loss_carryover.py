"""Capital Loss Carryover Worksheet (Schedule D instructions, lines 1-13) and the IRC §1211(b) limit (T1-01, S1).

Expected values are worked by hand from the worksheet text of the Schedule D instructions (2025 edition, page D-11, read
on 2026-10-09; the 2026 edition carries the same lines one year on), the §1211(b) limit (3,000; 1,500 married filing
separately) and the 2026 standard deduction of Rev. Proc. 2025-32 §4.14 (16,100 single and MFS, 32,200 joint). Nothing
below was taken from the engine.

The worksheet, for a 2026 return (carryover to 2027):
  1. Form 1040 line 15, as it would be if a negative amount could be entered
  2. Schedule D line 21 loss, as a positive amount
  3. Combine lines 1 and 2; if zero or less, -0-
  4. The smaller of line 2 or line 3
  If Schedule D line 7 is a loss: 5. that loss; 6. any Schedule D line 15 gain; 7. lines 4 + 6; 8. short-term carryover = 5 - 7
  (otherwise line 5 is -0- and lines 6-8 are skipped)
  If Schedule D line 15 is a loss: 9. that loss; 10. any Schedule D line 7 gain; 11. line 4 - line 5 (floor 0); 12. 10 + 11;
  13. long-term carryover = 9 - 12 (otherwise lines 9-13 are skipped)
"""

from datetime import date
from decimal import Decimal

import pytest
from test_return_workflow import fam, household  # noqa: F401  (fam is a fixture)
from test_returns_1040 import run, spouse, you

from agentledger.returns.facts import InputRejected
from agentledger.returns.model import CapitalTransaction, PriorYear, W2
from agentledger.returns.store import ENGINE_VERSION, Returns, _check_fields, carryovers

WS = "ws_capital_loss_carryover"


def short_loss(amount):
    """A 2026 sale at a short-term loss of `amount`."""
    return CapitalTransaction(description="S", acquired=date(2026, 1, 5), sold=date(2026, 3, 1), proceeds=1000, cost_basis=1000 + amount)


def long_sale(gain):
    """A 2026 sale of property held since 2020 (gain negative for a loss)."""
    return CapitalTransaction(description="L", acquired=date(2020, 1, 5), sold=date(2026, 3, 1), proceeds=5000, cost_basis=5000 - gain)


def lines(r, form):
    return {k: int(v) for k, v in r.forms[form].items()}


def test_short_term_loss_only():
    """Wages 50,000, a 10,000 short-term loss. Line 21 = -3,000; AGI 47,000; taxable income 47,000 - 16,100 = 30,900.
    Worksheet: 1 = 30,900; 2 = 3,000; 3 = 33,900; 4 = 3,000; line 7 is a loss: 5 = 10,000; 6 = 0; 7 = 3,000; 8 = 7,000.
    Line 15 is not a loss: lines 9-13 are skipped."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=50000)], capital_transactions=[short_loss(10000)])
    assert r.line("sch_d", "7") == -10000 and r.line("sch_d", "21") == -3000 and r.line("f1040", "11a") == 47000
    assert lines(r, WS) == {"1": 30900, "2": 3000, "3": 33900, "4": 3000, "5": 10000, "6": 0, "7": 3000, "8": 7000}
    assert r.carryforwards == {"capital_loss_carryover_short": Decimal(7000), "capital_loss_carryover_long": Decimal(0)}
    assert r.to_dict()["carryforwards"] == {"capital_loss_carryover_short": "7000", "capital_loss_carryover_long": "0"}
    assert not any(d.code == "capital_loss_carryover" for d in r.diagnostics)      # the worksheet replaces the old note
    assert not r.blocking


def test_long_term_carryover_from_the_prior_year_partly_used():
    """A 10,000 long-term carryover from 2025 (prior_year group) and nothing else on Schedule D, wages 60,000. Schedule D:
    line 14 = -10,000, 15 = -10,000, 16 = -10,000, 21 = -3,000; AGI 57,000; taxable income 40,900. Worksheet: 1 = 40,900;
    2 = 3,000; 3 = 43,900; 4 = 3,000; line 7 is not a loss: 5 = 0, skip to 9 = 10,000; 10 = 0; 11 = 3,000 - 0 = 3,000;
    12 = 3,000; 13 = 7,000 carries to 2027."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=PriorYear(capital_loss_carryover_long=10000))
    assert (r.line("sch_d", "14"), r.line("sch_d", "15"), r.line("sch_d", "16"), r.line("sch_d", "21")) == (-10000, -10000, -10000, -3000)
    assert r.line("f1040", "7a") == -3000 and r.line("f1040", "11a") == 57000 and r.line("f1040", "15") == 40900
    assert lines(r, WS) == {"1": 40900, "2": 3000, "3": 43900, "4": 3000, "5": 0, "9": 10000, "10": 0, "11": 3000, "12": 3000, "13": 7000}
    assert r.carryforwards == {"capital_loss_carryover_short": Decimal(0), "capital_loss_carryover_long": Decimal(7000)}
    assert r.sheets.facts[WS]["carries_to"] == 2027


def test_negative_taxable_income_carries_the_whole_loss():
    """Wages 15,000 and an 8,000 long-term loss. Line 21 = -3,000; AGI 12,000; the standard deduction of 16,100 leaves
    taxable income of -4,100 if it could be negative (Form 1040 line 15 shows 0). Worksheet: 1 = (4,100); 2 = 3,000;
    3 = 0 (combined -1,100, floored); 4 = 0; 5 = 0; 9 = 8,000; 10 = 0; 11 = 0; 12 = 0; 13 = 8,000: none of the deduction
    reduced taxable income, so the whole loss carries (IRC §1212(b)(2))."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=15000)], capital_transactions=[long_sale(-8000)])
    assert r.line("f1040", "7a") == -3000 and r.line("f1040", "11a") == 12000 and r.line("f1040", "15") == 0
    assert lines(r, WS) == {"1": -4100, "2": 3000, "3": 0, "4": 0, "5": 0, "9": 8000, "10": 0, "11": 0, "12": 0, "13": 8000}
    assert r.carryforwards["capital_loss_carryover_long"] == 8000 and r.carryforwards["capital_loss_carryover_short"] == 0


def test_short_term_loss_against_a_long_term_gain():
    """Wages 60,000, a 5,000 short-term loss and a 1,000 long-term gain: line 7 = -5,000, 15 = 1,000, 16 = -4,000,
    21 = -3,000, taxable income 40,900. Worksheet: 4 = 3,000; 5 = 5,000; 6 = 1,000; 7 = 4,000; 8 = 1,000 short-term;
    line 15 is a gain, so lines 9-13 are skipped (no long-term carryover)."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_transactions=[short_loss(5000), long_sale(1000)])
    assert (r.line("sch_d", "7"), r.line("sch_d", "15"), r.line("sch_d", "16"), r.line("sch_d", "21")) == (-5000, 1000, -4000, -3000)
    assert lines(r, WS) == {"1": 40900, "2": 3000, "3": 43900, "4": 3000, "5": 5000, "6": 1000, "7": 4000, "8": 1000}
    assert r.carryforwards == {"capital_loss_carryover_short": Decimal(1000), "capital_loss_carryover_long": Decimal(0)}


def test_short_term_losses_are_used_before_long_term_losses():
    """Wages 60,000, a 2,000 short-term and a 6,000 long-term loss: line 16 = -8,000, 21 = -3,000, taxable income 40,900.
    Worksheet: 4 = 3,000; 5 = 2,000; 6 = 0; 7 = 3,000; 8 = 0 (the short-term loss is used up first); 9 = 6,000; 10 = 0;
    11 = 3,000 - 2,000 = 1,000; 12 = 1,000; 13 = 5,000 long-term carryover (IRC §1212(b)(1))."""
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_transactions=[short_loss(2000), long_sale(-6000)])
    assert lines(r, WS) == {"1": 40900, "2": 3000, "3": 43900, "4": 3000, "5": 2000, "6": 0, "7": 3000, "8": 0,
                            "9": 6000, "10": 0, "11": 1000, "12": 1000, "13": 5000}
    assert r.carryforwards == {"capital_loss_carryover_short": Decimal(0), "capital_loss_carryover_long": Decimal(5000)}


def test_married_filing_separately_limit():
    """MFS, wages 50,000, a 4,000 short-term loss: line 21 = -1,500 (IRC §1211(b)(1)); taxable income 48,500 - 16,100 =
    32,400. Worksheet: 2 = 1,500; 3 = 33,900; 4 = 1,500; 5 = 4,000; 6 = 0; 7 = 1,500; 8 = 2,500."""
    r = run(filing_status="mfs", taxpayer=you(), spouse=spouse(), w2s=[W2(wages=50000)], capital_transactions=[short_loss(4000)])
    assert r.line("sch_d", "21") == -1500
    assert lines(r, WS) == {"1": 32400, "2": 1500, "3": 33900, "4": 1500, "5": 4000, "6": 0, "7": 1500, "8": 2500}
    assert r.carryforwards["capital_loss_carryover_short"] == 2500


def test_no_worksheet_without_a_net_loss():
    gain = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_transactions=[long_sale(4000)])
    assert WS not in gain.forms and gain.carryforwards == {"capital_loss_carryover_short": Decimal(0), "capital_loss_carryover_long": Decimal(0)}
    small = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_transactions=[short_loss(2000)])
    # A 2,000 loss is deducted in full (line 21 = -2,000): the worksheet gives 4 = 2,000, 5 = 2,000, 7 = 2,000, 8 = 0.
    assert small.line("sch_d", "21") == -2000 and lines(small, WS)["8"] == 0
    assert small.carryforwards == {"capital_loss_carryover_short": Decimal(0), "capital_loss_carryover_long": Decimal(0)}
    none = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)])
    assert WS not in none.forms and "sch_d" not in none.forms and none.to_dict()["carryforwards"] == {
        "capital_loss_carryover_short": "0", "capital_loss_carryover_long": "0"}


def test_legacy_inputs_still_feed_schedule_d_and_a_conflict_blocks():
    legacy = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_loss_carryover_long=10000)
    assert legacy.line("sch_d", "14") == -10000 and legacy.carryforwards["capital_loss_carryover_long"] == 7000 and not legacy.blocking
    same = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_loss_carryover_long=10000,
               prior_year=PriorYear(capital_loss_carryover_long=10000))
    assert same.line("sch_d", "14") == -10000 and not same.blocking
    differ = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], capital_loss_carryover_long=5000,
                 prior_year=PriorYear(capital_loss_carryover_long=10000))
    [d] = [d for d in differ.blocking if d.code == "capital_loss_carryover_conflict"]
    assert d.form == "sch_d" and d.line == "14" and "10000" in d.message and "5000" in d.message
    assert differ.line("sch_d", "14") == -10000                                   # the prior-year group is used meanwhile
    negative = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=PriorYear(capital_loss_carryover_short=-500))
    assert any(d.code == "capital_loss_carryover_sign" for d in negative.blocking) and negative.line("sch_d", "6") == 0


def test_an_empty_prior_year_group_states_no_carryover():
    r = run(filing_status="single", taxpayer=you(), w2s=[W2(wages=60000)], prior_year=PriorYear(tax=4000, agi=55000))
    assert "sch_d" not in r.forms and WS not in r.forms and not r.blocking


def test_the_store_accepts_the_prior_year_group_and_records_its_carryover(fam):  # noqa: F811
    inputs = {**household(), "w2s": [{"owner": "taxpayer", "wages": "60000"}],
              "prior_year": {"capital_loss_carryover_long": "10000", "agi": "88000", "filing_status": "mfj"}}
    assert carryovers(inputs) == ["prior_year.capital_loss_carryover_long"]
    R = Returns(fam.conn, fam.kb)
    rid = R.create("rivera", 2026, "maya", inputs)
    R.compute(rid, "maya")
    v = R.latest(rid)
    assert v["result"]["forms"]["sch_d"]["14"] == "-10000" and v["result"]["forms"][WS]["13"] == "7000"
    assert v["result"]["carryforwards"] == {"capital_loss_carryover_short": "0", "capital_loss_carryover_long": "7000"}
    assert v["result"]["pinned"]["engine"] == ENGINE_VERSION == "1040-2026.2"    # computations changed: recompute reopens review
    assert fam.conn.execute("SELECT detail FROM return_retention_facts WHERE return_id = ? AND kind = 'carryover'",
                            (rid,)).fetchone()[0] == "prior_year.capital_loss_carryover_long"
    assert WS not in v["result"]["coverage"]["forms"]                           # a worksheet, not a registry form


@pytest.mark.parametrize("bad,key", [
    ({"prior_year": {"capital_loss_carryover_lon": "1000"}}, "prior_year.capital_loss_carryover_lon"),
    ({"prior_year": {"ftc_carryovers": [{"category": "passive", "carryover": "100", "year": 2024}]}}, "prior_year.ftc_carryovers[0].year"),
    ({"prior_year": {"nonrecaptured_1231_losses": [{"tax_year": 2024, "loss": "100"}]}}, "prior_year.nonrecaptured_1231_losses[0].loss"),
])
def test_unknown_keys_inside_the_prior_year_group_are_refused(bad, key):
    with pytest.raises(InputRejected) as e:
        _check_fields({**household(), **bad})
    assert key in str(e.value)
