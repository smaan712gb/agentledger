"""Document vault on disk. Inside a firm every file is sealed with that firm's data key
(AES-256-GCM, see crypto.Keyring); without a keyring (single-firm dev) files are plain.
Reads accept both, so a vault can be migrated to encryption in place."""

from __future__ import annotations

from pathlib import Path

from .crypto import MAGIC, Keyring


class Vault:
    def __init__(self, root: Path, keyring: Keyring | None = None, firm_id: str | None = None):
        self.root = Path(root)
        self.keyring = keyring
        self.firm_id = firm_id
        if keyring is not None and not firm_id:
            raise ValueError("an encrypted vault needs the firm id")

    @property
    def encrypted(self) -> bool:
        return self.keyring is not None

    def path(self, rel: str | Path) -> Path:
        p = (self.root / rel).resolve()
        if self.root.resolve() not in p.parents and p != self.root.resolve():
            raise ValueError("path escapes the vault")
        return p

    def write(self, rel: str | Path, data: bytes) -> Path:
        dest = self.path(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.keyring.encrypt(self.firm_id, data, "vault") if self.keyring else data)
        return dest

    def read(self, rel: str | Path) -> bytes:
        raw = self.path(rel).read_bytes()
        if raw[:3] == MAGIC:
            if not self.keyring:
                raise PermissionError("this document is encrypted and no firm key is available")
            return self.keyring.decrypt(self.firm_id, raw, "vault")
        return raw

    def move(self, src: str | Path, dst: str | Path) -> None:
        target = self.path(dst)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.path(src).replace(target)

    def exists(self, rel: str | Path) -> bool:
        return self.path(rel).exists()


def as_vault(v: "Vault | Path | str") -> Vault:
    return v if isinstance(v, Vault) else Vault(Path(v))
