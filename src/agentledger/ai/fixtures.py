"""Scripted document-extraction answers for the local demo and the end-to-end run (backlog F-10, slice 2).

Intake asks the model router to classify an uploaded document and copy its box amounts (intake/pipeline.py). A
browser test that needs a filled-in W-2 cannot depend on a model, so the router can be told to answer from fixtures
instead: AGENTLEDGER_AI_FIXTURES names a directory holding the fixture documents and `fixtures.json`, which maps the
SHA-256 of each document's bytes to the structured answer (intake's `Classification`) a model would have given:

    {"<sha256 of w2.pdf>": {"file": "w2.pdf", "response": {"doc_type": "W-2", "tax_year": 2026, "party_names": [...],
                                                           "tin_last4": ["0009"], "fields": {"box1": "61200.00", ...},
                                                           "summary": "...", "confidence": 0.97}}}

The router never sees the file: it is shown a prompt carrying the document's extracted text, or the image bytes of a
scanned document. So each fixture is indexed under every hash a call may present: the bytes themselves (an image), and
the text intake extracts from them (`intake.extract.explode`, the same code path, so a PDF's text layer and a .txt
file's bytes both match). The answer is validated against the schema the caller asks for and returned as
"fixture:<hash>". A document without a fixture is `Unavailable`, exactly as an unreachable model is: intake falls back
to its deterministic detectors and nothing is ever guessed. `fields` may be written as a mapping; it becomes the
`{name, value}` list the schema wants.

Fixtures are refused outside the local demo and the end-to-end run. Like dev mode itself (api/app.py `DEV`, which
must never be enabled where real taxpayer data lives), the variable is honoured only with AGENTLEDGER_DEV_AUTH=1 or
with AGENTLEDGER_E2E=1 (set by the Playwright launcher, apps/web/e2e/servers.mjs). Set anywhere else, the router
refuses to start rather than answer extraction from a script.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, TypeVar

from pydantic import BaseModel

ENV = "AGENTLEDGER_AI_FIXTURES"
E2E_FLAG = "AGENTLEDGER_E2E"
DEV_FLAG = "AGENTLEDGER_DEV_AUTH"
MAPPING = "fixtures.json"
PROMPT_TEXT = 30000                                   # intake/pipeline.py puts part.text[:30000] in the prompt
_DOCUMENT = re.compile(r"<document>\n(.*)\n</document>", re.S)

M = TypeVar("M", bound=BaseModel)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def allowed(environ: Mapping[str, str] | None = None) -> bool:
    """Where fixtures may answer: the local demo (dev auth) or an end-to-end run; nowhere else."""
    env = os.environ if environ is None else environ
    return env.get(DEV_FLAG) == "1" or env.get(E2E_FLAG) == "1"


@dataclass(frozen=True)
class Fixture:
    sha256: str
    answer: dict[str, Any]
    file: str | None


def _as_schema_payload(answer: dict[str, Any]) -> dict[str, Any]:
    """`fields` written as a mapping becomes the list of {name, value} intake's Classification declares."""
    fields = answer.get("fields")
    if isinstance(fields, dict):
        return {**answer, "fields": [{"name": str(k), "value": str(v)} for k, v in fields.items()]}
    return answer


def _hashes_of(name: str, data: bytes) -> Iterator[str]:
    """Every hash a router call may present for this document: its bytes, the text intake extracts (as the prompt
    carries it), and each image of a scanned document."""
    yield sha256(data)
    from ..intake.extract import explode      # intake imports the router; imported here, at load time, not at import

    for part in explode(name, data):
        if part.text:
            yield sha256(part.text[:PROMPT_TEXT].encode("utf-8"))
        for image in part.images:
            yield sha256(image)


class FixtureRouter:
    """Answers `structured` calls from a directory of fixtures; everything else is unavailable."""

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        self.fixtures: dict[str, Fixture] = {}            # by the sha256 of the document's bytes (the mapping's key)
        self._index: dict[str, Fixture] = {}              # by every hash a call may present
        self._load()

    def _load(self) -> None:
        path = self.directory / MAPPING
        if not path.is_file():
            raise RuntimeError(f"{ENV}: {path} is missing")
        try:
            mapping = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as e:
            raise RuntimeError(f"{ENV}: {path} is not valid JSON ({e})") from None
        if not isinstance(mapping, dict):
            raise RuntimeError(f"{ENV}: {path} must map sha256 -> fixture")
        for key, entry in mapping.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("response"), dict):
                raise RuntimeError(f"{ENV}: fixture {key} needs a 'response' object")
            file = entry.get("file")
            fixture = Fixture(str(key), dict(entry["response"]), str(file) if file else None)
            self.fixtures[fixture.sha256] = fixture
            self._index[fixture.sha256] = fixture
            if fixture.file:
                data = (self.directory / fixture.file).read_bytes()
                if sha256(data) != fixture.sha256:
                    raise RuntimeError(f"{ENV}: {fixture.file} does not hash to {fixture.sha256}; regenerate {MAPPING}")
                for h in _hashes_of(fixture.file, data):
                    self._index[h] = fixture

    def lookup(self, user: str, images: list[bytes] | None = None) -> Fixture | None:
        """The fixture for what a call shows: an image's bytes, or the document text inside the prompt."""
        for image in images or []:
            found = self._index.get(sha256(image))
            if found:
                return found
        m = _DOCUMENT.search(user)
        if m:
            return self._index.get(sha256(m.group(1).encode("utf-8")))
        return None

    def structured(self, role: str, *, system: str, user: str, schema: type[M], images: list[bytes] | None = None,
                   escalate: bool = False, client_id: str | None = None, effort: str = "medium",
                   data_class: str = "taxpayer") -> tuple[M, str]:
        from .router import Unavailable

        fixture = self.lookup(user, images)
        if fixture is None:
            raise Unavailable(f"role {role}: no extraction fixture matches this document")
        return schema.model_validate(_as_schema_payload(fixture.answer)), f"fixture:{fixture.sha256[:12]}"

    def stream(self, role: str, *, system: str, messages: list[dict[str, Any]], client_id: str | None = None,
               data_class: str = "taxpayer") -> tuple[Iterator[str], str]:
        from .router import Unavailable

        raise Unavailable(f"role {role}: fixtures answer structured extraction only")

    def frontier_allowed(self) -> bool:
        return False

    def describe(self) -> dict[str, Any]:
        return {"directory": str(self.directory), "documents": len(self.fixtures)}


def from_env(environ: Mapping[str, str] | None = None) -> FixtureRouter | None:
    """The fixture router the environment asks for, None when it asks for none. Asking for one where it is not allowed
    is a misconfiguration the process refuses, loudly."""
    env = os.environ if environ is None else environ
    where = env.get(ENV, "").strip()
    if not where:
        return None
    if not allowed(env):
        raise RuntimeError(f"{ENV} is set, but extraction fixtures are honoured only with {DEV_FLAG}=1 (the local demo) or "
                           f"{E2E_FLAG}=1 (the end-to-end run): a deployment never answers document extraction from a script")
    return FixtureRouter(where)
