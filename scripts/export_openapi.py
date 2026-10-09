"""Write the API's OpenAPI document to packages/contracts/openapi.json without a running server.

    python scripts/export_openapi.py            # write it: keys sorted, two-space indent, LF line endings
    python scripts/export_openapi.py --check    # exit 1 when the committed file differs (the CI drift gate)

The application module opens the platform store and the knowledge base when it is imported, so it is imported inside
a throwaway AGENTLEDGER_HOME holding copies of rules/, config/, domains/, playbooks/, golden/ and evals/ (what
tests/conftest.py's `home` fixture copies), with the test suite's environment pins: SQLite stores, local identity,
file blobs, no models, no agents, a random master key, and every service variable blanked so nothing from a
developer's environment is reached (.env is never read: the home is the temporary directory). Nothing under the
repository is written except the target file.

The document must have one operation id per route (the handler's name; app.py sets `generate_unique_id_function`),
because the generated TypeScript (`npm run -w packages/contracts generate`) keys its operations by it.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
TARGET = REPO / "packages" / "contracts" / "openapi.json"
COPIED = ("rules", "config", "golden", "domains", "playbooks", "evals")
BLANKED = ("AGENTLEDGER_BLOB_ENDPOINT", "AGENTLEDGER_BLOB_BUCKET", "AGENTLEDGER_BLOB_ACCESS_KEY_ID", "AGENTLEDGER_BLOB_SECRET_ACCESS_KEY",
           "AGENTLEDGER_BLOB_PREFIX", "WORKOS_REDIRECT_URI", "WORKOS_API_KEY", "WORKOS_CLIENT_ID", "DATABASE_URL", "DATABASE_URL_UNPOOLED",
           "AGENTLEDGER_MIGRATION_URL", "AGENTLEDGER_RUNTIME_DATABASE_URL", "NEON_API_KEY", "NEON_PROJECT_ID")
REMOVED = ("AGENTLEDGER_DEV_AUTH", "AGENTLEDGER_BUILD", "AGENTLEDGER_SMOKE_TOKEN", "ANTHROPIC_API_KEY", "AGENTLEDGER_WEBHOOK_SECRET",
           "AGENTLEDGER_PG_SCHEMA_PREFIX")


def pinned_environment(home: Path) -> dict[str, str]:
    return {
        "AGENTLEDGER_HOME": str(home),
        "AGENTLEDGER_MASTER_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "AGENTLEDGER_DATABASE": "sqlite",
        "AGENTLEDGER_PLATFORM_DATABASE": "sqlite",
        "AGENTLEDGER_ALLOW_SQLITE": "1",
        "AGENTLEDGER_IDENTITY": "local",
        "AGENTLEDGER_BLOBS": "file",
        "AGENTLEDGER_OLLAMA_URL": "http://127.0.0.1:9",
        "AGENTLEDGER_AGENTS": "0",
    }


def operation_ids(document: dict[str, Any]) -> list[str]:
    return [op["operationId"] for methods in document.get("paths", {}).values() for op in methods.values()
            if isinstance(op, dict) and "operationId" in op]


def build_document() -> dict[str, Any]:
    """Import the application in a throwaway home and return `app.openapi()`."""
    tmp = Path(tempfile.mkdtemp(prefix="agentledger-openapi-"))
    try:
        home = tmp / "home"
        home.mkdir()
        for name in COPIED:
            shutil.copytree(REPO / name, home / name)
        shutil.copy(REPO / "pyproject.toml", home / "pyproject.toml")
        os.environ.update(pinned_environment(home))
        for name in BLANKED:
            os.environ[name] = ""
        for name in REMOVED:
            os.environ.pop(name, None)
        sys.path.insert(0, str(REPO / "src"))
        import agentledger.api.app as api_mod

        document = api_mod.app.openapi()
        ids = operation_ids(document)
        duplicates = sorted({i for i in ids if ids.count(i) > 1})
        if duplicates:
            raise SystemExit(f"duplicate operation ids (two handlers share a name): {', '.join(duplicates)}")
        api_mod.PLATFORM.close()
        api_mod.APP.conn.close()
        return document
    finally:
        shutil.rmtree(tmp, ignore_errors=True)   # SQLite files still open elsewhere are left to the OS temp cleanup


def render(document: dict[str, Any]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    text = render(build_document())
    paths = len(json.loads(text).get("paths", {}))
    if check:
        committed = TARGET.read_text(encoding="utf-8").replace("\r\n", "\n") if TARGET.exists() else ""
        if committed != text:
            print(f"{TARGET.relative_to(REPO)} is out of date with the API: run `python scripts/export_openapi.py` and "
                  "`npm run -w packages/contracts generate`, then commit both.", file=sys.stderr)
            return 1
        print(f"{TARGET.relative_to(REPO)} matches the API ({paths} paths).")
        return 0
    TARGET.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TARGET.relative_to(REPO)}: {paths} paths")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
