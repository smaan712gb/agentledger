"""The evidence vault (backlog F-06). Inside a firm every object is sealed with that firm's data key (AES-256-GCM,
see crypto.Keyring) before it reaches storage; without a keyring (single-firm dev) objects are plain.

New evidence is content addressed: `put(data)` returns a locator `blob:cas/<ab>/<keyed hash>`, where the hash is an
HMAC of the plaintext under a per-firm key, so identical uploads are stored once and the storage provider (R2 in
production) learns neither content, client names nor document types from object names. Bytes go to a pluggable
store (evidence.blobs: a directory, or S3-compatible R2/MinIO).

Documents filed before content addressing keep their relative paths; `read`, `move` and `exists` accept both.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

from .crypto import MAGIC, Keyring

BLOB = "blob:"


class Vault:
    def __init__(self, root: Path, keyring: Keyring | None = None, firm_id: str | None = None, blobs: Any = None):
        self.root = Path(root)
        self.keyring = keyring
        self.firm_id = firm_id
        if keyring is not None and not firm_id:
            raise ValueError("an encrypted vault needs the firm id")
        if blobs is None:
            from ..evidence.blobs import for_firm

            blobs = for_firm(self.root / "blobs", firm_id)
        self.blobs = blobs

    def _firm(self) -> str:
        if not self.firm_id:  # guaranteed by __init__ whenever a keyring is set
            raise ValueError("an encrypted vault needs the firm id")
        return self.firm_id

    @property
    def encrypted(self) -> bool:
        return self.keyring is not None

    # ------------------------------------------------------------------ content-addressed evidence
    def address(self, data: bytes) -> str:
        key = self.keyring.derive(self._firm(), "content-address") if self.keyring else b"agentledger:dev-content-address"
        digest = hmac.new(key, data, hashlib.sha256).hexdigest()
        return f"{BLOB}cas/{digest[:2]}/{digest}"

    def put(self, data: bytes) -> str:
        """Store bytes once; returns their locator."""
        loc = self.address(data)
        key = loc.removeprefix(BLOB)
        if not self.blobs.exists(key):
            self.blobs.put(key, self.keyring.encrypt(self._firm(), data, "vault") if self.keyring else data)
        return loc

    def delete(self, loc: str) -> None:
        """Remove stored bytes (retention deletion only; see evidence.records.purge_expired)."""
        if loc.startswith(BLOB):
            self.blobs.delete(loc.removeprefix(BLOB))
        else:
            self.path(loc).unlink(missing_ok=True)

    def _open(self, raw: bytes) -> bytes:
        if raw[:3] == MAGIC:
            if not self.keyring:
                raise PermissionError("this document is encrypted and no firm key is available")
            return self.keyring.decrypt(self._firm(), raw, "vault")
        return raw

    # ------------------------------------------------------------------ both kinds
    def path(self, rel: str | Path) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError("path escapes the vault")
        return p

    def write(self, rel: str | Path, data: bytes) -> Path:
        """Path-addressed write (legacy and non-evidence files such as exports)."""
        dest = self.path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.keyring.encrypt(self._firm(), data, "vault") if self.keyring else data)
        return dest

    def read(self, loc: str | Path) -> bytes:
        loc = str(loc)
        if loc.startswith(BLOB):
            return self._open(self.blobs.get(loc.removeprefix(BLOB)))
        return self._open(self.path(loc).read_bytes())

    def move(self, src: str | Path, dst: str | Path) -> None:
        if str(src).startswith(BLOB):
            return                         # content-addressed evidence never moves; only its record changes
        target = self.path(dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path(src).replace(target)

    def exists(self, loc: str | Path) -> bool:
        loc = str(loc)
        if loc.startswith(BLOB):
            return self.blobs.exists(loc.removeprefix(BLOB))
        return self.path(loc).exists()


def as_vault(v: "Vault | Path | str") -> Vault:
    return v if isinstance(v, Vault) else Vault(Path(v))
