"""Email + password login with signed tokens (scrypt password hashing, HS256 JWT)."""
import hashlib
import hmac
import re
import secrets
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db, loginkey
from .config import SERVER_SECRET, LOGIN_MODE

TOKEN_HOURS = 12
_bearer = HTTPBearer(auto_error=False)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _hash(password: str, salt: bytes) -> bytes:
    # cryptography's scrypt matches hashlib.scrypt but also works on Pythons built without it
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    return Scrypt(salt=salt, length=32, n=2 ** 14, r=8, p=1).derive(password.encode())


def register(email: str, password: str, name: str = "") -> dict:
    email = email.strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(400, "Enter a valid email address.")
    if len(password) < 8:
        raise HTTPException(400, "Password must be at least 8 characters.")
    if db.one("SELECT id FROM users WHERE email=?", (email,)):
        raise HTTPException(409, "An account with this email already exists. Try logging in.")
    salt = secrets.token_bytes(16)
    uid = db.execute(
        "INSERT INTO users(email,name,pw_salt,pw_hash) VALUES (?,?,?,?)",
        (email, name.strip()[:60], salt.hex(), _hash(password, salt).hex()),
    )
    return {"id": uid, "email": email, "name": name.strip()[:60]}


def login(email: str, password: str) -> dict:
    row = db.one("SELECT * FROM users WHERE email=?", (email.strip().lower(),))
    ok = False
    if row:
        ok = hmac.compare_digest(_hash(password, bytes.fromhex(row["pw_salt"])).hex(), row["pw_hash"])
    if not ok:
        raise HTTPException(401, "Email or password is incorrect.")
    return row


def make_token(user: dict, sid: int, atag: str) -> str:
    """Login token. `sid` and `atag` tie it to this login's BB84 handshake and authentication key."""
    now = int(time.time())
    return jwt.encode(
        {"sub": str(user["id"]), "sid": sid, "atag": atag, "iat": now, "exp": now + TOKEN_HOURS * 3600},
        SERVER_SECRET,
        algorithm="HS256",
    )


def make_live_token(user: dict) -> str:
    """Issue the ordinary application JWT after the one-time live BB84 proof succeeds."""
    now = int(time.time())
    jti = secrets.token_urlsafe(24)
    db.execute("INSERT INTO live_auth_sessions(id,user_id) VALUES (?,?)", (jti, user["id"]))
    return jwt.encode({"sub": str(user["id"]), "sid": 0, "atag": "live", "jti": jti,
                       "iat": now, "exp": now + TOKEN_HOURS * 3600}, SERVER_SECRET, algorithm="HS256")


def current_user(cred: HTTPAuthorizationCredentials = Depends(_bearer)) -> dict:
    if not cred:
        raise HTTPException(401, "Please log in.")
    try:
        data = jwt.decode(cred.credentials, SERVER_SECRET, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(401, "Your session expired. Please log in again.")
    user = db.one("SELECT id,email,name,key_id FROM users WHERE id=?", (int(data["sub"]),))
    if not user:
        raise HTTPException(401, "Account not found.")
    # Support validating both live-mode tokens (with jti) and classic-mode tokens (with sid & atag)
    if "jti" in data:
        jti = data.get("jti")
        live_session = db.one("SELECT revoked FROM live_auth_sessions WHERE id=? AND user_id=?", (jti, user["id"])) if jti else None
        if not live_session or live_session["revoked"]:
            raise HTTPException(401, "Your live login session is no longer valid. Please sign in again.")
        user["live_sid"] = jti
    elif "sid" in data:
        if not loginkey.verify(data.get("sid"), user["id"], data.get("atag")):
            raise HTTPException(401, "Your secure session is no longer valid. Please log in again.")
    else:
        raise HTTPException(401, "Your secure session is no longer valid. Please log in again.")
    user["sid"] = int(data.get("sid", 0) or 0)
    return user
