"""The evidence vault (backlog F-06). Inside a firm every object is sealed with that firm's data key (AES-256-GCM,
see crypto.Keyring) before it reaches storage; without a keyring (single-firm dev) objects are plain.

New evidence is content addressed per document: `put(data, owner=document_id)` returns a locator
`blob:cas/<ab>/<keyed hash>`, where the hash is an HMAC of the document id and the plaintext under a per-firm key. A
document's versions with the same bytes share an object; two documents never do, so deleting one document's evidence
can never take another's (re-audit of 952ee96). The storage provider (R2 in production) learns neither content,
client names nor document types from object names. Bytes go to a pluggable store (evidence.blobs: a directory, or
S3-compatible R2/MinIO). Objects stored before per-document addressing (`put(data)` without an owner) may be shared;
evidence.records keeps them while any referencing document is retained or held.

Documents filed before content addressing keep their relative paths; `read`, `move` and `exists` accept both.

Integrity (re-audit of fe75514): each object is sealed (AES-256-GCM) with its own locator as associated data, so
another object's valid ciphertext put in its place fails authentication; an encrypted vault never accepts unsealed
bytes; an object sealed before that binding must match its content address; and a read given the document's SHA-256
checks the plaintext against it. Any mismatch raises VaultIntegrityError: the bytes are never returned.
"""

from __future__ import annotations

import hashlib
import hmac
from pathlib import Path
from typing import Any

from .crypto import MAGIC, Keyring

BLOB = "blob:"
LEGACY_PURPOSE = "vault"                 # objects sealed before each was bound to its locator


class VaultIntegrityError(Exception):
    """The stored bytes are not the ones the record names: replaced, swapped with another object, or unsealed."""


def _purpose(key: str) -> str:
    return f"vault:{key}"


def _rel(loc: str | Path) -> str:
    return Path(str(loc)).as_posix()


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
    def address(self, data: bytes, owner: str | None = None) -> str:
        key = self.keyring.derive(self._firm(), "content-address") if self.keyring else b"agentledger:dev-content-address"
        digest = hmac.new(key, (f"{owner}\x00".encode() if owner else b"") + data, hashlib.sha256).hexdigest()
        return f"{BLOB}cas/{digest[:2]}/{digest}"

    def put(self, data: bytes, owner: str | None = None) -> str:
        """Store bytes once (per owning document); returns their locator."""
        loc = self.address(data, owner)
        key = loc.removeprefix(BLOB)
        if not self.blobs.exists(key):
            self.blobs.put(key, self.keyring.encrypt(self._firm(), data, _purpose(key)) if self.keyring else data)
        return loc

    def delete(self, loc: str) -> None:
        """Remove stored bytes (retention deletion only; see evidence.records.purge_expired)."""
        if loc.startswith(BLOB):
            self.blobs.delete(loc.removeprefix(BLOB))
        else:
            self.path(loc).unlink(missing_ok=True)

    def _open(self, raw: bytes, key: str) -> tuple[bytes, bool]:
        """(plaintext, bound): bound when the object was sealed for this very locator."""
        if raw[:3] != MAGIC:
            if self.keyring:
                raise VaultIntegrityError("the stored object is not sealed: an encrypted vault never accepts plain bytes")
            return raw, False
        if not self.keyring:
            raise PermissionError("this document is encrypted and no firm key is available")
        try:
            return self.keyring.decrypt(self._firm(), raw, _purpose(key)), True
        except Exception:
            pass
        try:                                      # sealed before objects were bound to their locator
            return self.keyring.decrypt(self._firm(), raw, LEGACY_PURPOSE), False
        except Exception as exc:
            raise VaultIntegrityError("the stored object was not sealed for this locator (replaced or swapped)") from exc

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
        dest.write_bytes(self.keyring.encrypt(self._firm(), data, _purpose(_rel(rel))) if self.keyring else data)
        return dest

    def read(self, loc: str | Path, *, sha256: str | None = None) -> bytes:
        """The plaintext of a stored object, authenticated: sealed for this locator (or, sealed before that binding, the
        object its content address names), and, with `sha256` (the document's or version's recorded hash), exactly the
        document's bytes. Raises VaultIntegrityError otherwise; nothing unauthenticated is ever returned."""
        loc = str(loc)
        if loc.startswith(BLOB):
            key = loc.removeprefix(BLOB)
            data, bound = self._open(self.blobs.get(key), key)
            if not bound and self.keyring and self.address(data) != loc:
                raise VaultIntegrityError("the stored object does not match its content address (replaced or swapped)")
        else:
            data, _ = self._open(self.path(loc).read_bytes(), _rel(loc))
        if sha256 is not None and hashlib.sha256(data).hexdigest() != sha256:
            raise VaultIntegrityError("the stored bytes are not the document's (their hash does not match the record)")
        return data

    def copy(self, src: str | Path, dst: str | Path) -> None:
        """Copy a path-addressed document (filed before content addressing) to a new path, sealed for that path."""
        self.write(dst, self.read(src))

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
