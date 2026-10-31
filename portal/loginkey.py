"""BB84 login authentication.

Alice and Bob are both server-side protocol roles; this is not a quantum link to the user's device.
"""
import hashlib
import hmac
import json
import secrets
import time

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import bb84_batch, config, db, keybank, qlock

LOGIN_SESSIONS = 8
MIN_SECRET_BITS = 128
MAX_ATTEMPTS = 3
QBER_LIMIT = keybank.QBER_LIMIT


def _storage_key() -> bytes:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(config.SERVER_SECRET + b"|qportal-login-auth-storage")
    return digest.finalize()


def _seal_key(key: bytes) -> str:
    nonce = secrets.token_bytes(12)
    return "enc1:" + (nonce + AESGCM(_storage_key()).encrypt(nonce, key, b"qportal-login-auth")).hex()


def _open_key(value: str, sid: int = None) -> bytes:
    if not value.startswith("enc1:"):
        raw = bytes.fromhex(value)  # legacy database migration
        if sid is not None:
            db.execute("UPDATE login_sessions SET auth_key_hex=? WHERE id=?", (_seal_key(raw), sid))
        return raw
    packed = bytes.fromhex(value[5:])
    return AESGCM(_storage_key()).decrypt(packed[:12], packed[12:], b"qportal-login-auth")


def _simulate(eve: bool) -> dict:
    try:
        import qiskit_aer  # noqa: F401
    except ImportError:
        return bb84_batch.run_model(LOGIN_SESSIONS, eve=eve)
    return bb84_batch.run(bb84_batch.execute_simulator, LOGIN_SESSIONS, bb84_batch.QUBITS, eve)


def _judge(out: dict, eve: bool) -> dict:
    errors, sample = out["errors"], out["sample_bits"]
    qber = errors / sample if sample else 1.0
    upper = keybank._upper95(errors, sample)
    detected = upper > QBER_LIMIT
    return {
        "secret": out["secret"], "backend": out["backend"],
        "job_ids": [out["job_id"]] if out.get("job_id") else [],
        "sessions": out["sessions"], "raw_qubits": out["raw_qubits"],
        "sifted_bits": out["sifted_bits"], "sample_bits": sample,
        "secret_bits": len(out["secret"]), "qber": qber, "qber_upper95": upper,
        "eve_present": eve, "eavesdropping_detected": detected,
        "ok": len(out["secret"]) >= MIN_SECRET_BITS and not detected,
        "trace": out.get("trace", {}),
    }


def exchange(eve: bool = False, use_ibm: bool = False) -> dict:
    """One exchange. Hardware is only used when explicitly selected."""
    note = None
    if use_ibm and config.IBM_TOKEN:
        try:
            res = _judge(bb84_batch.run(bb84_batch.execute_hardware, LOGIN_SESSIONS,
                                        bb84_batch.QUBITS, eve), eve)
            return {**res, "source": "hardware", "fallback_note": None}
        except Exception as ex:
            note = f"IBM unavailable ({type(ex).__name__}); used the simulator instead."
    elif use_ibm:
        note = "IBM Quantum is not configured; used the simulator instead."
    res = _judge(_simulate(eve), eve)
    source = "simulator" if not res["backend"].startswith("Python BB84 model") else "model"
    return {**res, "source": source, "fallback_note": note}


def _derive(secret_bits, pool_key: bytes, user_id: int) -> bytes:
    fresh = keybank._pack(secret_bits)
    material = fresh + pool_key
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=config.SERVER_SECRET,
                info=f"qportal-login-auth|{user_id}".encode()).derive(material)


def _group(hex_str: str, size: int = 4) -> str:
    h = hex_str.upper()
    return "-".join(h[i:i + size] for i in range(0, len(h), size))


def fingerprint(key: bytes) -> str:
    return _group(hashlib.sha256(b"fp|" + key).hexdigest()[:32])


