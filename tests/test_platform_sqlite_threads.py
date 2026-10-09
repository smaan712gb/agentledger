"""The SQLite platform store (development and self-hosting) gives each thread its own connection. Before, every
thread shared one: a statement another request ran while a critical section's transaction was open joined that
transaction, and a section that rolled back (a refusal) took the other request's record with it."""

from __future__ import annotations

import threading
import time

import pytest

from agentledger import db
from agentledger.security.platform import Platform

pytestmark = pytest.mark.skipif(db.backend() == "postgres", reason="the SQLite platform store")


def test_a_write_from_another_thread_is_not_lost_when_a_critical_section_rolls_back(tmp_path):
    plat = Platform(tmp_path, dev=True)
    attempting, done = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def another_request():
        try:
            attempting.set()
            plat.event("probe", email="probe@example.com")      # an audit record written outside any section
        except BaseException as exc:                            # noqa: BLE001  (reported by the test)
            errors.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=another_request)
    with pytest.raises(RuntimeError, match="injected"):
        with plat._critical("firm:probe"):
            plat.conn.execute("INSERT INTO firms (id, name, status) VALUES ('probe', 'Probe CPA', 'active')")
            t.start()
            assert attempting.wait(5)
            time.sleep(0.3)                                     # the other write is attempted while the section is open
            raise RuntimeError("injected: the section fails after the other request wrote")
    assert done.wait(30) and errors == []
    assert plat.conn.execute("SELECT COUNT(*) AS n FROM auth_events WHERE event = 'probe'").fetchone()["n"] == 1
    assert plat.conn.execute("SELECT 1 FROM firms WHERE id = 'probe'").fetchone() is None         # the section's own write
    plat.close()


def test_each_thread_has_its_own_connection(tmp_path):
    plat = Platform(tmp_path, dev=True)
    seen: list[int] = []

    def record():
        seen.append(id(plat.conn._get()))

    threads = [threading.Thread(target=record) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert len(set(seen)) == 3 and id(plat.conn._get()) not in seen
    plat.close()
    with pytest.raises(db.DatabaseError):
        plat.conn.execute("SELECT 1")
