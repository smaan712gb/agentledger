"""Second adversarial review of the write-ahead deletion (re-audit of 952ee96, finding 4): moving a document filed
before content addressing could leave a full copy that no record named (the old file when removing it after the commit
failed, the new copy when the move failed after copying), integrity() swept only the blob store, and the document's
recorded deletion then left that copy behind while integrity() reported ok. Asserted now (both backends): an old file
the move cannot remove is recorded as a version of the document ("previous path, not yet removed"), so the document's
deletion removes it too; a failed move removes its copy; and the integrity sweep lists path-addressed files no record
names (a worker killed between the commit and the removal) and files a recorded deletion should have removed.
"""

from __future__ import annotations

import hashlib
from datetime import date

import pytest

from agentledger import audit, db
from agentledger.evidence import records
from agentledger.intake import pipeline
from agentledger.ledger import store

TODAY = date(2026, 10, 8)
REASON = "annual retention review"
MOVE = "misfiled in 2017: this receipt belongs to Beta Holdings"
DATA = b"2016 receipt scanned before content addressing"
PREVIOUS = "previous path, not yet removed"


class Killed(BaseException):
    """The worker dies (SIGKILL, a container stop): no handler after this point runs."""


def _legacy(conn, vault):
    """doc_legacy: Acme's receipt filed by path before content addressing (no version recorded), due for deletion."""
    store.add_client(conn, id="beta", name="Beta Holdings LLC", kind="business")
    sha = hashlib.sha256(DATA).hexdigest()
    old = f"acme/undated/receipts/2017-01-05_Receipt_scan_{sha[:8]}.txt"
    vault.write(old, DATA)
    conn.execute("INSERT INTO documents (id, client_id, sha256, original_name, media_type, channel, received_at, status, "
                 "vault_path, fields, doc_type, retention_class, retention_confirmed_by, retention_confirmed_at) VALUES "
                 "('doc_legacy', 'acme', ?, 'scan.txt', 'text/plain', 'upload', '2017-01-05T00:00:00+00:00', 'filed', ?, "
                 "'{}', 'Receipt', 'tax_return_support', 'maya', ?)", (sha, old, audit.now()))
    assert [d["id"] for d in records.due_for_deletion(conn, TODAY)] == ["doc_legacy"]
    return old


def _old_file_stays(monkeypatch, vault, old, exc):
    """Removing `old` raises `exc` (the file is open in another process, or the worker dies); other paths are removed."""
    real = vault.delete

    def delete(loc):
        if str(loc) == old:
            raise exc
        return real(loc)

    monkeypatch.setattr(vault, "delete", delete)
    return real


def _move(conn, vault):
    return pipeline.assign(conn, vault, "doc_legacy", "beta", "lee", "cpa", move_reason=MOVE)


def _confirm_and_purge(conn, vault):
    records.confirm_retention(conn, "doc_legacy", tax_year=None, retention_class="tax_return_support", actor="maya",
                              role="cpa", note="receipt checked again after the move, no tax year relied on")
    out = records.purge_expired(conn, vault, TODAY, actor="maya", role="cpa", attested=True, reason=REASON)
    assert [r["document_id"] for r in out] == ["doc_legacy"] and out.failures == []
    return out


def _outcomes(conn):
    out: dict[str, list[str]] = {}
    for r in db.rows(conn, "SELECT b.locator, r.outcome FROM blob_deletion_results r JOIN blob_deletions b "
                           "ON b.id = r.deletion_id WHERE b.document_id = 'doc_legacy' ORDER BY r.id"):
        out.setdefault(r["locator"], []).append(r["outcome"])
    return out


def _files(vault, client):
    root = vault.root / client
    return sorted(p.relative_to(vault.root).as_posix() for p in root.rglob("*") if p.is_file()) if root.exists() else []


