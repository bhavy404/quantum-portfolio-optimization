"""Tests for browser/server live BB84 login mode."""
import os
import secrets
import tempfile

os.environ.setdefault("QPORTAL_DATA_DIR", tempfile.mkdtemp(prefix="qportal-live-tests-"))

from fastapi import HTTPException
from fastapi.testclient import TestClient
from portal import auth, config, db, qkd_live
from portal.main import app


def browser_exchange(eve=False, attempt=1, account=""):
    """Test-side Bob: choose bases first, receive outcomes from the simulated channel, derive locally."""
    start = qkd_live.start(eve_on=eve, attempt=attempt, account=account)
    n = start["qubits"]
    bases = [secrets.randbelow(2) for _ in range(n)]
    sift = qkd_live.sift(start["session_id"], bases, account)
    bits = sift["bob_bits"]
    kept = [i for i, (a, b) in enumerate(zip(sift["alice_bases"], bases)) if a == b]
    sample_set = set(sift["sample_positions"])
    verdict = qkd_live.sample(start["session_id"], [bits[i] for i in sift["sample_positions"]], account)
    key = None
    if verdict["verdict"] == "secure":
        secret = [bits[i] for i in kept if i not in sample_set and i >= sift["demo_rows"]]
        assert len(secret) == verdict["secret_bits"]
        key = qkd_live.derive_key(secret, start["session_id"])
        assert qkd_live.fingerprint(key) == verdict["fingerprint"]
    return start, sift, verdict, key


def clean_browser_exchange(account=""):
    for attempt in range(1, 4):
        result = browser_exchange(False, attempt=attempt, account=account)
        if result[3] is not None:
            return result
    raise AssertionError("A clean BB84 exchange failed three consecutive attempts")

def test_clean_exchange_secure_and_at_least_128_secret_bits():
    _, _, result, key = clean_browser_exchange()
    assert result["verdict"] == "secure"
    assert result["secret_bits"] >= 128
    assert len(key) == 32


def test_standard_parameters():
    assert qkd_live.N_QUBITS == 1600 and qkd_live.SAMPLE_FRACTION == 0.30
    _, sift, result, _ = clean_browser_exchange()
    assert abs(result["sample_size"] / result["sifted_bits"] - 0.30) < 0.01
    assert result["secret_bits"] >= 400  # about 560 expected


def test_start_does_not_reveal_states_bases_or_bits():
    """The qubit states must stay on the server: no labels, bases or bits before Bob commits his bases."""
    start = qkd_live.start(eve_on=False, attempt=1)
    assert set(start) == {"session_id", "qubits", "demo_rows", "attempt", "eve_on", "channel_noise", "note", "latency_ms"}
    assert "states" not in start and "alice_bases" not in start and "alice_bits" not in start


def test_sift_needs_bases_before_any_outcome_and_only_once():
    start = qkd_live.start()
    for bad in ([0] * 10, [2] * qkd_live.N_QUBITS):
        try:
            qkd_live.sift(start["session_id"], bad)
            assert False, "invalid bases must be rejected"
        except ValueError:
            pass
    bases = [secrets.randbelow(2) for _ in range(qkd_live.N_QUBITS)]
    qkd_live.sift(start["session_id"], bases)
    try:
        qkd_live.sift(start["session_id"], bases)
        assert False, "a second sift of the same session must fail"
    except ValueError:
        pass


def test_demo_table_positions_never_enter_the_key():
    """browser_exchange derives the key WITHOUT the demo rows and asserts the server's fingerprint matches,
    so a server that included the revealed demo bits in its key would fail here."""
    _, sift, result, key = clean_browser_exchange()
    assert sift["demo_rows"] == qkd_live.DEMO_ROWS == 20
    assert key is not None and result["fingerprint"] == qkd_live.fingerprint(key)


def test_eve_detected_in_100_of_100_runs():
    detections, rates = 0, []
    for _ in range(100):
        _, _, result, _ = browser_exchange(True)
        detections += result["verdict"] == "eavesdropper_detected"
        rates.append(result["error_rate"])
    assert detections == 100
    assert 0.20 <= sum(rates) / len(rates) <= 0.30


def test_clean_false_rejection_rate_at_most_five_percent_over_200_runs():
    rejected = 0
    for _ in range(200):
        _, _, result, _ = browser_exchange(False)
        rejected += result["verdict"] != "secure"
    assert rejected <= 10, f"Clean false rejects: {rejected}/200; Priyanshu must be told if this fails."


