"""Identity, MFA, sessions and per-firm encryption."""

import base64
import time

import pytest

from veritas.security import totp
from veritas.security.crypto import CryptoError, Keyring
from veritas.security.platform import AuthError, Platform

PW = "correct horse battery staple"


def test_totp_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert totp.code_at(secret, 59 // 30) == "287082"          # RFC 6238 Appendix B, truncated to 6 digits
    assert totp.code_at(secret, 1111111109 // 30) == "081804"


@pytest.fixture
def plat(tmp_path, monkeypatch):
    monkeypatch.delenv("VERITAS_MASTER_KEY", raising=False)
    return Platform(tmp_path, dev=True)


def sign_in(p, email, password=PW):
    step = p.login(email, password)
    assert "mfa" in step
    u = p.conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    secret = p.keys.open_text(u["firm_id"], u["totp_secret"], f"totp:{u['id']}")
    code = totp.code_at(secret, int(time.time() // 30) + 1)   # next step: never equal to one already used
    return p.complete_mfa(step["mfa"], code)


def onboard(p, firm="rivera-cpa"):
    admin_id = p.bootstrap_admin("ops@veritas.example", "Ops", PW)
    admin = p.public_user(p.user(admin_id))
    p.create_firm(firm, "Rivera CPA", by=admin_id)
    token = p.invite(firm, "maya@rivera.example", "firm_admin", by=admin)
    enrol = p.accept_invite(token, "Maya Chen", PW)
    session = p.complete_mfa(enrol.challenge, totp.code_at(enrol.secret, int(time.time() // 30)))
    return admin, session


def test_invite_enrol_and_login(plat):
    admin, session = onboard(plat)
    maya = plat.session_user(session)
    assert maya["role"] == "firm_admin" and maya["firm_id"] == "rivera-cpa" and maya["mfa_enrolled_at"]
    assert "password_hash" not in maya and "totp_secret" not in maya
    second = sign_in(plat, "maya@rivera.example")
    assert plat.session_user(second)["email"] == "maya@rivera.example"
    plat.logout(second)
    assert plat.session_user(second) is None
    events = {e["event"] for e in plat.events("rivera-cpa")}
    assert {"firm_created", "invite_accepted", "mfa_enrolled", "login", "logout"} <= events


def test_no_session_without_mfa_and_no_replay(plat):
    onboard(plat)
    step = plat.login("maya@rivera.example", PW)
    assert set(step) == {"mfa"}
    with pytest.raises(AuthError):
        plat.complete_mfa(step["mfa"], "000000")
    u = plat.conn.execute("SELECT * FROM users WHERE email = 'maya@rivera.example'").fetchone()
    secret = plat.keys.open_text(u["firm_id"], u["totp_secret"], f"totp:{u['id']}")
    used = totp.code_at(secret, u["totp_last_step"])
    step = plat.login("maya@rivera.example", PW)
    with pytest.raises(AuthError):
        plat.complete_mfa(step["mfa"], used)                  # replay of an already-used step


def test_lockout_after_failures(plat):
    onboard(plat)
    for _ in range(5):
        with pytest.raises(AuthError):
            plat.login("maya@rivera.example", "wrong password here")
    with pytest.raises(AuthError, match="too many attempts"):
        plat.login("maya@rivera.example", PW)


def test_unknown_account_same_message(plat):
    onboard(plat)
    with pytest.raises(AuthError, match="email or password is incorrect"):
        plat.login("nobody@x.example", PW)


def test_weak_password_rejected(plat):
    with pytest.raises(AuthError, match="12 characters"):
        plat.bootstrap_admin("a@b.example", "A", "short")


def test_invite_permissions(plat):
    admin, session = onboard(plat)
    maya = plat.session_user(session)
    plat.create_firm("other-cpa", "Other CPA", by=admin["id"])
    with pytest.raises(AuthError):
        plat.invite("other-cpa", "x@y.example", "cpa", by=maya)       # cannot invite into another firm
    with pytest.raises(AuthError):
        plat.invite("rivera-cpa", "x@y.example", "client", by=maya)   # client must be linked to a client
    tok = plat.invite("rivera-cpa", "cpa@rivera.example", "cpa", by=maya)
    enrol = plat.accept_invite(tok, "Sam", PW)
    cpa = plat.public_user(plat.user(plat.conn.execute("SELECT id FROM users WHERE email='cpa@rivera.example'").fetchone()[0]))
    with pytest.raises(AuthError):
        plat.invite("rivera-cpa", "s@rivera.example", "staff", by=cpa)  # only firm admins invite staff
    with pytest.raises(AuthError):
        plat.accept_invite(tok, "Sam", PW)                              # single use


def test_disable_revokes_sessions(plat):
    admin, session = onboard(plat)
    maya = plat.session_user(session)
    plat.set_disabled(maya["id"], True, by=admin)
    assert plat.session_user(session) is None


def test_encryption_binds_firm_and_purpose(plat):
    plat.keys.create("firm-a")
    plat.keys.create("firm-b")
    blob = plat.keys.encrypt("firm-a", b"123-45-6789", "ssn:client-1")
    assert plat.keys.decrypt("firm-a", blob, "ssn:client-1") == b"123-45-6789"
    with pytest.raises(Exception):
        plat.keys.decrypt("firm-a", blob, "ssn:client-2")
    with pytest.raises(Exception):
        plat.keys.decrypt("firm-b", blob, "ssn:client-1")
    assert b"123-45-6789" not in blob


def test_rotation_keeps_old_ciphertext_readable(plat):
    plat.keys.create("firm-a")
    old = plat.keys.encrypt("firm-a", b"old", "x")
    assert plat.keys.rotate("firm-a") == 2
    assert plat.keys.decrypt("firm-a", old, "x") == b"old"
    assert plat.keys.encrypt("firm-a", b"new", "x")[3:5] == (2).to_bytes(2, "big")


def test_deleting_firm_crypto_shreds_and_revokes(plat):
    admin, session = onboard(plat)
    blob = plat.keys.encrypt("rivera-cpa", b"secret", "doc")
    plat.delete_firm("rivera-cpa", by=admin["id"])
    assert plat.session_user(session) is None
    fresh = Keyring(plat.conn, plat.keys.master)
    with pytest.raises(CryptoError):
        fresh.decrypt("rivera-cpa", blob, "doc")


def test_production_requires_master_key(tmp_path, monkeypatch):
    monkeypatch.delenv("VERITAS_MASTER_KEY", raising=False)
    with pytest.raises(CryptoError):
        Platform(tmp_path, dev=False)
