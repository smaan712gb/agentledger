"""Sign-in through WorkOS AuthKit, with authority kept in AgentLedger (ADR-0004, backlog F-05).

The real WorkOS client runs against a fake WorkOS server that checks PKCE (sha256 of the verifier must equal the
challenge sent in the authorize URL), the client secret and the grant type, and returns users with or without a
second factor.
"""

from __future__ import annotations

import base64
import hashlib
import importlib
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agentledger.security import totp

PW = "correct horse battery staple"


class FakeWorkOS:
    def __init__(self):
        self.codes: dict[str, dict] = {}
        self.factors: dict[str, list] = {}

    def issue(self, authorize_url: str, user: dict, method: str = "Password", impersonator: dict | None = None) -> tuple[str, str]:
        """What the hosted page does after the person signs in: returns (code, state) for the callback."""
        q = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        assert q["response_type"] == "code" and q["provider"] == "authkit" and q["code_challenge_method"] == "S256"
        code = "code_" + secrets.token_hex(8)
        self.codes[code] = {"challenge": q["code_challenge"], "user": user, "method": method, "impersonator": impersonator,
                            "max_age": q.get("max_age")}
        return code, q["state"]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        import json

        path = request.url.path
        if path == "/user_management/authenticate":
            body = json.loads(request.content)
            assert body["grant_type"] == "authorization_code" and body["client_id"] == "client_test"
            assert body["client_secret"] == "sk_test"
            grant = self.codes.pop(body["code"], None)
            if grant is None:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "code is invalid"})
            digest = base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if digest != grant["challenge"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE mismatch"})
            out = {"user": grant["user"], "organization_id": None, "access_token": "jwt", "refresh_token": "r",
                   "authentication_method": grant["method"]}
            if grant["impersonator"]:
                out["impersonator"] = grant["impersonator"]
            return httpx.Response(200, json=out)
        if path.startswith("/user_management/users/") and path.endswith("/auth_factors"):
            uid = path.split("/")[3]
            return httpx.Response(200, json={"data": self.factors.get(uid, [])})
        return httpx.Response(404, json={"message": "not found"})


def wuser(email: str, uid: str | None = None, verified: bool = True) -> dict:
    return {"id": uid or "user_" + secrets.token_hex(6), "email": email, "email_verified": verified,
            "first_name": email.split("@")[0].title(), "last_name": None}


@pytest.fixture
def api(home, monkeypatch):
    monkeypatch.setenv("AGENTLEDGER_HOME", str(home))
    monkeypatch.setenv("AGENTLEDGER_AGENTS", "0")
    monkeypatch.delenv("AGENTLEDGER_DEV_AUTH", raising=False)
    monkeypatch.setenv("AGENTLEDGER_MASTER_KEY", base64.b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setenv("AGENTLEDGER_IDENTITY", "workos")
    monkeypatch.setenv("WORKOS_REDIRECT_URI", "https://app.agentledger.example/api/auth/idp/callback")
    import agentledger.api.app as mod

    importlib.reload(mod)
    from fastapi.testclient import TestClient

    from agentledger.security.workos import WorkOS

    fake = FakeWorkOS()
    mod.IDP_CLIENT = WorkOS(api_key="sk_test", client_id="client_test",
                            client=httpx.Client(base_url="https://api.workos.com", transport=httpx.MockTransport(fake)))
    c = TestClient(mod.app)
    # A platform administrator (break-glass password + TOTP) creates a firm and invites its admin.
    mod.PLATFORM.bootstrap_admin("ops@agentledger.example", "Ops", PW)
    step = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    r = c.post("/api/auth/mfa", json={"challenge": step["challenge"], "code": totp.code_at(step["secret"], int(time.time() // 30))})
    ops = {"Authorization": f"Bearer {r.json()['token']}"}
    invite = c.post("/api/platform/firms", json={"id": "rivera-cpa", "name": "Rivera CPA", "admin_email": "maya@rivera.example"},
                    headers=ops).json()["admin_invite_token"]
    return mod, c, fake, invite


def sign_in(c, fake, purpose, user, *, invite="", headers=None, method="Passkey", impersonator=None):
    r = c.get("/api/auth/idp/start", params={"purpose": purpose, "invite": invite}, headers=headers or {})
    assert r.status_code == 200, r.text
    code, state = fake.issue(r.json()["url"], user, method, impersonator)
    return c.get("/api/auth/idp/callback", params={"code": code, "state": state}, follow_redirects=False), r.json()["url"], (code, state)


def session_from(resp) -> dict:
    loc = resp.headers["location"]
    assert loc.startswith("/#session="), loc
    return {"Authorization": f"Bearer {loc.split('=', 1)[1]}"}


def test_invitation_creates_a_linked_account_and_password_sign_in_is_off(api):
    mod, c, fake, invite = api
    resp, url, _ = sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite)
    maya = session_from(resp)
    assert "login_hint=maya%40rivera.example" in url
    assert c.get("/api/clients", headers=maya).status_code == 200
    u = mod.PLATFORM.conn.execute("SELECT * FROM users WHERE email = 'maya@rivera.example'").fetchone()
    assert u["idp_subject"] == "user_maya" and u["role"] == "firm_admin" and u["password_hash"] == "!idp"
    assert c.post("/api/auth/login", json={"email": "maya@rivera.example", "password": PW}).status_code == 401
    # The same identity signs in again later.
    resp, _, _ = sign_in(c, fake, "login", wuser("maya@rivera.example", "user_maya"))
    assert c.get("/api/me", headers=session_from(resp)).json()["email"] == "maya@rivera.example"


@pytest.mark.parametrize("case", ["wrong_email", "unverified", "impersonated", "no_second_factor"])
def test_provider_sign_ins_that_are_refused(api, case):
    mod, c, fake, invite = api
    user = wuser("maya@rivera.example", verified=case != "unverified")
    if case == "wrong_email":
        user = wuser("someone@else.example")
    resp, _, _ = sign_in(c, fake, "invite", user, invite=invite, method="Password" if case == "no_second_factor" else "Passkey",
                         impersonator={"email": "support@workos.example", "reason": "debug"} if case == "impersonated" else None)
    assert resp.headers["location"].startswith("/#signin_error="), resp.headers["location"]
    assert not mod.PLATFORM.conn.execute("SELECT 1 FROM users WHERE email = 'maya@rivera.example'").fetchone()


def test_password_sign_in_with_an_enrolled_totp_factor_counts_as_multi_factor(api):
    mod, c, fake, invite = api
    fake.factors["user_maya"] = [{"id": "auth_factor_1", "type": "totp"}]
    resp, _, _ = sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite, method="Password")
    assert resp.headers["location"].startswith("/#session=")


def test_no_account_is_linked_by_email_alone_and_state_cannot_be_replayed(api):
    mod, c, fake, invite = api
    resp, _, (code, state) = sign_in(c, fake, "login", wuser("maya@rivera.example"))
    assert "no%20AgentLedger%20account" in resp.headers["location"]
    replay = c.get("/api/auth/idp/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert "expired" in replay.headers["location"]


def test_step_up_is_required_for_consequential_actions(api):
    mod, c, fake, invite = api
    resp, _, _ = sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite)
    maya = session_from(resp)
    assert c.post("/api/clients", json={"id": "ortiz-auto", "name": "Ortiz Auto"}, headers=maya).status_code == 200
    tok = c.post("/api/auth/invite", json={"email": "lee@rivera.example", "role": "cpa"}, headers=maya).json()["invite_token"]
    resp, _, _ = sign_in(c, fake, "invite", wuser("lee@rivera.example", "user_lee"), invite=tok)
    lee = session_from(resp)
    close = {"through": "2026-01-31"}
    assert c.post("/api/clients/ortiz-auto/periods/close", json=close, headers=lee).status_code == 200   # just signed in
    # Ten minutes later the session is no longer fresh.
    old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 600))
    mod.PLATFORM.conn.execute("UPDATE sessions SET created_at = ? WHERE user_id = (SELECT id FROM users WHERE email = 'lee@rivera.example')", (old,))
    r = c.post("/api/clients/ortiz-auto/periods/close", json={"through": "2026-02-28"}, headers=lee)
    assert r.status_code == 403 and r.json()["detail"] == "step_up_required"
    # A different identity cannot step up this session; the right one, re-authenticated (max_age=0), can.
    resp, url, _ = sign_in(c, fake, "step_up", wuser("maya@rivera.example", "user_maya"), headers=lee)
    assert "max_age=0" in url and resp.headers["location"].startswith("/#signin_error=")
    assert c.post("/api/clients/ortiz-auto/periods/close", json={"through": "2026-02-28"}, headers=lee).status_code == 403
    resp, _, _ = sign_in(c, fake, "step_up", wuser("lee@rivera.example", "user_lee"), headers=lee)
    assert resp.headers["location"] == "/#step_up=ok"
    assert c.post("/api/clients/ortiz-auto/periods/close", json={"through": "2026-02-28"}, headers=lee).status_code == 200


def test_local_account_steps_up_with_its_code_and_links_the_provider(api):
    mod, c, fake, _ = api
    ops_login = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    assert ops_login["next"] == "mfa"                       # break-glass password sign-in stays for platform admins
    u = mod.PLATFORM.conn.execute("SELECT * FROM users WHERE email = 'ops@agentledger.example'").fetchone()
    secret = mod.PLATFORM.keys.open_text(u["firm_id"], u["totp_secret"], f"totp:{u['id']}")
    step = int(time.time() // 30)
    r = c.post("/api/auth/mfa", json={"challenge": ops_login["challenge"], "code": totp.code_at(secret, step + 1)})
    ops = {"Authorization": f"Bearer {r.json()['token']}"}
    old = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(time.time() - 600))
    mod.PLATFORM.conn.execute("UPDATE sessions SET created_at = ? WHERE token_hash = ?",
                              (old, hashlib.sha256(ops["Authorization"][7:].encode()).hexdigest()))
    firm = {"id": "lake-tax", "name": "Lake Tax", "admin_email": "lee@lake.example"}
    assert c.post("/api/platform/firms", json=firm, headers=ops).status_code == 403
    assert c.post("/api/auth/step-up", json={"code": "000000"}, headers=ops).status_code == 401
    # A code is accepted once; pretend the next time window has arrived instead of sleeping 30 seconds.
    mod.PLATFORM.conn.execute("UPDATE users SET totp_last_step = ? WHERE id = ?", (step - 5, u["id"]))
    assert c.post("/api/auth/step-up", json={"code": totp.code_at(secret, step)}, headers=ops).status_code == 200
    assert c.post("/api/platform/firms", json=firm, headers=ops).status_code == 200
    # Linking the provider to this local account, from the signed-in session only.
    resp, _, _ = sign_in(c, fake, "link", wuser("ops@agentledger.example", "user_ops"), headers=ops)
    assert resp.headers["location"] == "/#link=ok"
    resp, _, _ = sign_in(c, fake, "login", wuser("ops@agentledger.example", "user_ops"))
    assert resp.headers["location"].startswith("/#session=")


def test_live_workos_credentials_reach_the_real_api():
    """With real keys in the environment: the authorize URL is well formed and the token endpoint accepts our
    client credentials (a made-up code is refused as an invalid grant, not as unauthorized)."""
    from pathlib import Path

    from agentledger import envfile
    from agentledger.security.workos import WorkOS, WorkOSError, pkce_pair

    envfile.load(Path(__file__).resolve().parents[1])
    import os

    if os.environ.get("AGENTLEDGER_TEST_WORKOS") != "1" or not os.environ.get("WORKOS_API_KEY"):
        pytest.skip("live WorkOS check: set AGENTLEDGER_TEST_WORKOS=1 with WORKOS_API_KEY and WORKOS_CLIENT_ID")
    w = WorkOS()
    verifier, challenge = pkce_pair()
    url = w.authorization_url("http://localhost:8740/api/auth/idp/callback", "s", challenge)
    assert url.startswith("https://api.workos.com/user_management/authorize?") and "client_id=" in url
    with pytest.raises(WorkOSError) as exc:
        w.authenticate("not-a-real-code", verifier)
    assert " 401 " not in str(exc.value) and " 403 " not in str(exc.value), str(exc.value)
