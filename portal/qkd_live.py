"""Browser/server BB84 live login mode.

Alice, the simulated quantum channel and Eve run on the server. Bob (the browser) chooses his
measurement bases FIRST and the channel then hands him his outcomes. The simulated qubit states, and
therefore Alice's bases and bits, never leave the server before the public discussion, so nobody (including
the browser) can read a basis or a bit off the wire. No derived key is sent to the server.

Reconciliation is idealised: both sides use Bob's measured bits for the key, as if error correction had
already removed the remaining channel errors. Privacy amplification is the HKDF step.
"""
from __future__ import annotations
import hashlib, hmac, secrets, threading, time, uuid
from typing import Any
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from . import db, keybank

N_QUBITS = 1600
DEMO_ROWS = 20  # the first positions are shown in the demo table, so they are never used for the key
CHANNEL_ERROR_RATE = 0.045
# Standard 30% sample. 1,600 qubits give about 800 sifted bits, so a 30% sample is about 240 bits; at 4.5%
# channel noise this keeps false rejections near 1% (800 qubits with a 30% sample falsely aborted 23.5%).
SAMPLE_FRACTION = 0.30
MIN_KEPT = int(N_QUBITS * 0.4)
QBER_LIMIT = 0.11
MAX_ATTEMPTS = 3
MIN_SECRET_BITS = 128
HKDF_INFO = b"qportal-bb84-live-v1"
PROOF_PREFIX = "qportal-live-login-v1"
_LOCK = threading.RLock()
_SESSIONS: dict[str, dict[str, Any]] = {}

def _bit(): return secrets.randbelow(2)
def _basis(): return secrets.randbelow(2)  # 0 rectilinear, 1 diagonal
def derive_key(bits: list[int], session_id: str) -> bytes:
    if len(bits) < MIN_SECRET_BITS: raise ValueError("Not enough secret bits to derive a login key.")
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=session_id.encode(), info=HKDF_INFO).derive(bytes(int(b)&1 for b in bits))
def fingerprint(key: bytes) -> str:
    d=hashlib.sha256(key).hexdigest()[:20].upper()
    return "-".join(d[i:i+4] for i in range(0,len(d),4))
def _mode_aliases(mode):
    # The live UI tab is "register"; the auth endpoint labels the same event "signup".
    m = (mode or "login").strip().lower()
    if m in ("signup", "register"):
        return ("signup", "register")
    return ("login",)
def proof_message(session_id, email, mode="login"):
    return f"{PROOF_PREFIX}|{mode}|{session_id}|{email.strip().lower()}".encode()
def make_proof(key, session_id, email, mode="login"):
    return hmac.new(key, proof_message(session_id,email,mode),hashlib.sha256).hexdigest()
def _audit(account, session_id, group_id, attempt, eve, verdict, latency, sifted=0, sample=0, qber=None, upper=None):
    db.execute("INSERT INTO bb84_live_audit(account,session_id,group_id,mode,qubits,sifted_bits,sample_size,error_rate,upper_bound,eve_on,verdict,attempt_number,latency_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
      (account.strip().lower()[:160],session_id,group_id,"live",N_QUBITS,sifted,sample,qber,upper,int(eve),verdict,attempt,round(latency,3)))
