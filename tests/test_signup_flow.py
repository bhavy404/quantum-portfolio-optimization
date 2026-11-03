"""Sign-up / log-in behaviour in classic mode: no token on sign-up, BB84 event recorded, key bank refilled."""
import os
import tempfile

os.environ.setdefault("QPORTAL_DATA_DIR", tempfile.mkdtemp(prefix="qportal-signup-tests-"))

from fastapi.testclient import TestClient

from portal import config, db, keybank
from portal.main import app


def _client(monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MODE", "classic")
    calls = []
    monkeypatch.setattr(keybank, "top_up_async", lambda target=None: calls.append(target))
    return TestClient(app), calls


def test_classic_register_returns_no_token_but_records_signup_event(monkeypatch):
    client, calls = _client(monkeypatch)
    email = "classic-signup@example.test"
    r = client.post("/api/auth/register", json={"email": email, "password": "throwaway-123", "name": "Classic"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and "token" not in body
    assert body["user"]["email"] == email
    # The BB84 sign-up exchange was still run and recorded.
    sess = db.one("SELECT event FROM login_sessions WHERE user_id=(SELECT id FROM users WHERE email=?) ORDER BY id DESC LIMIT 1", (email,))
    assert sess and sess["event"] == "signup"
    audit = db.one("SELECT event,outcome FROM login_audit WHERE account=? ORDER BY id DESC LIMIT 1", (email,))
    assert audit and audit["event"] == "signup" and audit["outcome"] == "accepted"
    # Key bank topped up on registration.
    assert len(calls) >= 1


def test_classic_login_refills_key_bank_and_returns_token(monkeypatch):
    client, calls = _client(monkeypatch)
    email = "classic-login@example.test"
    assert client.post("/api/auth/register", json={"email": email, "password": "throwaway-123"}).status_code == 200
    before = len(calls)
    r = client.post("/api/auth/login", json={"email": email, "password": "throwaway-123"})
    assert r.status_code == 200, r.text
    assert r.json()["token"]
    assert len(calls) > before, "a classic login consumes a bank key, so it must trigger a top-up"
    me = client.get("/api/me", headers={"Authorization": "Bearer " + r.json()["token"]})
    assert me.status_code == 200
    feed = client.get("/api/security/activity", headers={"Authorization": "Bearer " + r.json()["token"]})
    titles = [i["title"] for i in feed.json()["items"]]
    assert "Account created" in titles and "Signed in" in titles


def test_wrong_password_is_a_plain_401_and_shows_in_activity(monkeypatch):
    client, _ = _client(monkeypatch)
    email = "classic-wrongpw@example.test"
    client.post("/api/auth/register", json={"email": email, "password": "throwaway-123"})
    bad = client.post("/api/auth/login", json={"email": email, "password": "not-the-password"})
    assert bad.status_code == 401
    assert bad.json()["detail"] == "Email or password is incorrect."
    good = client.post("/api/auth/login", json={"email": email, "password": "throwaway-123"})
    feed = client.get("/api/security/activity", headers={"Authorization": "Bearer " + good.json()["token"]})
    assert "Sign-in blocked" in [i["title"] for i in feed.json()["items"]]


def _audit_row(email, eve_on, verdict):
    db.execute("INSERT INTO bb84_live_audit(account,mode,qubits,eve_on,verdict,attempt_number) VALUES (?,?,?,?,?,?)",
               (email, "live", 1600, 1 if eve_on else 0, verdict, 1))


def test_safety_check_needs_a_caught_snooper_then_a_clean_key(monkeypatch):
    client, _ = _client(monkeypatch)
    email = "safety-check@example.test"
    client.post("/api/auth/register", json={"email": email, "password": "throwaway-123"})
    token = client.post("/api/auth/login", json={"email": email, "password": "throwaway-123"}).json()["token"]
    hdr = {"Authorization": "Bearer " + token}
    assert client.post("/api/security/safety-check", headers=hdr).status_code == 400  # nothing run yet
    _audit_row(email, True, "eavesdropper_detected")
    assert client.post("/api/security/safety-check", headers=hdr).status_code == 400  # no clean key after it
    _audit_row(email, False, "secure")
    r = client.post("/api/security/safety-check", headers=hdr)
    assert r.status_code == 200 and r.json()["saved"] is True
    titles = [i["title"] for i in client.get("/api/security/activity", headers=hdr).json()["items"]]
    assert "Safety check run" in titles


def test_safety_check_cannot_use_another_accounts_rows(monkeypatch):
    client, _ = _client(monkeypatch)
    email = "safety-other@example.test"
    client.post("/api/auth/register", json={"email": email, "password": "throwaway-123"})
    token = client.post("/api/auth/login", json={"email": email, "password": "throwaway-123"}).json()["token"]
    _audit_row("someone-else@example.test", True, "eavesdropper_detected")
    _audit_row("someone-else@example.test", False, "secure")
    assert client.post("/api/security/safety-check", headers={"Authorization": "Bearer " + token}).status_code == 400
