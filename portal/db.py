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
"""
with _lock:
    _conn.executescript(SCHEMA)
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
