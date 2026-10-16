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
from .config import BANK_TARGET_KEYS, IBM_BACKEND, IBM_TOKEN, KEY_MIN_SECRET_BITS, KEY_TARGET, SERVER_SECRET

QBER_LIMIT = 0.11
SESSION_QUBITS = 64
MAX_SESSIONS = 60
_bank_lock = threading.Lock()


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
            (target, summary["backend"], sessions, raw, sifted_total, len(secret), qber, json.dumps(job_ids[:5]), material.hex()),
        )
    return summary


def unassigned_count() -> int:
    row = db.one("SELECT count(*) AS c FROM keys WHERE id NOT IN (SELECT key_id FROM users WHERE key_id IS NOT NULL)")
    return int(row["c"])


def top_up_async():
    """Keep a small stock of fresh keys ready so logins never wait for a quantum job."""
    def run():
        if not _bank_lock.acquire(blocking=False):
            return
        try:
            while unassigned_count() < BANK_TARGET_KEYS:
                if generate_key()["status"] != "SECURE_KEY_ESTABLISHED":
                    break
        except Exception as exc:
            print(f"[keybank] top-up failed: {exc}")
        finally:
            _bank_lock.release()

    threading.Thread(target=run, daemon=True).start()


def assign_key(user_id: int) -> int:
    """Give the user the oldest unused key (or make one now)."""
    with _bank_lock:
        row = db.one("SELECT id FROM keys WHERE id NOT IN (SELECT key_id FROM users WHERE key_id IS NOT NULL) ORDER BY id LIMIT 1")
        key_id = row["id"] if row else generate_key().get("key_id")
        if not key_id:
            raise RuntimeError("Could not create a secure key right now. Please try again.")
        db.execute("UPDATE users SET key_id=? WHERE id=?", (key_id, user_id))
    top_up_async()
    return key_id


def _user_key(key_id: int, user_id: int) -> bytes:
    row = db.one("SELECT material_hex FROM keys WHERE id=?", (key_id,))
    if not row:
        raise RuntimeError("Key not found.")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=SERVER_SECRET, info=f"qportal-user:{user_id}".encode()).derive(bytes.fromhex(row["material_hex"]))


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