def test_an_old_file_the_move_cannot_remove_stays_part_of_the_document(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    old = _legacy(conn, vault)
    real = _old_file_stays(monkeypatch, vault, old, PermissionError(13, "injected: the file is open in a search indexer"))
    moved = _move(conn, vault)
    monkeypatch.setattr(vault, "delete", real)
    new = moved["vault_path"]
    assert moved["client_id"] == "beta" and new.startswith("beta/") and vault.read(new) == DATA
    assert _files(vault, "acme") == [old] and _files(vault, "beta") == [new]
    [version] = records.versions(conn, "doc_legacy")                      # the record names the copy left behind
    assert version["locator"] == old and version["reason"] == PREVIOUS and version["sha256"] == moved["sha256"]
    report = records.integrity(conn, vault)
    assert report["storage_swept"] is True and report["ok"] is True
    assert report["unreferenced_objects"] == [] and report["deleted_but_still_stored"] == []
    assert report["live_documents_missing_bytes"] == []


def test_the_deletion_of_a_moved_document_removes_the_file_the_move_left(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    old = _legacy(conn, vault)
    real = _old_file_stays(monkeypatch, vault, old, PermissionError(13, "injected: the file is open in a search indexer"))
    new = _move(conn, vault)["vault_path"]
    monkeypatch.setattr(vault, "delete", real)
    _confirm_and_purge(conn, vault)
    [receipt] = db.rows(conn, "SELECT client_id, locators FROM deletion_receipts WHERE document_id = 'doc_legacy'")
    assert receipt["client_id"] == "beta" and sorted(receipt["locators"].split(",")) == sorted([old, new])
    assert _outcomes(conn) == {old: ["started", "deleted"], new: ["started", "deleted"]}
    assert not vault.exists(old) and not vault.exists(new) and _files(vault, "acme") == [] and _files(vault, "beta") == []
    report = records.integrity(conn, vault)
    assert report["ok"] is True and report["deleted_but_still_stored"] == [] and report["unreferenced_objects"] == []


def test_a_failed_move_removes_its_copy_from_the_other_clients_folder(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    old = _legacy(conn, vault)
    real_record = audit.record

    def record(c, actor, role, action, payload, client_id=None):
        if action == "document.assigned":
            raise db.DatabaseError("injected: audit trail write failed")
        return real_record(c, actor, role, action, payload, client_id=client_id)

    monkeypatch.setattr(audit, "record", record)
    with pytest.raises(db.DatabaseError, match="injected"):
        _move(conn, vault)
    monkeypatch.setattr(audit, "record", real_record)
    assert db.one(conn, "SELECT client_id, vault_path FROM documents WHERE id = 'doc_legacy'") == {"client_id": "acme",
                                                                                                  "vault_path": old}
    assert _files(vault, "beta") == [] and _files(vault, "acme") == [old] and vault.read(old) == DATA
    assert records.versions(conn, "doc_legacy") == [] and db.rows(conn, "SELECT id FROM document_moves") == []
    report = records.integrity(conn, vault)
    assert report["ok"] is True and report["unreferenced_objects"] == [] and report["deleted_but_still_stored"] == []
    _confirm_and_purge(conn, vault)
    assert _outcomes(conn) == {old: ["started", "deleted"]}
    assert _files(vault, "acme") == [] and _files(vault, "beta") == [] and records.integrity(conn, vault)["ok"] is True


def test_the_sweep_lists_an_old_file_left_by_a_worker_killed_after_the_move_committed(biz, monkeypatch):
    conn, vault = biz.conn, biz.vault
    old = _legacy(conn, vault)
    real = _old_file_stays(monkeypatch, vault, old, Killed())
    with pytest.raises(Killed):
        _move(conn, vault)                                                   # dies between the commit and the removal
    monkeypatch.setattr(vault, "delete", real)
    d = db.one(conn, "SELECT client_id, vault_path FROM documents WHERE id = 'doc_legacy'")
    assert d["client_id"] == "beta" and _files(vault, "beta") == [d["vault_path"]]     # the move had committed
    assert vault.exists(old) and records.versions(conn, "doc_legacy") == []            # no record names the old file
    report = records.integrity(conn, vault)
    assert report["storage_swept"] is True and report["unreferenced_objects"] == [old], report


def test_the_sweep_lists_a_path_addressed_file_a_recorded_deletion_should_have_removed(biz):
    conn, vault = biz.conn, biz.vault
    old = _legacy(conn, vault)
    _confirm_and_purge(conn, vault)
    assert _outcomes(conn) == {old: ["started", "deleted"]} and not vault.exists(old)
    assert records.integrity(conn, vault)["ok"] is True
    vault.write(old, DATA)                                   # put back (a restore of the vault folder from a backup)
    report = records.integrity(conn, vault)
    assert report["deleted_but_still_stored"] == [old] and report["unreferenced_objects"] == []
    assert report["ok"] is False
