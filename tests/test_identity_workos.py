"""Sign-in through WorkOS AuthKit, with authority kept in AgentLedger (ADR-0004, backlog F-05).

The real WorkOS client runs against a fake WorkOS server that checks PKCE (sha256 of the verifier must equal the
challenge sent in the authorize URL), the client secret and the grant type, publishes a JWKS, and issues RS256 access
tokens whose auth_time says when the person really authenticated (possibly an older provider session).
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


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class FakeWorkOS:
    def __init__(self):
        from cryptography.hazmat.primitives.asymmetric import rsa

        self.codes: dict[str, dict] = {}
        self.factors: dict[str, list] = {}
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key_" + secrets.token_hex(4)

    def jwks(self) -> dict:
        n = self.key.public_key().public_numbers()
        return {"keys": [{"kty": "RSA", "kid": self.kid, "alg": "RS256", "use": "sig",
                          "n": _b64u(n.n.to_bytes((n.n.bit_length() + 7) // 8, "big")), "e": _b64u(n.e.to_bytes(3, "big"))}]}

    def token(self, sub: str, auth_time: float, **extra) -> str:
        import json

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        header = _b64u(json.dumps({"alg": "RS256", "kid": self.kid, "typ": "JWT"}).encode())
        claims = {"iss": "https://api.workos.com", "sub": sub, "client_id": "client_test", "sid": "session_x",
                  "exp": int(time.time()) + 300, "iat": int(time.time()), "auth_time": int(auth_time), **extra}
        payload = _b64u(json.dumps(claims).encode())
        sig = self.key.sign(f"{header}.{payload}".encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{header}.{payload}.{_b64u(sig)}"

    def issue(self, authorize_url: str, user: dict, method: str = "Password", impersonator: dict | None = None, *,
              auth_age: int = 0, organization: str | None = None) -> tuple[str, str]:
        """What the hosted page does after the person signs in: returns (code, state) for the callback. `auth_age`
        is how long ago the person really authenticated (an existing provider session reused without max_age)."""
        q = {k: v[0] for k, v in parse_qs(urlparse(authorize_url).query).items()}
        assert q["response_type"] == "code" and q["provider"] == "authkit" and q["code_challenge_method"] == "S256"
        if q.get("max_age") == "0":
            auth_age = 0                     # a forced re-authentication is fresh by definition
        code = "code_" + secrets.token_hex(8)
        self.codes[code] = {"challenge": q["code_challenge"], "user": user, "method": method, "impersonator": impersonator,
                            "auth_time": time.time() - auth_age, "organization": organization}
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
            out = {"user": grant["user"], "organization_id": grant["organization"], "refresh_token": "r",
                   "access_token": self.token(grant["user"]["id"], grant["auth_time"]),
                   "authentication_method": grant["method"]}
            if grant["impersonator"]:
                out["impersonator"] = grant["impersonator"]
            return httpx.Response(200, json=out)
        if path == "/sso/jwks/client_test":
            return httpx.Response(200, json=self.jwks())
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


def sign_in(c, fake, purpose, user, *, invite="", headers=None, method="Passkey", impersonator=None, auth_age=0, organization=None):
    r = c.get("/api/auth/idp/start", params={"purpose": purpose, "invite": invite}, headers=headers or {})
    assert r.status_code == 200, r.text
    code, state = fake.issue(r.json()["url"], user, method, impersonator, auth_age=auth_age, organization=organization)
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
    # Signing in again by reusing an hour-old provider session: our session is new, the authentication is not.
    resp, _, _ = sign_in(c, fake, "login", wuser("lee@rivera.example", "user_lee"), auth_age=3600)
    lee = session_from(resp)
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
    mod.PLATFORM.conn.execute("UPDATE sessions SET authenticated_at = ? WHERE token_hash = ?",
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


# --------------------------------------------------------------------------- re-audit of 2b42c07
def test_workos_mode_never_creates_local_password_accounts(api):
    mod, c, fake, invite = api
    r = c.post("/api/auth/accept", json={"token": invite, "name": "Maya", "password": PW})
    assert r.status_code == 400 and "sign-in provider" in r.json()["detail"]
    assert not mod.PLATFORM.conn.execute("SELECT 1 FROM users WHERE email = 'maya@rivera.example'").fetchone()


def test_sso_counts_as_mfa_only_for_an_attested_organization(api):
    mod, c, fake, invite = api
    resp, _, _ = sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite, method="SSO",
                         organization="org_rivera")
    assert "two-step" in resp.headers["location"]                       # SSO alone proves nothing about MFA
    ops = c.post("/api/auth/login", json={"email": "ops@agentledger.example", "password": PW}).json()
    u = mod.PLATFORM.conn.execute("SELECT * FROM users WHERE email = 'ops@agentledger.example'").fetchone()
    secret = mod.PLATFORM.keys.open_text(u["firm_id"], u["totp_secret"], f"totp:{u['id']}")
    mod.PLATFORM.conn.execute("UPDATE users SET totp_last_step = NULL WHERE id = ?", (u["id"],))
    ops_h = {"Authorization": "Bearer " + c.post("/api/auth/mfa", json={"challenge": ops["challenge"],
             "code": totp.code_at(secret, int(time.time() // 30))}).json()["token"]}
    att = {"organization_id": "org_rivera", "firm_id": "rivera-cpa", "evidence": "Okta policy export: MFA required for all apps"}
    assert c.post("/api/platform/sso-mfa", json=att, headers=ops_h).status_code == 200
    resp, _, _ = sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite, method="SSO",
                         organization="org_other")
    assert "two-step" in resp.headers["location"]                       # another organization's SSO does not count
    resp, _, _ = sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite, method="SSO",
                         organization="org_rivera")
    assert resp.headers["location"].startswith("/#session=")


def test_step_up_needs_a_fresh_provider_authentication(api):
    mod, c, fake, invite = api
    maya = session_from(sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite)[0])
    old = session_from(sign_in(c, fake, "login", wuser("maya@rivera.example", "user_maya"), auth_age=3600)[0])
    # The provider ignored max_age and returned the old authentication: refused.
    r = c.get("/api/auth/idp/start", params={"purpose": "step_up"}, headers=old)
    code, state = fake.issue(r.json()["url"], wuser("maya@rivera.example", "user_maya"), "Passkey")
    fake.codes[code]["auth_time"] = time.time() - 3600          # as if max_age had been ignored
    back = c.get("/api/auth/idp/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert "did%20not%20re-authenticate" in back.headers["location"]
    assert maya  # the first, genuinely fresh session is unaffected


def test_granting_engagements_needs_a_recent_sign_in(api):
    mod, c, fake, invite = api
    maya = session_from(sign_in(c, fake, "invite", wuser("maya@rivera.example", "user_maya"), invite=invite)[0])
    assert c.post("/api/clients", json={"id": "ortiz-auto", "name": "Ortiz Auto"}, headers=maya).status_code == 200
    tok = c.post("/api/auth/invite", json={"email": "sam@rivera.example", "role": "staff"}, headers=maya).json()["invite_token"]
    session_from(sign_in(c, fake, "invite", wuser("sam@rivera.example", "user_sam"), invite=tok)[0])
    sam_id = mod.PLATFORM.conn.execute("SELECT id FROM users WHERE email = 'sam@rivera.example'").fetchone()[0]
    stale = session_from(sign_in(c, fake, "login", wuser("maya@rivera.example", "user_maya"), auth_age=3600)[0])
    for call in (lambda h: c.post(f"/api/auth/users/{sam_id}/grants", json={"client_id": "ortiz-auto"}, headers=h),
                 lambda h: c.delete(f"/api/auth/users/{sam_id}/grants/ortiz-auto", headers=h),
                 lambda h: c.post("/api/auth/invite", json={"email": "x@rivera.example", "role": "staff"}, headers=h)):
        r = call(stale)
        assert r.status_code == 403 and r.json()["detail"] == "step_up_required"
    assert c.post(f"/api/auth/users/{sam_id}/grants", json={"client_id": "ortiz-auto"}, headers=maya).status_code == 200


def test_callback_must_come_back_to_the_browser_that_started(api):
    mod, c, fake, invite = api
    from fastapi.testclient import TestClient

    r = c.get("/api/auth/idp/start", params={"purpose": "invite", "invite": invite})
    code, state = fake.issue(r.json()["url"], wuser("maya@rivera.example", "user_maya"), "Passkey")
    other_browser = TestClient(mod.app)                                  # no cookie from the start request
    back = other_browser.get("/api/auth/idp/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert "same%20browser" in back.headers["location"]
    again = c.get("/api/auth/idp/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert "expired" in again.headers["location"]                        # and the state is spent
