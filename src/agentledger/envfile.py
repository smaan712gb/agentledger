"""Load a local `.env` file (git-ignored) into the environment for development.

Values already set in the real environment win, so CI secrets and production secrets are never overridden by a
stray file. Production on Cloudflare uses Worker/Container secrets instead of a file.
"""

from __future__ import annotations

import os
from pathlib import Path


def load(root: Path) -> list[str]:
    path = Path(root) / ".env"
    if not path.exists():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
