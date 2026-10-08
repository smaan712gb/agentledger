"""Envelope encryption: AES-256-GCM with a data key per firm, wrapped by a master key.

The master key never touches the database. It comes from VERITAS_MASTER_KEY (base64 of 32
bytes), normally injected from a secrets manager or KMS. Each firm's data key is generated
randomly, wrapped with the master key and stored in the platform database. Destroying a
firm's wrapped key renders all of its ciphertext unreadable (crypto-shredding), which is how
a firm's data is deleted on request.

Ciphertext layout: b"VX1" | key_version (2 bytes) | nonce (12 bytes) | ciphertext+tag.
The associated data binds a ciphertext to its firm and purpose, so a blob copied from one
firm or field to another fails authentication instead of decrypting.
"""

from __future__ import annotations

import base64
import os
import secrets
import sqlite3
import threading
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"VX1"


class CryptoError(Exception):
    pass


def load_master_key(state_dir: Path, *, allow_dev_file: bool) -> bytes:
    env = os.environ.get("VERITAS_MASTER_KEY")
    if env:
        key = base64.b64decode(env)
        if len(key) != 32:
            raise CryptoError("VERITAS_MASTER_KEY must be base64 of exactly 32 bytes")
        return key
    if not allow_dev_file:
        raise CryptoError("VERITAS_MASTER_KEY is not set; refusing to start without a master key")
    path = Path(state_dir) / "master.key.dev"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64encode(secrets.token_bytes(32)))
    return base64.b64decode(path.read_bytes())


def _seal(key: bytes, plaintext: bytes, aad: bytes, version: int) -> bytes:
    nonce = secrets.token_bytes(12)
    return MAGIC + version.to_bytes(2, "big") + nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _open(key_for_version, blob: bytes, aad: bytes) -> bytes:
    if blob[:3] != MAGIC:
        raise CryptoError("not a Veritas ciphertext")
    version = int.from_bytes(blob[3:5], "big")
    return AESGCM(key_for_version(version)).decrypt(blob[5:17], blob[17:], aad)


class Keyring:
    """Per-firm data keys, stored wrapped in the platform database (table firm_keys)."""

    def __init__(self, conn: sqlite3.Connection, master: bytes):
        self.conn = conn
        self.master = master
        self._cache: dict[tuple[str, int], bytes] = {}
        self._lock = threading.Lock()

    def _wrap_aad(self, firm_id: str, version: int) -> bytes:
        return f"firm-key:{firm_id}:{version}".encode()

    def create(self, firm_id: str) -> int:
        with self._lock:
            row = self.conn.execute("SELECT MAX(version) FROM firm_keys WHERE firm_id = ?", (firm_id,)).fetchone()
            version = (row[0] or 0) + 1
            dek = secrets.token_bytes(32)
            wrapped = _seal(self.master, dek, self._wrap_aad(firm_id, version), 1)
            self.conn.execute("INSERT INTO firm_keys (firm_id, version, wrapped) VALUES (?, ?, ?)", (firm_id, version, wrapped))
            self.conn.commit()
            self._cache[(firm_id, version)] = dek
            return version

    def current_version(self, firm_id: str) -> int:
        row = self.conn.execute("SELECT MAX(version) FROM firm_keys WHERE firm_id = ? AND destroyed_at IS NULL",
                                (firm_id,)).fetchone()
        if not row or row[0] is None:
            raise CryptoError(f"firm {firm_id} has no active data key")
        return row[0]

    def key(self, firm_id: str, version: int) -> bytes:
        k = self._cache.get((firm_id, version))
        if k is not None:
            return k
        row = self.conn.execute("SELECT wrapped FROM firm_keys WHERE firm_id = ? AND version = ? AND destroyed_at IS NULL",
                                (firm_id, version)).fetchone()
        if not row:
            raise CryptoError(f"data key {version} for firm {firm_id} is missing or destroyed")
        dek = _open(lambda _v: self.master, bytes(row[0]), self._wrap_aad(firm_id, version))
        self._cache[(firm_id, version)] = dek
        return dek

    def encrypt(self, firm_id: str, plaintext: bytes, purpose: str) -> bytes:
        version = self.current_version(firm_id)
        return _seal(self.key(firm_id, version), plaintext, f"{firm_id}:{purpose}".encode(), version)

    def decrypt(self, firm_id: str, blob: bytes, purpose: str) -> bytes:
        return _open(lambda v: self.key(firm_id, v), blob, f"{firm_id}:{purpose}".encode())

    def seal_text(self, firm_id: str, text: str, purpose: str) -> str:
        return base64.b64encode(self.encrypt(firm_id, text.encode("utf-8"), purpose)).decode("ascii")

    def open_text(self, firm_id: str, token: str, purpose: str) -> str:
        return self.decrypt(firm_id, base64.b64decode(token), purpose).decode("utf-8")

    def rotate(self, firm_id: str) -> int:
        """New data key for new writes; older versions stay readable until re-encrypted."""
        return self.create(firm_id)

    def destroy(self, firm_id: str) -> None:
        """Crypto-shred: every ciphertext for this firm becomes permanently unreadable."""
        with self._lock:
            self.conn.execute("UPDATE firm_keys SET wrapped = X'', destroyed_at = datetime('now') WHERE firm_id = ?", (firm_id,))
            self.conn.commit()
            for k in [k for k in self._cache if k[0] == firm_id]:
                del self._cache[k]
