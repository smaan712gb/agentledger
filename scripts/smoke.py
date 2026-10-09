"""Post-deploy smoke checks against one deployment (release.yml runs them after `wrangler deploy`; the production
soak repeats them). Nothing is created, changed or deleted: the smoke principal is refused everywhere except
GET /api/coverage and POST /api/returns/individual.

    python scripts/smoke.py https://staging-api.example.com

Environment:
    SMOKE_TOKEN         the deployment's AGENTLEDGER_SMOKE_TOKEN (32+ characters)
    EXPECTED_BUILD      the commit SHA the image was built from (release.yml: $GITHUB_SHA); unset accepts any build
    SMOKE_WAIT_SECONDS  how long to wait for /healthz to report that build on 3 consecutive polls (default 600):
                        `wrangler deploy` returns before the container rollout finishes
    SMOKE_DEV=1         the target runs `agentledger serve --dev`, whose demo identity picker (GET /api/users) is on

Against a local server:
    AGENTLEDGER_SMOKE_TOKEN=$(python -c "import secrets; print(secrets.token_urlsafe(32))") agentledger serve --dev
    SMOKE_TOKEN=<the same value> SMOKE_DEV=1 SMOKE_WAIT_SECONDS=30 python scripts/smoke.py http://127.0.0.1:8740

Every check prints one line; the exit status is 1 when any of them failed.
"""

from __future__ import annotations

import os
import secrets
import sys
import time

import httpx

TIMEOUT = httpx.Timeout(60.0, connect=10.0)
POLL_SECONDS = 10
CONSECUTIVE = 3
# A single filer with one W-2; Form 1040 line 16 is the Rev. Proc. 2025-32 Tax Table value (tests/test_returns_1040.py).
SAMPLE = {"tax_year": 2026, "filing_status": "single", "taxpayer": {"ssn": "400-00-0001", "dob": "1985-06-01"},
          "w2s": [{"wages": 60000, "federal_withholding": 6000}]}
LINE_16 = "5023"


class Failed(Exception):
    pass


def _body(r: httpx.Response) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise Failed(f"{r.request.method} {r.request.url.path}: {r.status_code} with a non-JSON body: {r.text[:200]!r}")
    return data if isinstance(data, dict) else {"_": data}


def check_health(c: httpx.Client, expected: str | None, wait: int) -> str:
    """/healthz answers 200, ok, with the expected build, CONSECUTIVE polls in a row (every instance has rolled)."""
    deadline = time.monotonic() + wait
    streak, last = 0, "not polled yet"
    while True:
        try:
            r = c.get("/healthz")
            body = _body(r) if r.status_code == 200 else {}
            last = f"{r.status_code} {r.text[:200]!r}"
        except (httpx.HTTPError, Failed) as e:
            body, last = {}, f"{type(e).__name__}: {e}"
        if body.get("ok") is True and (expected is None or body.get("build") == expected):
            streak += 1
            if streak >= CONSECUTIVE:
                return f"build {body.get('build')} on {body.get('backend')} ({CONSECUTIVE} consecutive polls)"
        else:
            streak = 0
        if time.monotonic() >= deadline:
            want = f"build {expected}" if expected else "ok"
            raise Failed(f"/healthz did not report {want} {CONSECUTIVE} times in a row within {wait}s; last answer: {last}")
        time.sleep(POLL_SECONDS)


def check_auth_config(c: httpx.Client, dev: bool) -> str:
    r = c.get("/api/auth/config")
    if r.status_code != 200:
        raise Failed(f"GET /api/auth/config answered {r.status_code}: {r.text[:200]!r}")
    identity = _body(r).get("identity")
    r = c.get("/api/users")
    if dev:
        if r.status_code != 200:
            raise Failed(f"GET /api/users answered {r.status_code} on a --dev server (its identity picker should be on)")
        picker = "demo identity picker on (SMOKE_DEV=1)"
    elif r.status_code != 404:
        raise Failed(f"GET /api/users answered {r.status_code}: the demo identity picker must not exist outside --dev")
    else:
        picker = "no demo identity picker"
    return f"identity {identity}; {picker}"


