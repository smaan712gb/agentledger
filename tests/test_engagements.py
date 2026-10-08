"""Engagement grants and reviewer authority (backlog F-05).

Staff see only the clients they are engaged on, in the API and, on PostgreSQL, in the database session itself.
A firm administrator can hold reviewer authority only when someone else records it, with its credential.
"""

from __future__ import annotations

import pytest
from test_tenancy import PW, accept, api, enrol  # noqa: F401  (api is a fixture)

from agentledger import db


@pytest.fixture
def firm(api):  # noqa: F811
    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])
    r = c.post("/api/platform/firms", json={"id": "rivera-cpa", "name": "Rivera CPA", "admin_email": "maya@rivera.example"}, headers=ops)
    admin = accept(c, r.json()["admin_invite_token"], "Maya")
    people = {"admin": admin, "ops": ops}
    for email, role in (("sam@rivera.example", "staff"), ("lee@rivera.example", "cpa"), ("kim@rivera.example", "firm_admin")):
        tok = c.post("/api/auth/invite", json={"email": email, "role": role}, headers=admin).json()["invite_token"]
        people[email.split("@")[0]] = accept(c, tok, email.split("@")[0].title())
    for cid in ("ortiz-auto", "lakeside-fuel"):
        assert c.post("/api/clients", json={"id": cid, "name": cid.replace("-", " ").title()}, headers=admin).status_code == 200
    ids = {u["email"].split("@")[0]: u["id"] for u in c.get("/api/auth/users", headers=admin).json()}
    return mod, c, people, ids


def test_staff_see_only_engaged_clients_until_revoked(firm):
    mod, c, p, ids = firm
    sam = p["sam"]
    assert c.get("/api/clients", headers=sam).json() == []
    assert c.get("/api/clients/ortiz-auto", headers=sam).status_code == 403
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "ortiz-auto"}, headers=p["lee"]).status_code == 200
    assert [x["id"] for x in c.get("/api/clients", headers=sam).json()] == ["ortiz-auto"]
    assert [x["id"] for x in c.get("/api/dashboard", headers=sam).json()["clients"]] == ["ortiz-auto"]
    assert c.get("/api/clients/ortiz-auto", headers=sam).status_code == 200
    assert c.get("/api/clients/lakeside-fuel", headers=sam).status_code == 403
    if db.backend() == "postgres":   # the database session itself is scoped, not only the API's filters
        me = c.get("/api/me", headers=sam).json()
        ctx = mod.A(me)
        assert ctx.conn.execute("SELECT count(*) FROM clients").fetchone()[0] == 1
    assert c.delete(f"/api/auth/users/{ids['sam']}/grants/ortiz-auto", headers=p["admin"]).json()["engaged"] == []
    assert c.get("/api/clients/ortiz-auto", headers=sam).status_code == 403
    events = [e["event"] for e in c.get("/api/auth/events", headers=p["admin"]).json()]
    assert "engagement_granted" in events and "engagement_revoked" in events


def test_who_may_grant_engagements(firm):
    mod, c, p, ids = firm
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "ortiz-auto"}, headers=p["sam"]).status_code == 403
    assert c.post(f"/api/auth/users/{ids['lee']}/grants", json={"client_id": "ortiz-auto"}, headers=p["admin"]).status_code == 403
    # Another firm's administrator cannot see or engage this firm's people.
    r = c.post("/api/platform/firms", json={"id": "lake-tax", "name": "Lake Tax", "admin_email": "lee@lake.example"}, headers=p["ops"])
    other = accept(c, r.json()["admin_invite_token"], "Other")
    assert c.post(f"/api/auth/users/{ids['sam']}/grants", json={"client_id": "ortiz-auto"}, headers=other).status_code == 404


def test_reviewer_authority_for_a_firm_admin_is_recorded_by_someone_else(firm):
    mod, c, p, ids = firm
    rid = c.post("/api/clients/ortiz-auto/returns", json={"tax_year": 2026, "inputs": {"filing_status": "single",
                 "taxpayer": {"ssn": "400-00-0001", "dob": "1990-01-01"}}}, headers=p["lee"]).json()["id"]
    refused = c.post(f"/api/returns/{rid}/approve", headers=p["admin"])
    assert refused.status_code == 403 and "credentialed reviewer" in refused.json()["detail"]
    url = f"/api/auth/users/{ids['maya']}/reviewer"
    assert c.post(url, json={"on": True, "credential": "CPA NY 123456"}, headers=p["admin"]).status_code == 403   # not yourself
    assert c.post(url, json={"on": True, "credential": ""}, headers=p["kim"]).status_code == 403                 # needs a credential
    assert c.post(url, json={"on": True, "credential": "CPA NY 123456"}, headers=p["kim"]).status_code == 200
    after = c.post(f"/api/returns/{rid}/approve", headers=p["admin"])
    assert "credentialed reviewer" not in after.text                    # now refused only by the workflow, if at all
    assert c.get("/api/me", headers=p["admin"]).json()["reviewer"] is True
    events = {e["event"]: e["detail"] for e in c.get("/api/auth/events", headers=p["admin"]).json()}
    assert events.get("reviewer_granted") == "CPA NY 123456"