def tag(key_hex: str, sid: int, user_id: int) -> str:
    return hmac.new(bytes.fromhex(key_hex), f"qportal-session|{sid}|{user_id}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def verify(sid, user_id: int, atag) -> bool:
    try:
        row = db.one("SELECT user_id,auth_key_hex,revoked FROM login_sessions WHERE id=?", (int(sid),))
        if not row or row["revoked"] or row["user_id"] != user_id or not isinstance(atag, str):
            return False
        key = _open_key(row["auth_key_hex"], int(sid))
        return hmac.compare_digest(tag(key.hex(), int(sid), user_id), atag)
    except (TypeError, ValueError, KeyError):
        return False
    except Exception:
        return False


def establish(user_id: int, event: str = "login", eve_test: bool = False,
              use_ibm: bool = False) -> tuple:
    """Eve, when selected, attacks the actual first login attempt. Its key is discarded.

    A clean simulator exchange follows. A pre-generated BB84 pool key is also consumed and
    combined with the fresh exchange to derive the session authentication key.
    """
    started = time.monotonic()
    attempts = []
    eve_info = None
    if eve_test:
        attacked = exchange(eve=True, use_ibm=False)
        detected = bool(attacked["eavesdropping_detected"])
        attempts.append({
            "number": 1, "eve": True, "accepted": False, "detected": detected,
            "qber": attacked["qber"], "qber_upper95": attacked["qber_upper95"],
            "sample_bits": attacked["sample_bits"], "secret_bits": attacked["secret_bits"],
            "backend": attacked["backend"], "source": attacked["source"],
            "trace": attacked.get("trace", {}),
            "disposition": "rejected; key discarded",
        })
        eve_info = {"detected": detected, "qber": attacked["qber"],
                    "qber_upper95": attacked["qber_upper95"], "sample_bits": attacked["sample_bits"],
                    "backend": attacked["backend"]}
    clean = None
    tries = 0
    while tries < MAX_ATTEMPTS:
        tries += 1
        candidate = exchange(eve=False, use_ibm=use_ibm and tries == 1)
        accepted = bool(candidate["ok"])
        attempts.append({
            "number": len(attempts) + 1, "eve": False, "accepted": accepted,
            "detected": candidate["eavesdropping_detected"], "qber": candidate["qber"],
            "qber_upper95": candidate["qber_upper95"], "sample_bits": candidate["sample_bits"],
            "secret_bits": candidate["secret_bits"], "backend": candidate["backend"],
            "source": candidate["source"], "trace": candidate.get("trace", {}),
            "disposition": "accepted" if accepted else "rejected; key discarded",
        })
        if accepted:
            clean = candidate
            break
    if clean is None:
        raise RuntimeError("Could not establish a clean quantum key right now. Please try again.")

    pool_id, pool_key = keybank.claim_login_key(user_id)
    key = _derive(clean["secret"], pool_key, user_id)
    latency_ms = (time.monotonic() - started) * 1000
    note = clean["fallback_note"] or (None if tries == 1 else f"The first {tries - 1} clean attempt(s) were rejected.")
    if eve_test:
        note = ((note + " ") if note else "") + "The Eve-injected first attempt was rejected and its key discarded."
    safe_attempts = attempts
    sid = db.execute(
        "INSERT INTO login_sessions(user_id,event,source,backend,job_ids,sessions,raw_qubits,sifted_bits,sample_bits,"
        "secret_bits,qber,qber_upper95,eve_tested,eve_detected,eve_qber,fallback_note,fingerprint,auth_key_hex,"
        "attempts_json,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (user_id, event, clean["source"], clean["backend"], json.dumps(clean["job_ids"][:5]),
         clean["sessions"], clean["raw_qubits"], clean["sifted_bits"], clean["sample_bits"],
         clean["secret_bits"], clean["qber"], clean["qber_upper95"], int(bool(eve_test)),
         int(bool(eve_info and eve_info["detected"])), eve_info["qber"] if eve_info else None,
         note, fingerprint(key), _seal_key(key), json.dumps(safe_attempts), latency_ms))
    account = db.one("SELECT email FROM users WHERE id=?", (user_id,))
    db.execute(
        "INSERT INTO login_audit(account,event,backend,job_id,secret_bits,error_rate,eve_tested,eve_detected,latency_ms,outcome) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (account["email"] if account else str(user_id), event, clean["backend"],
         (clean["job_ids"] or [None])[0], clean["secret_bits"], clean["qber"],
         int(bool(eve_test)), int(bool(eve_info and eve_info["detected"])), latency_ms, "accepted"))
    return public(sid), tag(key.hex(), sid, user_id)