def check_login_is_generic(c: httpx.Client) -> str:
    email = f"nobody-{secrets.token_hex(6)}@smoke.invalid"
    r = c.post("/api/auth/login", json={"email": email, "password": secrets.token_urlsafe(16)})
    if r.status_code != 401:
        raise Failed(f"POST /api/auth/login with an unknown account answered {r.status_code}, not 401: {r.text[:200]!r}")
    detail = str(_body(r).get("detail", ""))
    low = detail.lower()
    if email in low or "no such" in low or "not found" in low or "unknown" in low:
        raise Failed(f"the sign-in error reveals whether the account exists: {detail!r}")
    return f"unknown account -> 401 {detail!r}"


def check_idp_start(c: httpx.Client, identity: str | None) -> str:
    r = c.get("/api/auth/idp/start")
    if identity == "workos":
        if r.status_code != 200:
            raise Failed(f"GET /api/auth/idp/start answered {r.status_code} with identity workos: {r.text[:200]!r}")
        url = str(_body(r).get("url", ""))
        if not url.startswith("https://api.workos.com/"):
            raise Failed(f"the hosted sign-in URL does not point at WorkOS: {url[:120]!r}")
        return f"hosted sign-in starts at {url.split('?')[0]}"
    # Not WorkOS: hosted sign-in must be unavailable (404 when the provider is off, 503 while no redirect is configured).
    if r.status_code not in (404, 503):
        raise Failed(f"identity is {identity!r} yet GET /api/auth/idp/start answered {r.status_code} instead of 404/503")
    return f"skipped: identity is {identity!r}, not workos (hosted sign-in unavailable, {r.status_code})"


def check_coverage(c: httpx.Client, headers: dict[str, str]) -> str:
    r = c.get("/api/coverage", headers=headers)
    if r.status_code != 200:
        raise Failed(f"GET /api/coverage answered {r.status_code} (is SMOKE_TOKEN the deployment's AGENTLEDGER_SMOKE_TOKEN?): "
                     f"{r.text[:200]!r}")
    caps = _body(r).get("capabilities") or []
    f1040 = [x for x in caps if x.get("id") == "f1040"]
    if not f1040:
        raise Failed("the published coverage has no f1040 entry")
    return f"{len(caps)} capabilities; f1040 is {f1040[0].get('status')}"


def check_individual_return(c: httpx.Client, headers: dict[str, str]) -> str:
    r = c.post("/api/returns/individual", json=SAMPLE, headers=headers)
    if r.status_code != 200:
        raise Failed(f"POST /api/returns/individual answered {r.status_code}: {r.text[:300]!r}")
    got = str(_body(r).get("forms", {}).get("f1040", {}).get("16"))
    if got != LINE_16:
        raise Failed(f"Form 1040 line 16 is {got!r}, expected {LINE_16!r} for the sample return")
    return f"Form 1040 line 16 = {got} for the sample return"


def main(argv: list[str]) -> int:
    if len(argv) != 2 or not argv[1].startswith(("http://", "https://")):
        print("usage: python scripts/smoke.py https://<deployment>", file=sys.stderr)
        return 2
    base = argv[1].rstrip("/")
    token = os.environ.get("SMOKE_TOKEN", "")
    expected = os.environ.get("EXPECTED_BUILD", "").strip() or None
    wait = int(os.environ.get("SMOKE_WAIT_SECONDS", "600"))
    dev = os.environ.get("SMOKE_DEV") == "1"
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    failures = 0
    identity: str | None = None
    print(f"smoke: {base} (expected build {expected or 'any'}, waiting up to {wait}s)")
    with httpx.Client(base_url=base, timeout=TIMEOUT, follow_redirects=False) as c:
        checks = [
            ("healthz", lambda: check_health(c, expected, wait)),
            ("auth config", lambda: check_auth_config(c, dev)),
            ("sign-in errors", lambda: check_login_is_generic(c)),
            ("hosted sign-in", lambda: check_idp_start(c, identity)),
            ("coverage", lambda: check_coverage(c, headers)),
            ("individual return", lambda: check_individual_return(c, headers)),
        ]
        for name, fn in checks:
            try:
                note = fn()
                if name == "auth config":
                    identity = note.split(";")[0].removeprefix("identity ").strip()
                print(f"ok    {name}: {note}")
            except Failed as e:
                failures += 1
                print(f"FAIL  {name}: {e}")
            except httpx.HTTPError as e:
                failures += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    if not token:
        print("note: SMOKE_TOKEN is not set; the coverage and return checks cannot pass without it")
    print("smoke", "ok" if failures == 0 else f"FAILED ({failures} of {len(checks)} checks)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
