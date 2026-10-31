"""Key bank: keys built from BB84 circuits, handed out to users and used to encrypt their saved results.

Design (and its honest limits):
  * BB84 runs as quantum circuits (Alice, Bob and an optional eavesdropper) on the Qiskit simulator or an IBM
    quantum processor. Alice and Bob are both on the server, so this is a protocol implementation, not a
    quantum link between the user and the server.
  * Many sessions are pooled until at least 256 secret bits exist, the pooled error rate is bounded with a
    95% confidence interval, and the bits are compressed with HKDF-SHA256.
  * Each user's key is HKDF(bank key, salt = server secret, info = user id). The server secret means the
    final key is never weaker than ordinary server-side key management; BB84 bits are added on top.
"""
import json
import secrets
import threading

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from . import db
from . import qlock
from .config import BANK_TARGET_KEYS, BANK_TARGET, IBM_BACKEND, IBM_TOKEN, KEY_MIN_SECRET_BITS, KEY_TARGET, SERVER_SECRET

QBER_LIMIT = 0.11
SESSION_QUBITS = 64
MAX_SESSIONS = 60
_bank_lock = threading.Lock()


def _seal_material(material: bytes) -> str:
    digest = hashes.Hash(hashes.SHA256())
    digest.update(SERVER_SECRET + b"|qportal-keybank-storage")
    key = digest.finalize()
    nonce = secrets.token_bytes(12)
    return "enc1:" + (nonce + AESGCM(key).encrypt(nonce, material, b"qportal-keybank")).hex()


def _open_material(value: str) -> bytes:
    if not value.startswith("enc1:"):
        # Legacy rows remain readable; every newly generated key is encrypted at rest.
        return bytes.fromhex(value)
    packed = bytes.fromhex(value[5:])
    digest = hashes.Hash(hashes.SHA256())
    digest.update(SERVER_SECRET + b"|qportal-keybank-storage")
    return AESGCM(digest.finalize()).decrypt(packed[:12], packed[12:], b"qportal-keybank")


