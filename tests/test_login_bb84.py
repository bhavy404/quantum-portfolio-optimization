"""Login BB84 handshake: key made, eavesdropper detected, token bound to the key.

Runs without Qiskit (uses the pure-Python BB84 model) and without a web server.
    QPORTAL_DATA_DIR=$(mktemp -d) python -m pytest tests/test_login_bb84.py   (or run this file directly)
"""
import os
import tempfile

os.environ.setdefault("QPORTAL_DATA_DIR", tempfile.mkdtemp())

from portal import auth, db, loginkey  # noqa: E402


def _user(email="t@example.com"):
    return auth.register(email, "correct-horse-1", "Tester")


def test_clean_login_makes_key_and_no_eavesdropping():
    u = _user("clean@example.com")
    sec, atag = loginkey.establish(u["id"], "signup")
    assert sec["status"] == "SECURE_KEY_ESTABLISHED" and sec["eavesdropping_detected"] is False
    assert sec["secret_bits"] >= loginkey.MIN_SECRET_BITS
    assert sec["qber"] < 0.05 and sec["qber_upper95"] <= loginkey.QBER_LIMIT
    assert len(sec["auth_key"]["fingerprint"].replace("-", "")) == 32
    assert "auth_key_hex" not in str(sec)  # the key itself is never exposed
    assert loginkey.verify(sec["session_id"], u["id"], atag)


def test_eavesdropper_option_is_always_detected_and_key_still_made():
    u = _user("eve@example.com")
    for _ in range(25):
        sec, atag = loginkey.establish(u["id"], "login", eve_test=True)
        assert sec["eve_test"]["ran"] and sec["eve_test"]["detected"], "intercept-resend must be caught"
        assert sec["eve_test"]["qber"] > 0.15
        assert sec["eavesdropping_detected"] is False and sec["qber"] < 0.05  # the real key came from a clean exchange


def test_exchange_with_eve_vs_without():
    assert loginkey.test_exchange(True)["status"] == "EAVESDROPPER_DETECTED"
    assert loginkey.test_exchange(True)["key_discarded"] is True
    clean = loginkey.test_exchange(False)
    assert clean["status"] == "SECURE_KEY_ESTABLISHED" and clean["key_discarded"] is False


def test_token_is_bound_to_handshake_key():
    u = _user("tok@example.com")
    other = _user("other@example.com")
    sec, atag = loginkey.establish(u["id"], "login")
    sid = sec["session_id"]
    assert not loginkey.verify(sid, u["id"], "0" * 32)               # forged tag
    assert not loginkey.verify(sid, other["id"], atag)               # someone else's session
    assert not loginkey.verify(sid + 999, u["id"], atag)             # unknown session
    assert not loginkey.verify(None, u["id"], atag)
    token = auth.make_token({"id": u["id"]}, sid, atag)
    class C: credentials = token
    assert auth.current_user(C())["sid"] == sid
    db.execute("UPDATE login_sessions SET revoked=1 WHERE id=?", (sid,))
    try:
        auth.current_user(C()); raise AssertionError("revoked session accepted")
    except Exception as ex:
        assert getattr(ex, "status_code", None) == 401


def test_each_login_gets_a_different_key():
    u = _user("many@example.com")
    prints = {loginkey.establish(u["id"], "login")[0]["auth_key"]["fingerprint"] for _ in range(5)}
    assert len(prints) == 5
    assert len(loginkey.history(u["id"])) == 5


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
