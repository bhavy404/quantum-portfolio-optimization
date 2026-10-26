"""Price cache. Daily closes are stored in SQLite so pages never wait on Yahoo Finance.

Real data comes from yfinance (an unofficial source: fine for a project, swap it for a licensed
feed before running a public service). QPORTAL_SYNTHETIC=1 generates fake prices for tests only.
"""
import hashlib
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from . import db
from .config import HISTORY_YEARS, PRICE_MAX_AGE_HOURS, SYNTHETIC
from .universe import BENCHMARK, SECTOR_BY_SYMBOL

MIN_DAYS = 200  # about 10 months of trading days needed to analyse a stock

# Intraday index quotes are deliberately kept separate from the daily-history cache.
# Yahoo Finance's unofficial intraday feed can be delayed or unavailable, so callers
# must show the quote timestamp and use a daily-close fallback when needed.
_INTRADAY_INDEX_CACHE = {"fetched_at": None, "data": None}
_INTRADAY_CACHE_SECONDS = 25


class DataUnavailable(Exception):
    pass


def _now():
    return datetime.now(timezone.utc)


def _synthetic(symbol: str) -> pd.Series:
    """Deterministic fake prices: a market factor + a sector factor + a stock-specific drift."""
    days = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=252 * HISTORY_YEARS)
    seed = lambda s: int(hashlib.sha256(s.encode()).hexdigest()[:8], 16)
    market = np.random.default_rng(1).normal(0.0004, 0.008, len(days))
    sector = SECTOR_BY_SYMBOL.get(symbol, "Other")
    sec = np.random.default_rng(seed(sector)).normal(0.0, 0.006, len(days))
    rng = np.random.default_rng(seed(symbol))
    drift = rng.uniform(-0.0002, 0.0012)
    idio = rng.normal(drift, rng.uniform(0.006, 0.02), len(days))
    ret = market + 0.7 * sec + idio if symbol != BENCHMARK else market + 0.3 * sec
    px = 100 * np.cumprod(1 + ret)
    return pd.Series(px, index=days)


def _download(symbols):
    """Return {symbol: Series} of daily closes."""
    if SYNTHETIC:
        return {s: _synthetic(s) for s in symbols}
    import yfinance as yf

    out = {}
    for i in range(0, len(symbols), 40):  # batch to stay polite to the data source
        chunk = symbols[i:i + 40]
        try:
            raw = yf.download(chunk, period=f"{HISTORY_YEARS}y", interval="1d",
                              auto_adjust=True, progress=False, threads=True)
        except Exception:
            continue
        if raw is None or raw.empty:
            continue
        close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": chunk[0]})
        for s in chunk:
            if s in close.columns:
                ser = close[s].dropna()
                if len(ser) >= MIN_DAYS:
                    out[s] = ser
    return out



def intraday_index_quotes(symbols):
    """Fetch near-live 1-minute index bars, cached briefly to avoid repeated Yahoo calls.

    Returns a dict keyed by ticker with {value, timestamp}. This is not an exchange-
    licensed real-time feed; Yahoo data may be delayed, missing, or rate limited.
    """
    global _INTRADAY_INDEX_CACHE
    symbols = list(dict.fromkeys(symbols))
    now = _now()
    cached_at = _INTRADAY_INDEX_CACHE.get("fetched_at")
    cached_data = _INTRADAY_INDEX_CACHE.get("data")
    if cached_at and cached_data is not None and (now - cached_at).total_seconds() < _INTRADAY_CACHE_SECONDS:
        return {s: cached_data[s] for s in symbols if s in cached_data}

    if SYNTHETIC:
        _INTRADAY_INDEX_CACHE = {"fetched_at": now, "data": {}}
        return {}

    result = {}
    try:
        import yfinance as yf
        raw = yf.download(symbols, period="1d", interval="1m", auto_adjust=False,
                          progress=False, threads=False, prepost=False)
        if raw is not None and not raw.empty:
            close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]].rename(columns={"Close": symbols[0]})
            for symbol in symbols:
                if symbol not in close.columns:
                    continue
                series = close[symbol].dropna()
                if series.empty:
                    continue
                stamp = series.index[-1]
                if hasattr(stamp, "to_pydatetime"):
                    stamp = stamp.to_pydatetime()
                if getattr(stamp, "tzinfo", None) is not None:
                    stamp = stamp.astimezone(timezone.utc)
                else:
                    # Yahoo intraday indexes are normally exchange-local; label unknown
                    # timezone data explicitly as UTC rather than implying live freshness.
                    stamp = stamp.replace(tzinfo=timezone.utc)
                result[symbol] = {"value": float(series.iloc[-1]), "timestamp": stamp.isoformat()}
    except Exception as exc:
        print(f"[prices] intraday index fetch failed: {exc}")

    _INTRADAY_INDEX_CACHE = {"fetched_at": now, "data": result}
    return result


def refresh(symbols, force=False):
    """Download and cache prices for symbols whose cache is missing or stale."""
    symbols = list(dict.fromkeys(symbols))
    limit = (_now() - timedelta(hours=PRICE_MAX_AGE_HOURS)).isoformat()
    marks = ",".join("?" * len(symbols)) or "''"
    fresh = {r["symbol"] for r in db.query(
        f"SELECT symbol FROM price_meta WHERE symbol IN ({marks}) AND updated_at > ?", (*symbols, limit))}
    todo = symbols if force else [s for s in symbols if s not in fresh]
    if not todo:
        return
    got = _download(todo)
    stamp = _now().isoformat()
    for s in todo:
        ser = got.get(s)
        if ser is None:
            db.execute("INSERT OR REPLACE INTO price_meta(symbol,updated_at,status) VALUES (?,?,?)", (s, stamp, "missing"))
            continue
        rows = [(s, idx.strftime("%Y-%m-%d"), float(v)) for idx, v in ser.items()]
        db.executemany("INSERT OR REPLACE INTO prices(symbol,d,close) VALUES (?,?,?)", rows)
        db.execute("INSERT OR REPLACE INTO price_meta(symbol,updated_at,status) VALUES (?,?,?)", (s, stamp, "ok"))


def load(symbols) -> pd.DataFrame:
    """Cached closes as a DataFrame (dates x symbols). Missing symbols are simply absent."""
    if not symbols:
        return pd.DataFrame()
    marks = ",".join("?" * len(symbols))
    rows = db.query(f"SELECT symbol,d,close FROM prices WHERE symbol IN ({marks}) ORDER BY d", tuple(symbols))
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).pivot(index="d", columns="symbol", values="close")
    df.index = pd.to_datetime(df.index)
    return df.sort_index()


def get(symbols, need_benchmark=False) -> pd.DataFrame:
    """Fetch (if needed) and return prices. Raises DataUnavailable when nothing can be loaded."""
    want = list(dict.fromkeys(list(symbols) + ([BENCHMARK] if need_benchmark else [])))
    refresh(want)
    df = load(want)
    if df.empty:
        raise DataUnavailable("Market data could not be loaded right now. Please try again in a few minutes.")
    return df


def last_updated():
    row = db.one("SELECT max(updated_at) AS t FROM price_meta WHERE status='ok'")
    return row["t"] if row else None