def _pack(bits):
    out = bytearray((len(bits) + 7) // 8)
    for i, b in enumerate(bits):
        if b:
            out[i // 8] |= 1 << (i % 8)
    return bytes(out)


def _upper95(errors: int, n: int) -> float:
    """One-sided 95% upper bound on the error rate (Clopper-Pearson)."""
    if n <= 0:
        return 1.0
    if errors >= n:
        return 1.0
    from scipy.stats import beta
    return float(beta.ppf(0.95, errors + 1, n - errors))


def generate_key(target: str = None, enable_eve: bool = False) -> dict:
    """Run BB84 sessions on the dedicated Qiskit thread (hardware sessions wait on IBM from there too)."""
    return qlock.run(_generate_key, target, enable_eve)


def _generate_key(target: str = None, enable_eve: bool = False) -> dict:
    """Run BB84 sessions until enough secret bits exist. Returns a summary and stores the key unless aborted."""
    from qkd_bb84 import BB84Session  # lives at the repository root

    target = target or KEY_TARGET
    if target == "hardware" and not IBM_TOKEN:
        target = "simulator"
    secret, job_ids = [], []
    sessions = raw = sifted_total = sample_total = errors = 0
    while len(secret) < KEY_MIN_SECRET_BITS and sessions < MAX_SESSIONS:
        s = BB84Session(num_qubits=SESSION_QUBITS, enable_eve=enable_eve, backend_name=IBM_BACKEND)
        s.execute_on_ibm_qpu() if target == "hardware" else s.execute_local_simulation()
        sessions += 1
        raw += SESSION_QUBITS
        n_sift = len(s.alice_sifted_bits)
        sample = max(1, int(round(n_sift * 0.3)))
        sifted_total += n_sift
        sample_total += sample
        errors += sum(1 for a, b in zip(s.alice_sifted_bits[:sample], s.bob_sifted_bits[:sample]) if a != b)
        secret.extend(s.alice_sifted_bits[sample:])
        job_ids.append(s.job_id)
        if sample_total >= 20 and errors / sample_total > QBER_LIMIT:
            break  # clear sign of tampering or a failing channel: stop early
    qber = errors / sample_total if sample_total else 1.0
    upper = _upper95(errors, sample_total)
    ok = len(secret) >= KEY_MIN_SECRET_BITS and upper <= QBER_LIMIT
    summary = {
        "status": "SECURE_KEY_ESTABLISHED" if ok else "ABORTED",
        "reason": None if ok else ("Eavesdropping suspected: error rate above 11%." if qber > QBER_LIMIT else "Not enough clean key bits."),
        "backend": IBM_BACKEND if target == "hardware" else "Qiskit Aer simulator",
        "source": target, "sessions": sessions, "raw_qubits": raw, "sifted_bits": sifted_total,
        "secret_bits": len(secret), "qber": qber, "qber_upper95": upper, "eve": enable_eve,
        "job_ids": job_ids[:5],
    }
    if ok and not enable_eve:
        material = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"qportal-bb84-bank").derive(_pack(secret))
        summary["key_id"] = db.execute(
            "INSERT INTO keys(source,backend,sessions,raw_qubits,sifted_bits,secret_bits,qber,job_ids,material_hex) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (target, summary["backend"], sessions, raw, sifted_total, len(secret), qber, json.dumps(job_ids[:5]), _seal_material(material)),
        )
    return summary


def make_key_batched(execute=None) -> dict:
    """One BB84 key from ONE batched quantum job (see bb84_batch). Used when a user asks for a fresh quantum key
    for a single result. `execute` defaults to the IBM processor; tests pass a simulator."""
    from . import bb84_batch

    execute = execute or bb84_batch.execute_hardware
    out = bb84_batch.run(execute)
    secret = out["secret"]
    errors, sample = out["errors"], out["sample_bits"]
    qber = errors / sample if sample else 1.0
    upper = _upper95(errors, sample)
    ok = len(secret) >= KEY_MIN_SECRET_BITS and upper <= QBER_LIMIT
    source = "hardware" if out["backend"] != "Qiskit Aer simulator" else "simulator"
    stored_source = source + "-result"  # marks a key made for ONE result; the bank never hands it to anyone else
    summary = {
        "status": "SECURE_KEY_ESTABLISHED" if ok else "ABORTED",
        "reason": None if ok else ("Error rate too high to trust this key (hardware noise or tampering)." if upper > QBER_LIMIT else "Not enough clean key bits."),
        "backend": out["backend"], "source": source, "sessions": out["sessions"], "raw_qubits": out["raw_qubits"],
        "sifted_bits": out["sifted_bits"], "secret_bits": len(secret), "qber": qber, "qber_upper95": upper,
        "job_ids": [out["job_id"]] if out["job_id"] else [],
    }
    if ok:
        material = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"qportal-bb84-bank").derive(_pack(secret))
        summary["key_id"] = db.execute(
            "INSERT INTO keys(source,backend,sessions,raw_qubits,sifted_bits,secret_bits,qber,job_ids,material_hex) VALUES (?,?,?,?,?,?,?,?,?)",
            (stored_source, summary["backend"], out["sessions"], out["raw_qubits"], out["sifted_bits"], len(secret), qber,
             json.dumps(summary["job_ids"]), _seal_material(material)),
        )
    return summary


def key_summary(key_id: int) -> dict:
    """What to show the user about the key that protects a result (never the key itself)."""
    row = db.one("SELECT source,backend,sessions,raw_qubits,sifted_bits,secret_bits,qber,job_ids FROM keys WHERE id=?", (key_id,))
    if not row:
        return {}
    row["job_ids"] = json.loads(row["job_ids"] or "[]")
    row["source"] = row["source"].replace("-result", "")
    return row


def unassigned_count() -> int:
    row = db.one("SELECT count(*) AS c FROM keys WHERE source NOT LIKE '%-result' "
                 "AND id NOT IN (SELECT key_id FROM users WHERE key_id IS NOT NULL) "
                 "AND id NOT IN (SELECT key_id FROM login_key_uses)")
    return int(row["c"])


def top_up_async(target: str = None):
    """Keep a small stock of fresh keys ready so logins never wait for a quantum job."""
    def run():
        if not _bank_lock.acquire(blocking=False):
            return
        try:
            while unassigned_count() < BANK_TARGET_KEYS:
                if generate_key(target=target or BANK_TARGET)["status"] != "SECURE_KEY_ESTABLISHED":
                    break
        except Exception as exc:
            print(f"[keybank] top-up failed: {exc}")
        finally:
            _bank_lock.release()

    threading.Thread(target=run, daemon=True).start()