def public(sid: int) -> dict:
    r = db.one("SELECT * FROM login_sessions WHERE id=?", (sid,))
    if not r:
        return {}
    from .auth import TOKEN_HOURS
    attempts = json.loads(r["attempts_json"] or "[]")
    return {
        "session_id": r["id"], "event": r["event"], "created": r["created"],
        "token_hours": TOKEN_HOURS, "status": "SECURE_KEY_ESTABLISHED",
        "eavesdropping_detected": False, "source": r["source"], "backend": r["backend"],
        "job_ids": json.loads(r["job_ids"] or "[]"), "sessions": r["sessions"],
        "raw_qubits": r["raw_qubits"], "sifted_bits": r["sifted_bits"],
        "sample_bits": r["sample_bits"], "secret_bits": r["secret_bits"],
        "qber": r["qber"], "qber_upper95": r["qber_upper95"], "qber_limit": QBER_LIMIT,
        "eve_test": {"ran": bool(r["eve_tested"]), "detected": bool(r["eve_detected"]), "qber": r["eve_qber"]},
        "attempts": attempts, "latency_ms": r["latency_ms"],
        "fallback_note": r["fallback_note"],
        "auth_key": {"fingerprint": r["fingerprint"], "tag": _group(tag(_key_hex(sid), sid, r["user_id"])[:16]),
                     "bits": 256, "kdf": "HKDF-SHA256 (server secret + fresh BB84 + pooled BB84 key)",
                     "mac": "HMAC-SHA256"},
    }


def _key_hex(sid: int) -> str:
    row = db.one("SELECT auth_key_hex FROM login_sessions WHERE id=?", (sid,))
    return _open_key(row["auth_key_hex"], sid).hex()


def history(user_id: int, limit: int = 6) -> list:
    rows = db.query("SELECT id,created,event,source,backend,qber,secret_bits,eve_tested,eve_detected,fingerprint,revoked,latency_ms "
                    "FROM login_sessions WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit))
    return [{**r, "eve_tested": bool(r["eve_tested"]), "eve_detected": bool(r["eve_detected"]),
             "revoked": bool(r["revoked"])} for r in rows]


def current(sid, user_id: int) -> dict:
    row = db.one("SELECT user_id FROM login_sessions WHERE id=?", (sid,)) if sid else None
    return public(sid) if row and row["user_id"] == user_id else {}


def test_exchange(eve: bool, use_ibm: bool = False) -> dict:
    r = exchange(eve=eve, use_ibm=use_ibm)
    return {k: r[k] for k in ("backend", "source", "job_ids", "sessions", "raw_qubits",
                              "sifted_bits", "sample_bits", "secret_bits", "qber", "qber_upper95",
                              "eve_present", "eavesdropping_detected", "fallback_note", "trace")} | {
        "qber_limit": QBER_LIMIT,
        "status": "EAVESDROPPER_DETECTED" if r["eavesdropping_detected"] else
                  ("SECURE_KEY_ESTABLISHED" if r["ok"] else "NOT_ENOUGH_CLEAN_BITS"),
        "key_discarded": bool(eve or not r["ok"]),
    }


def ibm_check() -> dict:
    from . import ibm
    out = {"configured": bool(config.IBM_TOKEN), "preferred_backend": config.IBM_BACKEND,
           "key_target": config.BANK_TARGET, "login_target": config.LOGIN_TARGET, "connected": False}
    if not config.IBM_TOKEN:
        out["message"] = "No IBM Quantum token is set on the server (IBM_QUANTUM_TOKEN in .env)."
        return out
    try:
        service = ibm.connect()
        backend, note = ibm.pick_backend(service, bb84_batch.QUBITS)
        st = backend.status()
        out.update(connected=True, backend=backend.name, qubits=backend.num_qubits,
                   operational=bool(st.operational), pending_jobs=int(st.pending_jobs),
                   message=note or f"{backend.name} is ready.")
    except Exception as ex:
        out["message"] = f"Could not reach IBM Quantum ({type(ex).__name__}: {str(ex)[:140]})."
    return out
