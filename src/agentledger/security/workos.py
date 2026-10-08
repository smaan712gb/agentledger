"""WorkOS AuthKit: hosted sign-in, MFA and passkeys at the identity provider (ADR-0004, backlog F-05).

WorkOS proves who someone is. Authority stays in AgentLedger: firm membership, role, reviewer status and engagement
grants live in the platform store, so a WorkOS account on its own grants nothing.

Endpoints used (https://workos.com/docs/reference/authkit):
  GET  /user_management/authorize                       hosted sign-in (PKCE S256, state, max_age=0 for step-up)
  POST /user_management/authenticate                     authorization code -> user, organization, method
  GET  /user_management/users/{id}/auth_factors          enrolled TOTP factors (MFA evidence)
  GET  /sso/jwks/{client_id}                              keys that sign access tokens (auth_time for freshness)
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import urlencode

API = "https://api.workos.com"
# A passkey is multi-factor on its own. SSO is not evidence of MFA (WorkOS: "The MFA requirement does not apply to
# SSO users"): it counts only for organizations whose identity provider is recorded as enforcing MFA. Anything else
# needs a TOTP factor enrolled at WorkOS.
PASSKEY = "Passkey"


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
        self._jwks: tuple[float, dict[str, Any]] | None = None
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

    def has_totp_factor(self, user_id: str) -> bool:
        return any(f.get("type") == "totp" for f in self.auth_factors(user_id))

    # ------------------------------------------------------------------ access token (auth_time)
    def jwks(self) -> dict[str, Any]:
        cached = self._jwks
        if cached and cached[0] > time.monotonic():
            return cached[1]
        keys = self._call("GET", f"/sso/jwks/{self.client_id}")
        self._jwks = (time.monotonic() + 3600, keys)
        return keys

    def verify_access_token(self, token: str, *, subject: str, leeway: int = 60) -> dict[str, Any]:
        """Verify an AuthKit access token (RS256, WorkOS JWKS) and return its claims. The caller relies on
        `auth_time`, the moment the person actually authenticated, never on when our own session was created."""
        try:
            header_b64, payload_b64, sig_b64 = token.split(".")
            header = json.loads(_b64(header_b64))
            claims = json.loads(_b64(payload_b64))
        except (ValueError, json.JSONDecodeError) as exc:
            raise WorkOSError("malformed access token") from exc
        if header.get("alg") != "RS256":
            raise WorkOSError("access token must be RS256")
        key = next((k for k in self.jwks().get("keys", []) if k.get("kid") == header.get("kid")), None)
        if key is None:
            self._jwks = None                      # keys may have rotated: refresh once
            key = next((k for k in self.jwks().get("keys", []) if k.get("kid") == header.get("kid")), None)
        if key is None or key.get("kty") != "RSA":
            raise WorkOSError("access token signed with an unknown key")
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        public = rsa.RSAPublicNumbers(int.from_bytes(_b64(key["e"]), "big"), int.from_bytes(_b64(key["n"]), "big")).public_key()
        try:
            public.verify(_b64(sig_b64), f"{header_b64}.{payload_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature as exc:
            raise WorkOSError("access token signature is invalid") from exc
        now = time.time()
        if not str(claims.get("iss", "")).startswith(API):
            raise WorkOSError("access token from an unexpected issuer")
        if claims.get("client_id") not in (None, self.client_id):
            raise WorkOSError("access token issued to another application")
        if claims.get("sub") != subject:
            raise WorkOSError("access token is for another user")
        if float(claims.get("exp", 0)) + leeway < now:
            raise WorkOSError("access token has expired")
        auth_time = claims.get("auth_time")
        if not isinstance(auth_time, (int, float)) or auth_time > now + leeway:
            raise WorkOSError("access token has no valid auth_time")
        return claims


def _b64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))
