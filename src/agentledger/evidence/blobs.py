"""Where evidence bytes live (backlog F-06). Objects are already sealed with the firm's key before they get here, and
their names are keyed content hashes, so the storage provider sees neither content, client names nor document types.

* FileBlobs: a directory (development, single-host self-hosting).
* S3Blobs: any S3-compatible store: Cloudflare R2 in production (ADR-0001), MinIO locally and in CI.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterator, Protocol

# A dot is allowed inside a key (anchor objects are <seq>.json, F-13); ".." and a .tmp suffix (FileBlobs' half-written
# object name) are refused below.
KEY = re.compile(r"^[a-z0-9][a-z0-9/_.-]{0,200}$")
ANCHORS = "anchors"


class BlobStore(Protocol):
    def put(self, key: str, data: bytes) -> None: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
    def keys(self, prefix: str = "") -> Iterator[str]: ...


def _check(key: str) -> str:
    if not KEY.match(key) or ".." in key or key.endswith(".tmp"):
        raise ValueError(f"bad blob key {key!r}")
    return key


class FileBlobs:
    def __init__(self, root: Path | str):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        return self.root / _check(key)

    def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(p)                      # never a half-written object

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def keys(self, prefix: str = "") -> Iterator[str]:
        base = self.root / prefix if prefix else self.root
        if base.exists():
            for p in sorted(base.rglob("*")):
                if p.is_file() and not p.name.endswith(".tmp"):
                    yield p.relative_to(self.root).as_posix()


class S3Blobs:
    """S3-compatible object storage. Configuration: AGENTLEDGER_BLOB_ENDPOINT (for R2:
    https://<account>.r2.cloudflarestorage.com), AGENTLEDGER_BLOB_BUCKET, AGENTLEDGER_BLOB_ACCESS_KEY_ID,
    AGENTLEDGER_BLOB_SECRET_ACCESS_KEY; `prefix` keeps each firm under its own path."""

    def __init__(self, *, bucket: str | None = None, endpoint: str | None = None, access_key: str | None = None,
                 secret_key: str | None = None, prefix: str = "", client: Any = None):
        self.bucket = bucket or os.environ.get("AGENTLEDGER_BLOB_BUCKET", "")
        self.prefix = prefix.strip("/") + "/" if prefix else ""
        if not self.bucket:
            raise ValueError("AGENTLEDGER_BLOB_BUCKET is not set")
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3", endpoint_url=endpoint or os.environ.get("AGENTLEDGER_BLOB_ENDPOINT"), region_name="auto",
                aws_access_key_id=access_key or os.environ.get("AGENTLEDGER_BLOB_ACCESS_KEY_ID"),
                aws_secret_access_key=secret_key or os.environ.get("AGENTLEDGER_BLOB_SECRET_ACCESS_KEY"),
                # Checksums only when an operation requires them: R2 does not document support for the CRC headers
                # boto3 >= 1.36 adds by default. Integrity is ours anyway: objects are AES-GCM sealed and content addressed.
                config=Config(signature_version="s3v4", retries={"max_attempts": 5, "mode": "standard"},
                              connect_timeout=10, read_timeout=30, **_checksum_settings()))
        self.s3 = client

    def _k(self, key: str) -> str:
        return self.prefix + _check(key)

    def put(self, key: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self._k(key), Body=data, ContentType="application/octet-stream")

    def get(self, key: str) -> bytes:
        return self.s3.get_object(Bucket=self.bucket, Key=self._k(key))["Body"].read()

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._k(key))
            return True
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._k(key))

    def keys(self, prefix: str = "") -> Iterator[str]:
        token = None
        while True:
            kw: dict[str, Any] = {"Bucket": self.bucket, "Prefix": self.prefix + prefix}
            if token:
                kw["ContinuationToken"] = token
            page = self.s3.list_objects_v2(**kw)
            for o in page.get("Contents", []):
                yield o["Key"][len(self.prefix):]
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")


def _checksum_settings() -> dict[str, str]:
    """Checksums only when an operation requires them (botocore >= 1.36 adds CRC headers R2 does not document by
    default; older botocore sends none and does not know these settings)."""
    import botocore

    major, minor = (int(x) for x in botocore.__version__.split(".")[:2])
    if (major, minor) < (1, 36):
        return {}
    return {"request_checksum_calculation": "when_required", "response_checksum_validation": "when_required"}


def for_firm(root: Path, firm_id: str | None) -> BlobStore:
    """The configured store for one firm: AGENTLEDGER_BLOBS=s3 uses the bucket under the prefix firms/<firm>/."""
    if os.environ.get("AGENTLEDGER_BLOBS", "file").strip().lower() == "s3":
        return S3Blobs(prefix=f"{os.environ.get('AGENTLEDGER_BLOB_PREFIX', '')}firms/{firm_id or 'dev'}")
    return FileBlobs(root)


def anchors_for_firm(root: Path, firm_id: str | None) -> tuple[BlobStore, str]:
    """The store a firm's audit chain anchors go to (evidence/anchors.py, F-13), and its label. With
    AGENTLEDGER_BLOBS=s3 the bucket under the top-level prefix anchors/<firm>/ (not under firms/<firm>/: one bucket
    lock rule on anchors/ then covers every firm, and offboarding, which removes firms/<firm>/, leaves the locked
    anchors alone; they hold hashes, never content). Otherwise `root`, a directory beside the vault, never inside it:
    the integrity sweep treats files under the vault as documents."""
    if os.environ.get("AGENTLEDGER_BLOBS", "file").strip().lower() == "s3":
        store = S3Blobs(prefix=f"{os.environ.get('AGENTLEDGER_BLOB_PREFIX', '')}{ANCHORS}/{firm_id or 'dev'}")
        return store, f"s3:{store.bucket}/{store.prefix}"
    return FileBlobs(root), f"file:{Path(root)}"


def location_of(firm_id: str) -> str:
    """Where a firm's objects live under the current settings: 'file' (inside the tenant directory) or
    's3:<bucket>/<prefix>'. Recorded when the firm is created; offboarding refuses to run under other settings."""
    if os.environ.get("AGENTLEDGER_BLOBS", "file").strip().lower() == "s3":
        store = for_firm(Path("."), firm_id)
        return f"s3:{getattr(store, 'bucket', '')}/{getattr(store, 'prefix', '')}"
    return "file"


def destroy_firm(firm_id: str) -> str:
    """Delete every object of a firm in the configured object store (offboarding; its key is already shredded)."""
    if os.environ.get("AGENTLEDGER_BLOBS", "file").strip().lower() != "s3":
        return "no object store (file blobs go with the tenant directory)"
    store = for_firm(Path("."), firm_id)
    n = 0
    for key in list(store.keys()):
        store.delete(key)
        n += 1
    return f"{n} object(s) deleted from the object store"
