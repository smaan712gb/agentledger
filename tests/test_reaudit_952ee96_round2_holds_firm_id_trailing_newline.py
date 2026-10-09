"""Second adversarial review of the fix for re-audit 952ee96 finding 3 (tests/adv2_holds_firm_id_trailing_newline.py):
the firm id check ("$" matches before a final newline) accepted "dev\\n", past the reservation, and "acme\\n", whose
PostgreSQL store name is the same as firm "acme-"'s, and schema-tenancy provisioning had no adoption check, so the
second firm was provisioned onto the first firm's schema. Asserted now: create_firm refuses an id with a trailing
newline as not a firm id and creates nothing for it, the API strips the id it is given, and provisioning refuses a
schema that exists without its own provisioning record ("refusing to adopt it"), whether another firm's or a
leftover, leaving it and its owner's records untouched.
"""

from __future__ import annotations

import pytest
from test_tenancy import PW, api, enrol  # noqa: F401  (api is a fixture)

from agentledger import db
from agentledger.ledger import store
from agentledger.security.crypto import CryptoError, Keyring
from agentledger.security.platform import FIRM_ID, AuthError, Platform

NOT_A_FIRM_ID = "firm id must be 2-41 lowercase letters, digits or hyphens, and not a reserved name"
needs_pg = pytest.mark.skipif(db.backend() != "postgres", reason="needs AGENTLEDGER_DATABASE=postgres")


@pytest.fixture(autouse=True)
def _schema_tenancy(monkeypatch):
    if db.backend() == "postgres":
        monkeypatch.setenv("AGENTLEDGER_PG_TENANCY", "schema")


@pytest.mark.parametrize("firm_id", ["acme\n", "dev\n"], ids=["names-acme-s-store", "reserved-id"])
def test_firm_id_with_a_trailing_newline_is_refused_and_nothing_is_created(tmp_path, firm_id):
    plat = Platform(tmp_path, dev=True)
    plat.create_firm("acme-", "Acme Hyphen CPA", by="ops")
    assert FIRM_ID.fullmatch(firm_id) is None

    with pytest.raises(AuthError, match=NOT_A_FIRM_ID):
        plat.create_firm(firm_id, "Pasted Id CPA", by="ops")

    assert [r["id"] for r in plat.conn.execute("SELECT id FROM firms ORDER BY id")] == ["acme-"]
    assert sorted(p.name for p in (tmp_path / "tenants").iterdir()) == ["acme-"]
    assert plat.get(firm_id) is None
    with pytest.raises(CryptoError):
        Keyring(plat.conn, plat.keys.master).current_version(firm_id)
    assert [e["firm_id"] for e in plat.events() if e["event"] == "firm_created"] == ["acme-"]


def test_api_strips_the_firm_id_it_is_given(api):  # noqa: F811
    mod, c = api
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    ops = enrol(c, step["challenge"], step["secret"])

    r = c.post("/api/platform/firms", json={"id": "acme-cpa\n", "name": "Acme CPA", "admin_email": "owner@acme.example"},
               headers=ops)
    assert r.status_code == 200, r.text
    assert r.json()["firm"]["id"] == "acme-cpa"
    r = c.post("/api/platform/firms", json={"id": "dev\n", "name": "Dev Partners CPA", "admin_email": "owner@dev.example"},
               headers=ops)
    assert r.status_code == 400 and "reserved name" in r.json()["detail"], r.text
    assert [f["id"] for f in mod.PLATFORM.firms()] == ["acme-cpa"]


@needs_pg
def test_provisioning_never_hands_one_firms_schema_to_another(tmp_path):
    """A firm row "acme\\n" left by the release before the fix (on Linux create_firm got that far): the provisioning
    worker refuses its id, and provisioning it directly refuses firm "acme-"'s schema, which the row would name."""
    from agentledger.pg import provision

    plat = Platform(tmp_path, dev=True)
    plat.create_firm("acme-", "Acme Hyphen CPA", by="ops")
    first = plat.get("acme-")
    path = plat.tenant_dir("acme-") / "state" / "agentledger.db"
    conn = db.open_store(path)
    try:
        store.add_client(conn, id="jordan-lee", name="Jordan Lee", kind="individual")
    finally:
        conn.close()
    plat.conn.execute("INSERT INTO firms (id, name, status) VALUES (?, 'Pasted Id CPA', 'provisioning')", ("acme\n",))

    [pending] = plat.provision_pending(by="provisioning-worker")
    assert (pending["firm"], pending["status"]) == ("acme\n", "provisioning") and NOT_A_FIRM_ID in pending["error"]
    with pytest.raises(provision.ProvisioningError,
                       match=rf"schema {first['name']} exists without a provisioning record; refusing to adopt it"):
        provision.provision("acme\n", plat)

    assert plat.get("acme\n") is None and plat.get("acme-") == first
    conn = db.open_store(path)
    try:
        assert [c["id"] for c in store.list_clients(conn)] == ["jordan-lee"]
    finally:
        conn.close()


@needs_pg
def test_leftover_schema_without_a_provisioning_record_is_never_adopted(tmp_path):
    from agentledger import pg
    from agentledger.pg import provision

    name = db.pg_name("firm_leftover_cpa")                           # what provisioning would name firm leftover-cpa's
    owner = pg.connect(pg.migration_url())
    try:
        owner.execute(f'CREATE SCHEMA "{name}"')
        owner.execute(f'CREATE TABLE "{name}".kept (note text)')
        owner.execute(f'INSERT INTO "{name}".kept VALUES (%s)', ("a store kept from an earlier deployment",))
    finally:
        owner.close()
    plat = Platform(tmp_path, dev=True)

    with pytest.raises(provision.ProvisioningError, match=rf"schema {name} exists without a provisioning record; refusing to adopt it"):
        plat.create_firm("leftover-cpa", "Leftover CPA", by="ops")
    assert plat.firm("leftover-cpa")["status"] == "provisioning" and plat.get("leftover-cpa") is None
    assert plat.abandon_firm("leftover-cpa", by="ops") == "nothing recorded"
    assert plat.firm("leftover-cpa")["status"] == "deleted"

    owner = pg.connect(pg.migration_url())
    try:
        assert owner.execute(f'SELECT note FROM "{name}".kept').fetchall() == [("a store kept from an earlier deployment",)]
    finally:
        owner.close()
