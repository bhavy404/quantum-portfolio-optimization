"""Security regression tests for BB84-bound login tokens and encrypted key storage."""
import os
import tempfile
os.environ.setdefault("QPORTAL_DATA_DIR", tempfile.mkdtemp())

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from portal import auth, db, loginkey
from portal.config import SERVER_SECRET


def _new_user(email):
    return auth.register(email, "correct-horse-123", "Security Test")


def test_forged_tag_and_unknown_session_rejected():
    user = _new_user("forged-hardening@example.test")
    sec, atag = loginkey.establish(user["id"], "login")
    assert not loginkey.verify(sec["session_id"], user["id"], "f" * 32)
    assert not loginkey.verify(sec["session_id"] + 9999, user["id"], atag)


def test_token_from_another_user_is_rejected():
    alice = _new_user("alice-hardening@example.test")
    bob = _new_user("bob-hardening@example.test")
    sec, atag = loginkey.establish(alice["id"], "login")
    assert not loginkey.verify(sec["session_id"], bob["id"], atag)


def test_stolen_and_replayed_token_rejected_after_logout():
    user = _new_user("logout-hardening@example.test")
    sec, atag = loginkey.establish(user["id"], "login")
    token = auth.make_token(user, sec["session_id"], atag)
    cred = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)
    assert auth.current_user(cred)["id"] == user["id"]
    db.execute("UPDATE login_sessions SET revoked=1 WHERE id=?", (sec["session_id"],))
    # The same captured bearer token (stolen or replayed) is now unusable.
    with pytest.raises(HTTPException) as ex:
        auth.current_user(cred)
    assert ex.value.status_code == 401
    with pytest.raises(HTTPException):
        auth.current_user(cred)


def test_authentication_key_is_encrypted_at_rest():
    user = _new_user("encrypted-hardening@example.test")
    sec, atag = loginkey.establish(user["id"], "login")
    row = db.one("SELECT auth_key_hex FROM login_sessions WHERE id=?", (sec["session_id"],))
    assert row["auth_key_hex"].startswith("enc1:")
    assert len(row["auth_key_hex"]) > 80
    assert loginkey.verify(sec["session_id"], user["id"], atag)


def test_eve_is_an_actual_rejected_login_attempt_then_clean_attempt_is_accepted():
    user = _new_user("eve-flow@example.test")
    sec, atag = loginkey.establish(user["id"], "login", eve_test=True)
    assert len(sec["attempts"]) >= 2
    attacked, clean = sec["attempts"][0], sec["attempts"][1]
    assert attacked["eve"] is True
    assert attacked["accepted"] is False
    assert attacked["detected"] is True
    assert attacked["qber"] > 0.15
    assert clean["eve"] is False and clean["accepted"] is True
    assert loginkey.verify(sec["session_id"], user["id"], atag)


def test_audit_row_records_login_metrics():
    user = _new_user("audit-hardening@example.test")
    loginkey.establish(user["id"], "login", eve_test=True)
    row = db.one("SELECT * FROM login_audit WHERE account=? ORDER BY id DESC LIMIT 1", (user["email"],))
    assert row
    assert row["backend"]
    assert row["secret_bits"] >= loginkey.MIN_SECRET_BITS
    assert row["eve_tested"] == 1
    assert row["eve_detected"] == 1
    assert row["latency_ms"] >= 0


def test_signup_rate_limit_is_enforced():
    from types import SimpleNamespace
    from portal import main
    request = SimpleNamespace(client=SimpleNamespace(host="198.51.100.77"))
    for _ in range(5):
        main._rate_limit(request, "signup", "rate-limit@example.test")
    with pytest.raises(HTTPException) as ex:
        main._rate_limit(request, "signup", "rate-limit@example.test")
    assert ex.value.status_code == 429
