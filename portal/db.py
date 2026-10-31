"""SQLite storage: users, stock universe, cached prices, encrypted runs, key bank."""
import sqlite3
import threading
from contextlib import contextmanager

from .config import DB_PATH

_lock = threading.RLock()
_conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
_conn.row_factory = sqlite3.Row
_conn.execute("PRAGMA journal_mode=WAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email TEXT UNIQUE NOT NULL,
  name TEXT,
  pw_salt TEXT NOT NULL,
  pw_hash TEXT NOT NULL,
  key_id INTEGER,
  created TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS stocks (
  symbol TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  sector TEXT NOT NULL DEFAULT 'Other',
  tier TEXT NOT NULL DEFAULT 'listed'
);
CREATE TABLE IF NOT EXISTS prices (
  symbol TEXT NOT NULL,
  d TEXT NOT NULL,
  close REAL NOT NULL,
  PRIMARY KEY (symbol, d)
);
CREATE TABLE IF NOT EXISTS price_meta (
  symbol TEXT PRIMARY KEY,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok'
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  created TEXT NOT NULL DEFAULT (datetime('now')),
  status TEXT NOT NULL,
  title TEXT,
  key_id INTEGER,
  nonce_hex TEXT,
  ciphertext_hex TEXT,
  error TEXT
);
CREATE TABLE IF NOT EXISTS keys (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT NOT NULL,
  backend TEXT,
  sessions INTEGER,
  raw_qubits INTEGER,
  sifted_bits INTEGER,
  secret_bits INTEGER,
  qber REAL,
  job_ids TEXT,
  material_hex TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INTEGER NOT NULL,
  created TEXT NOT NULL DEFAULT (datetime('now')),
  event TEXT NOT NULL,
  source TEXT NOT NULL,
  backend TEXT,
  job_ids TEXT,
  sessions INTEGER,
  raw_qubits INTEGER,
  sifted_bits INTEGER,
  sample_bits INTEGER,
  secret_bits INTEGER,
  qber REAL,
  qber_upper95 REAL,
  eve_tested INTEGER NOT NULL DEFAULT 0,
  eve_detected INTEGER NOT NULL DEFAULT 0,
  eve_qber REAL,
  fallback_note TEXT,
  fingerprint TEXT NOT NULL,
  auth_key_hex TEXT NOT NULL,
  attempts_json TEXT NOT NULL DEFAULT '[]',
  latency_ms REAL NOT NULL DEFAULT 0,
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_key_uses (
  key_id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL,
  used_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS live_auth_sessions (
  id TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL,
  created TEXT NOT NULL DEFAULT (datetime('now')),
  revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS bb84_live_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL DEFAULT '',
  session_id TEXT NOT NULL DEFAULT '',
  group_id TEXT NOT NULL DEFAULT '',
  event_time TEXT NOT NULL DEFAULT (datetime('now')),
  mode TEXT NOT NULL,
  qubits INTEGER NOT NULL,
  sifted_bits INTEGER NOT NULL DEFAULT 0,
  sample_size INTEGER NOT NULL DEFAULT 0,
  error_rate REAL,
  upper_bound REAL,
  eve_on INTEGER NOT NULL DEFAULT 0,
  verdict TEXT NOT NULL,
  attempt_number INTEGER NOT NULL,
  latency_ms REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  account TEXT NOT NULL,
  event_time TEXT NOT NULL DEFAULT (datetime('now')),
  event TEXT NOT NULL,
  backend TEXT,
  job_id TEXT,
  secret_bits INTEGER,
  error_rate REAL,
  eve_tested INTEGER NOT NULL DEFAULT 0,
  eve_detected INTEGER NOT NULL DEFAULT 0,
  latency_ms REAL NOT NULL DEFAULT 0,
  outcome TEXT NOT NULL
);
"""
with _lock:
    _conn.executescript(SCHEMA)
    live_existing = {r[1] for r in _conn.execute("PRAGMA table_info(bb84_live_audit)").fetchall()}
    if "session_id" not in live_existing:
        _conn.execute("ALTER TABLE bb84_live_audit ADD COLUMN session_id TEXT NOT NULL DEFAULT ''")
    if "group_id" not in live_existing:
        _conn.execute("ALTER TABLE bb84_live_audit ADD COLUMN group_id TEXT NOT NULL DEFAULT ''")
    existing = {r[1] for r in _conn.execute("PRAGMA table_info(login_sessions)").fetchall()}
    for name, ddl in {
        "attempts_json": "TEXT NOT NULL DEFAULT '[]'",
        "latency_ms": "REAL NOT NULL DEFAULT 0",
    }.items():
        if name not in existing:
            _conn.execute(f"ALTER TABLE login_sessions ADD COLUMN {name} {ddl}")
    _conn.commit()


@contextmanager
def tx():
    with _lock:
        try:
            yield _conn
            _conn.commit()
        except Exception:
            _conn.rollback()
            raise


def query(sql, params=()):
    with _lock:
        return [dict(r) for r in _conn.execute(sql, params).fetchall()]


def one(sql, params=()):
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    with tx() as c:
        cur = c.execute(sql, params)
        return cur.lastrowid


def executemany(sql, seq):
    with tx() as c:
        c.executemany(sql, seq)