def start(eve_on=False, attempt=1, account="", group_id=""):
    if not 1 <= int(attempt) <= MAX_ATTEMPTS: raise ValueError("Attempt number must be between 1 and 3.")
    group_id = group_id or uuid.uuid4().hex
    started=time.monotonic(); sid=uuid.uuid4().hex
    alice_bits=[_bit() for _ in range(N_QUBITS)]; alice_bases=[_basis() for _ in range(N_QUBITS)]
    eve_bases=[None]*N_QUBITS; sent_bits=[]; sent_bases=[]; channel_flips=[]
    for i,(bit,basis) in enumerate(zip(alice_bits,alice_bases)):
        if eve_on:
            eb=_basis(); eve_bases[i]=eb; eve_bit=bit if eb==basis else _bit(); state_bit,state_basis=eve_bit,eb
        else: state_bit,state_basis=bit,basis
        flip=int(secrets.randbelow(10000)<450); channel_flips.append(flip)
        sent_bits.append(state_bit ^ flip); sent_bases.append(state_basis)
    with _LOCK:
        now = time.monotonic()
        for old_id in [k for k,v in _SESSIONS.items() if now-v["created"]>600]:
            _SESSIONS.pop(old_id, None)
        _SESSIONS[sid]={"session_id":sid,"created":time.monotonic(),"attempt":int(attempt),"account":account.strip().lower(),"group_id":group_id,
          "eve_on":bool(eve_on),"alice_bits":alice_bits,"alice_bases":alice_bases,"eve_bases":eve_bases,
          "sent_bits":sent_bits,"sent_bases":sent_bases,"channel_flips":channel_flips,"state":"started",
          "key":None,"used":False,"bob_bases":None,"bob_bits":None}
    return {"session_id":sid,"qubits":N_QUBITS,"demo_rows":DEMO_ROWS,
      "attempt":int(attempt),"eve_on":bool(eve_on),"channel_noise":CHANNEL_ERROR_RATE,
      "note":"Qubits are simulated in this demo. States stay on the server; Bob sends his bases first and receives his outcomes.","latency_ms":round((time.monotonic()-started)*1000,3)}
def _measure(s, bob_bases):
    """The simulated channel: Bob measures in his chosen basis. Same basis as the arriving state gives its bit,
    a different basis gives a random bit."""
    return [s["sent_bits"][i] if b==s["sent_bases"][i] else secrets.randbelow(2) for i,b in enumerate(bob_bases)]
def sift(session_id, bob_bases, account=""):
    started=time.monotonic()
    with _LOCK:
        s=_SESSIONS.get(session_id)
        if not s or s["state"]!="started" or time.monotonic()-s["created"]>600: raise ValueError("Unknown, expired, or already sifted BB84 session.")
        if not isinstance(bob_bases,list) or len(bob_bases)!=N_QUBITS or any(b not in (0,1) for b in bob_bases): raise ValueError(f"Bob must provide exactly {N_QUBITS} valid measurement bases.")
        s["bob_bases"]=list(bob_bases); s["bob_bits"]=_measure(s,bob_bases); kept=[i for i,(a,b) in enumerate(zip(s["alice_bases"],bob_bases)) if a==b]
        if len(kept)<MIN_KEPT:
            s["state"]="rejected"; _audit(account or s["account"],session_id,s["group_id"],s["attempt"],s["eve_on"],"insufficient_sifted_bits",(time.monotonic()-started)*1000,sifted=len(kept))
            raise ValueError("Too few matching bases. Start a fresh BB84 exchange.")
        sample_count=max(1,min(len(kept)-MIN_SECRET_BITS,round(len(kept)*SAMPLE_FRACTION)))
        sample_positions=sorted(secrets.SystemRandom().sample(kept,sample_count))
        s["kept_positions"]=kept; s["sample_positions"]=sample_positions; s["state"]="sifted"
        return {"session_id":session_id,"bob_bits":s["bob_bits"],"demo_rows":DEMO_ROWS,"alice_bases":s["alice_bases"],"sample_positions":sample_positions,
          "sifted_bits":len(kept),"sample_size":len(sample_positions),"attempt":s["attempt"],"eve_on":s["eve_on"],
          "trace":{"alice_bits":s["alice_bits"][:DEMO_ROWS],"alice_bases":s["alice_bases"][:DEMO_ROWS],"eve_bases":s["eve_bases"][:DEMO_ROWS] if s["eve_on"] else []},
          "latency_ms":round((time.monotonic()-started)*1000,3)}
