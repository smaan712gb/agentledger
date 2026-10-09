"""A cancellation belongs to the offboarding run it was made against. Found by the second review of the fix for
re-audit 952ee96 finding 3: the request was recorded without checking that a run still held the lease, so a request
landing just as the run backed out on its own (an active hold, an unreadable store) was left behind and cancelled the
next, separately decided, offboarding once. Asserted now: a request is recorded only against a run holding the lease;
when none does, the cancellation takes the lease itself and finishes now (or finds the firm already back in use); and
a new offboarding begins with no request left over from an earlier one."""

from __future__ import annotations

import pytest

from agentledger import db
from agentledger.security.platform import AuthError, Platform

FIRM = "second-thoughts-cpa"
OFFBOARD = "signed offboarding request from the firm's owner, 2026-10-01"
CANCEL = "the firm's owner withdrew the offboarding request on 2026-10-02"


@pytest.fixture(autouse=True)
def _schema_tenancy(monkeypatch):
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")


def _firm(tmp_path) -> Platform:
    plat = Platform(tmp_path, dev=True)
    plat.create_firm(FIRM, "Second Thoughts CPA", by="ops")
    assert plat.firm(FIRM)["status"] == "active"
    return plat


def _request_left_over(plat) -> None:
    """What the race left: a request recorded after the run released the lease (no holder, firm back in use)."""
    plat.conn.execute("INSERT INTO offboarding_runs (firm_id) VALUES (?) ON CONFLICT (firm_id) DO NOTHING", (FIRM,))
    plat.conn.execute("UPDATE offboarding_runs SET holder = NULL, lease_until = NULL, cancel_requested_at = datetime('now'), "
                      "cancel_by = 'maya', cancel_reason = ? WHERE firm_id = ?", (CANCEL, FIRM))


def test_a_request_left_over_from_an_earlier_offboarding_does_not_cancel_the_next(tmp_path):
    plat = _firm(tmp_path)
    _request_left_over(plat)
    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)                    # a new decision: not cancelled by the old request
    assert plat.firm(FIRM)["status"] == "deleted"
    events = [e["event"] for e in plat.events(FIRM)]
    assert "firm_deleted" in events and "firm_offboarding_cancelled" not in events


def test_a_cancellation_is_recorded_only_against_a_run_holding_the_lease(tmp_path, monkeypatch):
    """The run released the lease between the cancellation's failed claim and its request: the request is not
    recorded against nobody; the cancellation claims the lease itself and finds the firm back in use."""
    plat = _firm(tmp_path)
    plat.conn.execute("UPDATE firms SET status = 'offboarding', offboarding_from = 'active' WHERE id = ?", (FIRM,))
    claims = iter([False, True])
    real_claim = plat._claim_run

    def claim(firm_id, holder):
        if not next(claims, True):                                        # the run still held the lease...
            plat.conn.execute("INSERT INTO offboarding_runs (firm_id) VALUES (?) ON CONFLICT (firm_id) DO NOTHING", (firm_id,))
            plat.conn.execute("UPDATE offboarding_runs SET holder = NULL, lease_until = NULL WHERE firm_id = ?", (firm_id,))
            plat.conn.execute("UPDATE firms SET status = 'active', offboarding_from = NULL WHERE id = ?", (firm_id,))
            return False                                                  # ...and backed out on its own just then
        return real_claim(firm_id, holder)

    monkeypatch.setattr(plat, "_claim_run", claim)
    with pytest.raises(AuthError, match="no longer being offboarded"):
        plat.cancel_offboarding(FIRM, by="maya", reason=CANCEL)
    assert plat._cancel_request(FIRM) is None                              # nothing left behind
    assert plat.firm(FIRM)["status"] == "active"
    r = plat.conn.execute("SELECT holder FROM offboarding_runs WHERE firm_id = ?", (FIRM,)).fetchone()
    assert r["holder"] is None                                             # the lease it took is released again
    plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)                     # the next offboarding is not cancelled
    assert plat.firm(FIRM)["status"] == "deleted"


def test_a_request_against_a_live_run_is_recorded_and_honoured(tmp_path, monkeypatch):
    plat = _firm(tmp_path)
    plat.conn.execute("UPDATE firms SET status = 'offboarding', offboarding_from = 'active' WHERE id = ?", (FIRM,))
    assert plat._claim_run(FIRM, "another-process:run")                    # a run in progress holds the lease
    assert plat.cancel_offboarding(FIRM, by="maya", reason=CANCEL) == "requested"
    assert plat._cancel_request(FIRM)["cancel_by"] == "maya"
    plat._release_run(FIRM, "another-process:run")                         # the run stops at its next stage: it reads the
    with pytest.raises(AuthError, match="cancelled before the store's removal began"):   # request and backs out
        plat.delete_firm(FIRM, by="ops", reason=OFFBOARD)
    assert plat.firm(FIRM)["status"] == "active" and plat._cancel_request(FIRM) is None