def test_eve_attempt_is_discarded_then_clean_retry_succeeds():
    _, _, attacked, key = browser_exchange(True, 1)
    assert attacked["verdict"] == "eavesdropper_detected"
    assert key is None
    _, _, clean, clean_key = clean_browser_exchange()
    assert clean["verdict"] == "secure"
    assert clean_key and len(clean_key) == 32


def test_proof_missing_wrong_session_wrong_proof_and_replay_rejected():
    _, _, result, key = clean_browser_exchange(account="proof-test@example.test")
    sid = result["session_id"]
    email = "proof-test@example.test"
    good = qkd_live.make_proof(key, sid, email, "signup")
    try:
        qkd_live.verify_and_consume(sid, email, "signup", "")
        assert False, "missing proof must fail"
    except ValueError:
        pass
    try:
        qkd_live.verify_and_consume(sid, email, "signup", "0" * 64)
        assert False, "wrong proof must fail"
    except ValueError:
        pass
    try:
        qkd_live.verify_and_consume("not-the-session", email, "signup", good)
        assert False, "wrong session must fail"
    except ValueError:
        pass
    # A proof created with one clean session key cannot authenticate another valid session.
    _, _, other_result, other_key = clean_browser_exchange(account=email)
    other_sid = other_result["session_id"]
    try:
        qkd_live.verify_and_consume(other_sid, email, "signup", good)
        assert False, "proof from another session must fail"
    except ValueError:
        pass
    other_proof = qkd_live.make_proof(other_key, other_sid, email, "signup")
    qkd_live.verify_and_consume(other_sid, email, "signup", other_proof)
    qkd_live.verify_and_consume(sid, email, "signup", good)
    try:
        qkd_live.verify_and_consume(sid, email, "signup", good)
        assert False, "session replay must fail"
    except ValueError:
        pass


def test_live_register_accepts_browser_register_proof_mode(monkeypatch):
    """The Create account tab used to HMAC with mode=register while the API expected signup."""
    monkeypatch.setattr(config, "LOGIN_MODE", "live")
    monkeypatch.setattr(auth, "LOGIN_MODE", "live")
    client = TestClient(app)
    email = "live-register-mode@example.test"
    start, _, _, key = clean_browser_exchange(account=email)
    proof = qkd_live.make_proof(key, start["session_id"], email, "register")
    response = client.post("/api/auth/register", json={"email": email, "password": "throwaway-123", "name": "Live Test",
        "session_id": start["session_id"], "proof": proof})
    assert response.status_code == 200, response.text
    assert response.json()["user"]["email"] == email


def test_live_api_requires_proof_and_checks_it_once(monkeypatch):
    monkeypatch.setattr(config, "LOGIN_MODE", "live")
    monkeypatch.setattr(auth, "LOGIN_MODE", "live")
    client = TestClient(app)
    email = "live-api-proof@example.test"
    # Missing proof cannot create an account.
    response = client.post("/api/auth/register", json={"email": email, "password": "throwaway-123", "name": "Live Test"})
    assert response.status_code == 401
    assert db.one("SELECT id FROM users WHERE email=?", (email,)) is None
    start, sift, verdict, key = clean_browser_exchange(account=email)
    proof = qkd_live.make_proof(key, start["session_id"], email, "signup")
    response = client.post("/api/auth/register", json={"email": email, "password": "throwaway-123", "name": "Live Test",
        "session_id": start["session_id"], "proof": proof})
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    me = client.get("/api/me", headers={"Authorization": "Bearer " + token})
    assert me.status_code == 200
    # Normal JWT logout revokes the app session without using the BB84 key per request.
    logged_out = client.post("/api/auth/logout", headers={"Authorization": "Bearer " + token})
    assert logged_out.status_code == 200
    assert client.get("/api/me", headers={"Authorization": "Bearer " + token}).status_code == 401
    # Reuse of the same browser session for a second login is rejected.
    response = client.post("/api/auth/login", json={"email": email, "password": "throwaway-123",
        "session_id": start["session_id"], "proof": proof})
    assert response.status_code == 401


def test_audit_records_required_live_fields():
    email = "audit-live@example.test"
    _, _, result, _ = clean_browser_exchange(account=email)
    row = db.one("SELECT * FROM bb84_live_audit WHERE account=? ORDER BY id DESC LIMIT 1", (email,))
    assert row and row["mode"] == "live" and row["qubits"] == qkd_live.N_QUBITS
    assert row["sifted_bits"] > 0 and row["sample_size"] > 0
    assert row["error_rate"] is not None and row["upper_bound"] is not None
    assert row["eve_on"] == 0 and row["verdict"] == "secure"
    assert row["attempt_number"] >= 1 and row["latency_ms"] >= 0
