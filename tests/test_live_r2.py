"""Live check of the evidence store on Cloudflare R2 (re-audit of fe75514: "live R2 still needs validation").

Opt-in (AGENTLEDGER_TEST_R2=1) and never in CI: it uses the R2 settings in the developer's .env explicitly (the test
suite pins every other test away from them), writes one sealed object under a unique selftest/ prefix, reads it back
authenticated, checks that a substituted object is refused, deletes it, and checks nothing is left. Credentials are
never printed.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

import pytest

from agentledger import envfile
from agentledger.evidence.blobs import S3Blobs
from agentledger.security.vault import Vault, VaultIntegrityError
from test_evidence import keyring  # noqa: F401  (fixture)

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.environ.get("AGENTLEDGER_TEST_R2") != "1", reason="live R2 check: set AGENTLEDGER_TEST_R2=1")
def test_r2_round_trip_with_authenticated_reads(tmp_path, keyring):  # noqa: F811
    env = envfile.values(REPO)
    needed = ("AGENTLEDGER_BLOB_ENDPOINT", "AGENTLEDGER_BLOB_BUCKET", "AGENTLEDGER_BLOB_ACCESS_KEY_ID",
              "AGENTLEDGER_BLOB_SECRET_ACCESS_KEY")
    missing = [k for k in needed if not env.get(k)]
    assert not missing, f"set in .env: {', '.join(missing)}"
    prefix = f"selftest/{secrets.token_hex(6)}"
    store = S3Blobs(bucket=env["AGENTLEDGER_BLOB_BUCKET"], endpoint=env["AGENTLEDGER_BLOB_ENDPOINT"],
                    access_key=env["AGENTLEDGER_BLOB_ACCESS_KEY_ID"], secret_key=env["AGENTLEDGER_BLOB_SECRET_ACCESS_KEY"],
                    prefix=prefix)
    keyring.create("selftest-cpa")
    vault = Vault(tmp_path, keyring, "selftest-cpa", blobs=store)
    a, b = b"selftest document A " + secrets.token_bytes(16), b"selftest document B " + secrets.token_bytes(16)
    try:
        la, lb = vault.put(a, owner="doc_a"), vault.put(b, owner="doc_b")
        assert vault.exists(la) and vault.read(la) == a and vault.read(lb) == b
        assert a not in store.get(la.removeprefix("blob:"))                     # sealed before it left the process
        store.put(la.removeprefix("blob:"), store.get(lb.removeprefix("blob:")))  # substitute B's ciphertext for A's
        with pytest.raises(VaultIntegrityError):
            vault.read(la)
    finally:
        for key in list(store.keys()):
            store.delete(key)
    assert list(store.keys()) == [] and not vault.exists(la)