def assign_key(user_id: int) -> int:
    """Give the user the oldest unused key (or make one now)."""
    with _bank_lock:
        row = db.one("SELECT id FROM keys WHERE source NOT LIKE '%-result' "
                     "AND id NOT IN (SELECT key_id FROM users WHERE key_id IS NOT NULL) "
                     "AND id NOT IN (SELECT key_id FROM login_key_uses) ORDER BY id LIMIT 1")
        key_id = row["id"] if row else generate_key().get("key_id")
        if not key_id:
            raise RuntimeError("Could not create a secure key right now. Please try again.")
        db.execute("UPDATE users SET key_id=? WHERE id=?", (key_id, user_id))
    top_up_async()
    return key_id


def claim_login_key(user_id: int) -> tuple[int, bytes]:
    """Atomically consume a pre-generated key; replenish from simulator if the pool is empty."""
    with db.tx() as c:
        row = c.execute(
            "SELECT id,material_hex FROM keys WHERE source NOT LIKE '%-result' "
            "AND id NOT IN (SELECT key_id FROM login_key_uses) "
            "AND id NOT IN (SELECT key_id FROM users WHERE key_id IS NOT NULL) ORDER BY id LIMIT 1"
        ).fetchone()
        if row:
            key_id, stored = int(row["id"]), row["material_hex"]
            c.execute("INSERT INTO login_key_uses(key_id,user_id) VALUES (?,?)", (key_id, user_id))
            material = _open_material(stored)
            derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=SERVER_SECRET,
                           info=f"qportal-login-pool:{user_id}:{key_id}".encode()).derive(material)
            return key_id, derived
    # Pure-Python protocol model keeps authentication available when Qiskit/Aer is not installed.
    from . import bb84_batch
    out = bb84_batch.run_model(sessions=10, n=100, eve=False)
    errors, sample = out["errors"], out["sample_bits"]
    qber = errors / sample if sample else 1.0
    if len(out["secret"]) < KEY_MIN_SECRET_BITS or _upper95(errors, sample) > QBER_LIMIT:
        raise RuntimeError("The BB84 key pool is temporarily empty. Please retry.")
    material = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                    info=b"qportal-bb84-bank").derive(_pack(out["secret"]))
    db.execute(
        "INSERT INTO keys(source,backend,sessions,raw_qubits,sifted_bits,secret_bits,qber,job_ids,material_hex) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("simulator", out["backend"], out["sessions"], out["raw_qubits"], out["sifted_bits"],
         len(out["secret"]), qber, "[]", _seal_material(material)),
    )
    return claim_login_key(user_id)


def _user_key(key_id: int, user_id: int) -> bytes:
    row = db.one("SELECT material_hex FROM keys WHERE id=?", (key_id,))
    if not row:
        raise RuntimeError("Key not found.")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=SERVER_SECRET, info=f"qportal-user:{user_id}".encode()).derive(_open_material(row["material_hex"]))


def encrypt(user_id: int, key_id: int, payload: dict) -> dict:
    nonce = secrets.token_bytes(12)
    ct = AESGCM(_user_key(key_id, user_id)).encrypt(nonce, json.dumps(payload).encode(), f"run:{user_id}".encode())
    return {"nonce_hex": nonce.hex(), "ciphertext_hex": ct.hex()}


def decrypt(user_id: int, key_id: int, nonce_hex: str, ciphertext_hex: str) -> dict:
    pt = AESGCM(_user_key(key_id, user_id)).decrypt(bytes.fromhex(nonce_hex), bytes.fromhex(ciphertext_hex), f"run:{user_id}".encode())
    return json.loads(pt)


def status_for(user_id: int) -> dict:
    u = db.one("SELECT key_id FROM users WHERE id=?", (user_id,))
    key = db.one("SELECT id,created,source,backend,sessions,raw_qubits,sifted_bits,secret_bits,qber FROM keys WHERE id=?", (u["key_id"],)) if u and u["key_id"] else None
    runs = db.one("SELECT count(*) AS c FROM runs WHERE user_id=? AND status='done'", (user_id,))["c"]
    return {"key": key, "encrypted_runs": runs, "bank_ready": unassigned_count(), "qber_limit": QBER_LIMIT, "min_secret_bits": KEY_MIN_SECRET_BITS}
