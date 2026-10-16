"""Email + password login with signed tokens (scrypt password hashing, HS256 JWT)."""
import hashlib
import hmac
import re
import secrets
import time

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from . import db
from .config import SERVER_SECRET

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


def make_token(user: dict) -> str:
    now = int(time.time())
    return jwt.encode(
        {"sub": str(user["id"]), "iat": now, "exp": now + TOKEN_HOURS * 3600},
        SERVER_SECRET,
        algorithm="HS256",
    )


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
    return user