def sample(session_id, bob_sample_bits, account=""):
    started=time.monotonic()
    with _LOCK:
        s=_SESSIONS.get(session_id)
        if not s or s["state"]!="sifted" or time.monotonic()-s["created"]>600: raise ValueError("Unknown, expired, or already sampled BB84 session.")
        positions=s["sample_positions"]
        if not isinstance(bob_sample_bits,list) or len(bob_sample_bits)!=len(positions) or any(b not in (0,1) for b in bob_sample_bits): raise ValueError("Bob's sample must contain one bit for every disclosed sample position.")
        errors=sum(int(bob_sample_bits[j]!=s["alice_bits"][pos]) for j,pos in enumerate(positions))
        n=len(positions); qber=errors/n if n else 1.0; upper=keybank._upper95(errors,n)
        verdict="secure" if upper<=QBER_LIMIT else "eavesdropper_detected"
        sample_set=set(positions)
        secret_positions=[p for p in s["kept_positions"] if p not in sample_set and p>=DEMO_ROWS]
        if verdict=="secure":
            # Idealised reconciliation: both sides use Bob's measured bits at the secret positions.
            bob_secret=[s["bob_bits"][p] for p in secret_positions]
            if len(bob_secret)<MIN_SECRET_BITS: verdict="insufficient_secret_bits"
            else:
                key=derive_key(bob_secret,session_id); s["key"]=key; s["state"]="secure"
                s["secret_bits"]=len(bob_secret); s["fingerprint"]=fingerprint(key)
        if verdict!="secure": s["key"]=None; s["state"]="detected" if verdict=="eavesdropper_detected" else "rejected"
        latency=(time.monotonic()-s["created"])*1000; s["qber"]=qber; s["upper_bound"]=upper; s["sample_size"]=n
        s["sifted_bits"]=len(s["kept_positions"]); s["account"]=(account or s["account"]).strip().lower()
        _audit(s["account"],session_id,s["group_id"],s["attempt"],s["eve_on"],verdict,latency,len(s["kept_positions"]),n,qber,upper)
        return {"session_id":session_id,"verdict":verdict,"sifted_bits":len(s["kept_positions"]),"sample_size":n,
          "errors":errors,"error_rate":qber,"upper_bound":upper,"limit":QBER_LIMIT,
          "secret_bits":len(secret_positions) if verdict=="secure" else 0,"fingerprint":s.get("fingerprint"),
          "eve_on":s["eve_on"],"latency_ms":round(latency,3),"note":"Qubits are simulated in this demo."}
def verify_and_consume(session_id,email,mode,proof):
    if not isinstance(proof,str) or len(proof)!=64: raise ValueError("A valid live BB84 proof is required.")
    with _LOCK:
        s=_SESSIONS.get(session_id)
        if not s or s["state"]!="secure" or s["used"] or not s.get("key") or time.monotonic()-s["created"]>600: raise ValueError("This BB84 session is not secure, is expired, or has already been used.")
        proof_hex = proof.lower()
        matched = False
        for alias in _mode_aliases(mode):
            expected = make_proof(s["key"], session_id, email, alias)
            matched = hmac.compare_digest(expected, proof_hex) or matched
        if not matched:
            raise ValueError("The live BB84 proof is invalid.")
        s["used"]=True; s["state"]="consumed"
        db.execute("UPDATE bb84_live_audit SET account=? WHERE group_id=?", (email.strip().lower(), s.get("group_id", "")))
        return s["key"]
def audit_rows(limit=5000, account=None):
    sql = "SELECT account,event_time,mode,qubits,sifted_bits,sample_size,error_rate,upper_bound,eve_on,verdict,attempt_number,latency_ms FROM bb84_live_audit"
    if account:
        return db.query(sql + " WHERE account=? ORDER BY id DESC LIMIT ?", (account.strip().lower(), max(1,min(int(limit),20000))))
    return db.query(sql + " ORDER BY id DESC LIMIT ?", (max(1,min(int(limit),20000)),))
def reset_sessions_for_tests():
    with _LOCK: _SESSIONS.clear()
