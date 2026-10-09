"""Adversarial review of the 952ee96 fixes: a dropped PostgreSQL connection reconnected with the store's default scope
("*"), so a client-scoped request continued firm-wide. A reconnect now keeps the scope the thread's request set."""

from __future__ import annotations

import pytest

from agentledger import db
from agentledger.ledger import store


@pytest.mark.skipif(db.backend() != "postgres", reason="row-level security scopes PostgreSQL sessions")
def test_a_dropped_connection_keeps_the_requests_client_scope(biz):
    conn = biz.conn
    store.add_client(conn, id="beta", name="Beta Holdings LLC", kind="business")
    conn.set_scope(["acme"])
    assert [r["id"] for r in conn.execute("SELECT id FROM clients ORDER BY id").fetchall()] == ["acme"]
    conn._get().raw.close()                              # the connection drops in the middle of the request
    assert [r["id"] for r in conn.execute("SELECT id FROM clients ORDER BY id").fetchall()] == ["acme"]
    conn.set_scope(["*"])
    assert [r["id"] for r in conn.execute("SELECT id FROM clients ORDER BY id").fetchall()] == ["acme", "beta"]
