"""WorkOS AuthKit: hosted sign-in, MFA and passkeys at the identity provider (ADR-0004, backlog F-05).

WorkOS proves who someone is. Authority stays in AgentLedger: firm membership, role, reviewer status and engagement
grants live in the platform store, so a WorkOS account on its own grants nothing.

Endpoints used (https://workos.com/docs/reference/authkit):
  GET  /user_management/authorize                       hosted sign-in (PKCE S256, state, max_age=0 for step-up)
  POST /user_management/authenticate                     authorization code -> user, organization, method
  GET  /user_management/users/{id}/auth_factors          enrolled TOTP factors (MFA evidence)
"""

from __future__ import annotations

import base64
import hashlib
import os
import secrets
from typing import Any
from urllib.parse import urlencode

API = "https://api.workos.com"
# Sign-in methods that are multi-factor or phishing-resistant on their own. Anything else needs an enrolled factor.
STRONG_METHODS = {"SSO", "Passkey"}


class WorkOSError(RuntimeError):
    pass


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge) for S256 PKCE."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class WorkOS:
    def __init__(self, *, api_key: str | None = None, client_id: str | None = None, client: Any = None):
        import httpx

        self.api_key = api_key or os.environ.get("WORKOS_API_KEY", "")
        self.client_id = client_id or os.environ.get("WORKOS_CLIENT_ID", "")
        if not self.api_key or not self.client_id:
            raise WorkOSError("WorkOS needs WORKOS_API_KEY and WORKOS_CLIENT_ID")
        self.http = client or httpx.Client(base_url=API, timeout=20,
                                           headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"})

    def authorization_url(self, redirect_uri: str, state: str, code_challenge: str, *, login_hint: str | None = None,
                          max_age: int | None = None, invitation_token: str | None = None) -> str:
        q: dict[str, Any] = {"response_type": "code", "client_id": self.client_id, "redirect_uri": redirect_uri,
                             "provider": "authkit", "state": state, "code_challenge": code_challenge,
                             "code_challenge_method": "S256"}
        if login_hint:
            q["login_hint"] = login_hint
        if max_age is not None:
            q["max_age"] = max_age
        if invitation_token:
            q["invitation_token"] = invitation_token
        return f"{API}/user_management/authorize?{urlencode(q)}"

    def _call(self, method: str, path: str, **kw: Any) -> dict[str, Any]:
        r = self.http.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                body = r.json()
                why = body.get("error_description") or body.get("message") or body.get("error") or ""
            except ValueError:
                why = r.text[:200]
            raise WorkOSError(f"WorkOS {path}: {r.status_code} {why}".strip())
        return r.json()

    def authenticate(self, code: str, code_verifier: str, *, ip: str | None = None, user_agent: str | None = None) -> dict[str, Any]:
        body = {"client_id": self.client_id, "client_secret": self.api_key, "grant_type": "authorization_code",
                "code": code, "code_verifier": code_verifier}
        if ip:
            body["ip_address"] = ip
        if user_agent:
            body["user_agent"] = user_agent[:200]
        return self._call("POST", "/user_management/authenticate", json=body)

    def auth_factors(self, user_id: str) -> list[dict[str, Any]]:
        return list(self._call("GET", f"/user_management/users/{user_id}/auth_factors", params={"limit": 10}).get("data", []))

    def multi_factor(self, auth: dict[str, Any]) -> bool:
        """Whether this sign-in is multi-factor: SSO or a passkey, or the user has an enrolled TOTP factor."""
        if auth.get("authentication_method") in STRONG_METHODS:
            return True
        return any(f.get("type") == "totp" for f in self.auth_factors(auth["user"]["id"]))
