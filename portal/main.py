"""QPortal web app: login, stock screener, quantum basket recommendation, encrypted history.

Run from the repository root:  uvicorn portal.main:app --port 8000
"""
import threading
import time
import csv
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator, model_validator

from . import analytics as an
from . import auth, config, db, jobs, keybank, prices, progress, universe

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="QPortal", version="2.0")


@app.exception_handler(Exception)
async def _unhandled(request, exc):
    import traceback
    from fastapi.responses import JSONResponse
    traceback.print_exc()
    return JSONResponse(status_code=500, content={"detail": f"Server error: {type(exc).__name__}: {str(exc)[:160]}"})
if config.ALLOWED_ORIGINS:
    app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])


# ----- start-up: load stock list, warm the price cache, stock up the key bank --------------------
@app.on_event("startup")
def _startup():
    db.execute("UPDATE runs SET status='failed', error=? WHERE status='running'", ("The server restarted before this analysis finished. Please run it again.",))
    universe.seed_core()
    universe.refresh_from_nse_async()
    keybank.top_up_async()

    def warm():
        while True:
            try:
                prices.refresh(universe.core_symbols() + universe.index_symbols(), force=True)
            except Exception as exc:
                print(f"[prices] refresh failed: {exc}")
            time.sleep(4 * 3600)

    threading.Thread(target=warm, daemon=True).start()


# ----- schemas ------------------------------------------------------------------------------------
class Signup(BaseModel):
    email: str
    password: str
    name: str = ""


class Login(BaseModel):
    email: str
    password: str


class Recommend(BaseModel):
    symbols: List[str] = []
    mode: str = "basket"  # "basket" = pick from the shortlist; "sector" = quantum pick inside chosen sectors
    sectors: List[str] = []
    sector_weights: dict = {}
    k: int = Field(ge=1, le=11)
    q: float = Field(ge=0.0, le=1.0)
    amount: float = Field(default=100000, ge=1000, le=1e9)
    engine: str = "qaoa_sim"
    reps: int = Field(default=2, ge=1, le=4)
    maxiter: int = Field(default=100, ge=20, le=300)
    mixer: str = "xy"
    hw_key: bool = False

    @field_validator("symbols")
    @classmethod
    def _syms(cls, v):
        return list(dict.fromkeys(s.strip().upper() for s in v if s and s.strip()))

    @model_validator(mode="after")
    def _check_mode(self):
        if self.mode == "sector":
            self.sectors = list(dict.fromkeys(s.strip() for s in self.sectors if s and s.strip()))
            if not (2 <= len(self.sectors) <= 5):
                raise ValueError("Choose between 2 and 5 sectors.")
            if self.k < len(self.sectors):
                raise ValueError("Hold at least one stock per sector you chose.")
            self.sector_weights = {str(a): float(b) for a, b in (self.sector_weights or {}).items() if str(a) in self.sectors}
        elif self.mode == "basket":
            if not (config.MIN_SHORTLIST <= len(self.symbols) <= config.MAX_SHORTLIST):
                raise ValueError(f"Choose between {config.MIN_SHORTLIST} and {config.MAX_SHORTLIST} stocks.")
        else:
            raise ValueError("Unknown mode.")
        return self

    @field_validator("mixer")
    @classmethod
    def _mixer(cls, v):
        if v not in ("xy", "x"):
            raise ValueError("Unknown circuit type.")
        return v

    @field_validator("engine")
    @classmethod
    def _engine(cls, v):
        if v not in ("qaoa_sim", "qaoa_hw", "classical"):
            raise ValueError("Unknown engine.")
        return v


# ----- public ------------------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return {"ok": True, "synthetic": config.SYNTHETIC, "ibm_configured": bool(config.IBM_TOKEN),
            "key_source": config.KEY_TARGET, "prices_updated": prices.last_updated(),
            "stocks_listed": db.one("SELECT count(*) AS c FROM stocks")["c"],
            "max_shortlist": config.MAX_SHORTLIST, "min_shortlist": config.MIN_SHORTLIST}


@app.post("/api/auth/register")
def register(body: Signup):
    user = auth.register(body.email, body.password, body.name)
    try:
        keybank.assign_key(user["id"])
    except Exception as exc:  # signup must not fail because of the key step; it is retried at first save
        import traceback
        traceback.print_exc()
        print(f"[register] key assignment deferred: {exc}")
    full = db.one("SELECT id,email,name FROM users WHERE id=?", (user["id"],))
    return {"token": auth.make_token(full), "user": full}


@app.post("/api/auth/login")
def login(body: Login):
    row = auth.login(body.email, body.password)
    return {"token": auth.make_token(row), "user": {"id": row["id"], "email": row["email"], "name": row["name"]}}


# ----- signed-in -----------------------------------------------------------------------------------
@app.get("/api/me")
def me(user=Depends(auth.current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"]}


@app.get("/api/stocks/search")
def search(q: str = Query(min_length=1, max_length=40), user=Depends(auth.current_user)):
    return universe.search(q)


@app.get("/api/market-indices")
def market_indices(user=Depends(auth.current_user)):
    """Return near-live one-minute index quotes when available, otherwise daily closes."""
    symbols = universe.index_symbols()
    labels = {universe.BENCHMARK: "NIFTY 50", universe.SENSEX: "BSE SENSEX"}

    # The daily cache provides previous-session closes for calculating the day change.
    try:
        daily = prices.get(symbols)
    except prices.DataUnavailable:
        daily = prices.load(symbols)

    intraday = prices.intraday_index_quotes(symbols)
    items = []
    timestamps = []
    for symbol in symbols:
        series = daily[symbol].dropna() if symbol in daily.columns else []
        quote = intraday.get(symbol)
        if quote:
            value = quote["value"]
            stamp = quote.get("timestamp")
            if stamp:
                timestamps.append(stamp)
            # If today's daily bar exists, compare intraday value with yesterday's
            # close. If the daily cache is older, its latest close is the prior close.
            quote_day = stamp[:10] if stamp else None
            last_daily_day = str(series.index[-1].date()) if len(series) else None
            if len(series) > 1 and quote_day and quote_day == last_daily_day:
                previous = float(series.iloc[-2])
            else:
                previous = float(series.iloc[-1]) if len(series) else None
        else:
            # Outside market hours or if Yahoo intraday data is unavailable, display
            # the latest daily close and label the overall response as a fallback.
            previous = float(series.iloc[-1]) if len(series) else None
            value = previous
            stamp = None
        change = value - previous if value is not None and previous is not None and quote else None
        change_pct = change / previous if change is not None and previous else None
        items.append({"symbol": symbol, "name": labels.get(symbol, symbol), "value": value,
                      "change": change, "change_pct": change_pct,
                      "quote_timestamp": stamp, "quote_type": "intraday" if quote else "daily_close"})

    mode = "intraday" if len(intraday) == len(symbols) and bool(symbols) else ("mixed" if intraday else "daily_close")
    as_of = max(timestamps) if timestamps else prices.last_updated()
    return {"as_of": as_of, "mode": mode, "refresh_seconds": 30, "indices": items,
            "notice": "Yahoo Finance intraday data may be delayed or unavailable; not a guaranteed real-time feed."}


# Fundamentals are fetched from Yahoo Finance on demand and cached in memory for six hours.
_FUNDAMENTALS_CACHE = {}
_FUNDAMENTALS_TTL = 6 * 3600
_FUNDAMENTAL_FIELDS = (
    ("pe_ratio", "trailingPE"), ("eps", "trailingEps"), ("pb_ratio", "priceToBook"),
    ("roe", "returnOnEquity"), ("roce", "returnOnCapitalEmployed"),
    ("debt_to_equity", "debtToEquity"), ("revenue_growth", "revenueGrowth"),
    ("net_profit_margin", "profitMargins"), ("dividend_yield", "dividendYield"),
    ("market_cap", "marketCap"),
)


# Mutual-fund data is sourced from MFAPI (which republishes Indian AMFI NAV data).
# Responses are cached in-process to avoid repeatedly requesting the same scheme history.
_MF_CACHE = {}
_MF_TTL = 3600


def _mf_json(path: str, timeout: int = 12):
    import json
    import urllib.request
    url = "https://api.mfapi.in/mf/" + path.lstrip("/")
    req = urllib.request.Request(url, headers={"User-Agent": "QPortal/1.0", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


@app.get("/api/mutual-funds/search")
def mutual_fund_search(q: str = Query(min_length=2, max_length=80), user=Depends(auth.current_user)):
    """Search Indian mutual-fund schemes by name using MFAPI."""
    import time as _time
    import urllib.parse
    key = "search:" + q.strip().lower()
    cached = _MF_CACHE.get(key)
    if cached and _time.time() - cached["at"] < _MF_TTL:
        return {"results": cached["data"], "source": "MFAPI", "cached": True}
    try:
        data = _mf_json("search?" + urllib.parse.urlencode({"q": q.strip()}))
        if not isinstance(data, list):
            data = []
        results = [{"scheme_code": int(item["schemeCode"]), "scheme_name": str(item.get("schemeName", ""))}
                   for item in data[:30] if item.get("schemeCode") and item.get("schemeName")]
        _MF_CACHE[key] = {"at": _time.time(), "data": results}
        return {"results": results, "source": "MFAPI", "cached": False}
    except Exception as exc:
        raise HTTPException(502, "Mutual-fund search is temporarily unavailable. Check your internet connection and try again.") from exc


@app.get("/api/mutual-funds/{scheme_code}")
def mutual_fund_details(scheme_code: int, user=Depends(auth.current_user)):
    """Return latest NAV and approximate trailing returns calculated from reported NAV history."""
    import datetime as _dt
    import math
    import time as _time
    if scheme_code <= 0:
        raise HTTPException(400, "Invalid mutual-fund scheme code.")
    key = "scheme:" + str(scheme_code)
    cached = _MF_CACHE.get(key)
    if cached and _time.time() - cached["at"] < _MF_TTL:
        return cached["data"]
    try:
        raw = _mf_json(str(scheme_code))
        meta = raw.get("meta") or {}
        nav_rows = raw.get("data") or []
        clean = []
        for point in nav_rows:
            try:
                date = _dt.datetime.strptime(point["date"], "%d-%m-%Y").date()
                nav = float(point["nav"])
                if math.isfinite(nav) and nav > 0:
                    clean.append((date, nav))
            except (KeyError, TypeError, ValueError):
                continue
        clean.sort(reverse=True)
        if not clean:
            raise HTTPException(404, "No NAV history is available for this scheme.")
        latest_date, latest_nav = clean[0]
        def trailing_return(years):
            target = latest_date - _dt.timedelta(days=365 * years)
            prior = next(((date, nav) for date, nav in clean if date <= target), None)
            if not prior or prior[1] <= 0:
                return None
            if years == 1:
                return latest_nav / prior[1] - 1
            elapsed_years = max((latest_date - prior[0]).days / 365.25, 0.01)
            return (latest_nav / prior[1]) ** (1 / elapsed_years) - 1
        item = {
            "scheme_code": scheme_code,
            "scheme_name": meta.get("scheme_name") or (nav_rows[0].get("scheme_name") if nav_rows else None) or str(scheme_code),
            "fund_house": meta.get("fund_house"), "scheme_type": meta.get("scheme_type"),
            "scheme_category": meta.get("scheme_category"), "latest_nav": latest_nav,
            "nav_date": latest_date.isoformat(), "returns": {"1y": trailing_return(1), "3y_cagr": trailing_return(3), "5y_cagr": trailing_return(5)},
            "history_points": len(clean), "source": "MFAPI / AMFI NAV history",
        }
        _MF_CACHE[key] = {"at": _time.time(), "data": item}
        return item
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, "Could not load this fund's NAV history right now. Please try again later.") from exc




# Local mutual-fund catalogue supplied with this project. It includes scheme metadata and
# a point-in-time NAV snapshot; historical returns continue to use MFAPI's NAV history.
_MF_CATALOG_PATH = Path(__file__).resolve().parent / "assets" / "mutual_fund_data.csv"
_MF_CATALOG = None


def _load_mf_catalog():
    global _MF_CATALOG
    if _MF_CATALOG is not None:
        return _MF_CATALOG
    rows = []
    if _MF_CATALOG_PATH.exists():
        with _MF_CATALOG_PATH.open("r", encoding="utf-8-sig", newline="") as handle:
            for raw in csv.DictReader(handle):
                try:
                    code = int(float(raw.get("Scheme_Code", "")))
                except (TypeError, ValueError):
                    continue
                name = (raw.get("Scheme_Name") or "").strip()
                if not name:
                    continue
                def num(key):
                    try:
                        value = float(raw.get(key, ""))
                        return value if value == value and abs(value) != float("inf") else None
                    except (TypeError, ValueError):
                        return None
                rows.append({
                    "scheme_code": code,
                    "scheme_name": name,
                    "display_name": (raw.get("Scheme_NAV_Name") or name).strip(),
                    "amc": (raw.get("AMC") or "").strip(),
                    "scheme_type": (raw.get("Scheme_Type") or "").strip(),
                    "scheme_category": (raw.get("Scheme_Category") or "").strip(),
                    "minimum_amount": num("Scheme_Min_Amt"),
                    "latest_nav": num("NAV"),
                    "nav_date": (raw.get("Latest_NAV_Date") or "").strip() or None,
                    "aum_crore": num("Average_AUM_Cr"),
                    "launch_date": (raw.get("Launch_Date") or "").strip() or None,
                })
    _MF_CATALOG = rows
    return rows


@app.get("/api/mutual-fund-catalog")
def mutual_fund_catalog(
    q: str = Query(default="", max_length=100),
    category: str = Query(default="", max_length=150),
    amc: str = Query(default="", max_length=150),
    scheme_type: str = Query(default="", max_length=80),
    limit: int = Query(default=300, ge=1, le=1000),
    user=Depends(auth.current_user),
):
    """Search the bundled mutual-fund dataset and provide dropdown filter options."""
    rows = _load_mf_catalog()
    categories = sorted({r["scheme_category"] for r in rows if r["scheme_category"]})
    amcs = sorted({r["amc"] for r in rows if r["amc"]})
    types = sorted({r["scheme_type"] for r in rows if r["scheme_type"]})
    query = q.strip().casefold()
    filtered = []
    for row in rows:
        if category and row["scheme_category"] != category:
            continue
        if amc and row["amc"] != amc:
            continue
        if scheme_type and row["scheme_type"] != scheme_type:
            continue
        if query and query not in row["scheme_name"].casefold() and query not in row["display_name"].casefold() and query not in row["amc"].casefold():
            continue
        filtered.append(row)
    filtered.sort(key=lambda r: (r["scheme_name"].casefold(), r["display_name"].casefold()))
    return {
        "results": filtered[:limit],
        "total": len(filtered),
        "categories": categories,
        "amcs": amcs,
        "scheme_types": types,
        "source": "Bundled mutual_fund_data.csv",
         "nav_snapshot_date": max((r["nav_date"] for r in rows if r["nav_date"]), default=None),
        "nav_snapshot_note": "The dataset's NAV snapshot may be older than today's NAV; open a scheme comparison to fetch its reported NAV history.",
    }


_SECTOR_RECOMMENDATIONS_CACHE = {"at": 0.0, "data": None}
_SECTOR_RECOMMENDATIONS_TTL = 6 * 3600


@app.get("/api/sector-recommendations")
def sector_recommendations(user=Depends(auth.current_user)):
    """Suggest sector weights from observed 3-month and 1-year price momentum."""
    now = time.time()
    cached = _SECTOR_RECOMMENDATIONS_CACHE
    if cached["data"] is not None and now - cached["at"] < _SECTOR_RECOMMENDATIONS_TTL:
        return cached["data"]

    grouped = {}
    for row in db.query("SELECT symbol,name,sector FROM stocks WHERE tier='core' AND symbol NOT LIKE '^%' ORDER BY sector,name"):
        grouped.setdefault(row["sector"], []).append({"symbol": row["symbol"], "name": row["name"]})

    all_symbols = [stock["symbol"] for stocks in grouped.values() for stock in stocks]
    history = None
    try:
        history = prices.get(all_symbols)
    except Exception as exc:
        print(f"[sector recommendations] price history unavailable: {exc}")
        try:
            history = prices.load(all_symbols)
        except Exception:
            history = None

    metrics = []
    for name, stocks in sorted(grouped.items()):
        returns_3m, returns_1y = [], []
        if history is not None and not history.empty:
            for stock in stocks:
                series = history[stock["symbol"]].dropna() if stock["symbol"] in history.columns else None
                if series is None or len(series) < 2:
                    continue
                last = float(series.iloc[-1])
                if last <= 0:
                    continue
                if len(series) >= 64 and float(series.iloc[-64]) > 0:
                    returns_3m.append(last / float(series.iloc[-64]) - 1)
                if len(series) >= 253 and float(series.iloc[-253]) > 0:
                    returns_1y.append(last / float(series.iloc[-253]) - 1)
        r3 = sum(returns_3m) / len(returns_3m) if returns_3m else None
        r1 = sum(returns_1y) / len(returns_1y) if returns_1y else None
        available = [x for x in (r3, r1) if x is not None]
        available_weight = (0.35 if r3 is not None else 0.0) + (0.65 if r1 is not None else 0.0)
        momentum = ((0.35 * r3 if r3 is not None else 0.0) + (0.65 * r1 if r1 is not None else 0.0)) / available_weight if available_weight else None
        metrics.append({
            "name": name, "stock_count": len(stocks), "stocks": stocks[:4],
            "return_3m": r3, "return_1y": r1, "momentum": momentum if available else None,
            "data_points": len(available),
        })

    any_history = any(item["momentum"] is not None for item in metrics)
    # Convert historical momentum to a positive score, then normalize to 100%.
    # The small floor prevents a weak/recently negative sector from disappearing entirely.
    for item in metrics:
        item["score"] = max(0.05, 1.0 + 3.0 * (item["momentum"] or 0.0)) if any_history else 1.0
    score_total = sum(item["score"] for item in metrics) or 1.0
    for item in metrics:
        item["suggested_weight"] = item["score"] / score_total
    result = {
        "sectors": metrics,
        "method": "Suggested weights use the average available 3-month (35%) and 1-year (65%) price returns of QPortal's curated starter stocks per sector. Weights are normalized to 100%.",
        "source": "QPortal cached daily stock prices",
        "synthetic": bool(config.SYNTHETIC),
        "as_of": prices.last_updated(),
        "note": "Historical price momentum is not a reliable forecast of future sector returns. Sector tags and coverage are approximate; this is an educational allocation estimate, not personalized investment advice.",
    }
    _SECTOR_RECOMMENDATIONS_CACHE.update({"at": now, "data": result})
    return result


@app.get("/api/sector-universe")
def sector_universe(user=Depends(auth.current_user)):
    """Return QPortal's curated stocks grouped by their tagged sector for portfolio construction."""
    rows = db.query("SELECT symbol,name,sector FROM stocks WHERE tier='core' AND symbol NOT LIKE '^%' ORDER BY sector,name")
    grouped = {}
    for row in rows:
        grouped.setdefault(row["sector"], []).append({"symbol": row["symbol"], "name": row["name"]})
    return {"sectors": [{"name": name, "stocks": stocks} for name, stocks in sorted(grouped.items())],
            "note": "Sector tags use QPortal's curated starter universe; they are broad classifications, not official exchange sector indices."}


@app.get("/api/fundamentals")
def fundamentals(symbols: str = Query(min_length=1, max_length=500), user=Depends(auth.current_user)):
    """Fetch up to eight companies' fundamentals, with a six-hour in-process cache."""
    import math
    from datetime import datetime, timezone
    import yfinance as yf

    requested = list(dict.fromkeys(x.strip().upper() for x in symbols.split(",") if x.strip()))
    if not requested or len(requested) > 8:
        raise HTTPException(400, "Choose between 1 and 8 companies to compare.")
    known = universe.info(requested)
    unknown = [sym for sym in requested if sym not in known or sym.startswith("^")]
    if unknown:
        raise HTTPException(400, "Fundamentals are available for individual listed companies, not indices. Unknown: " + ", ".join(unknown))

    now = time.time()
    results = []
    for symbol in requested:
        cached = _FUNDAMENTALS_CACHE.get(symbol)
        if cached and now - cached["cached_at"] < _FUNDAMENTALS_TTL:
            results.append(cached["data"])
            continue
        meta = known[symbol]
        try:
            info = yf.Ticker(symbol).info or {}
            values = {}
            for key, source_key in _FUNDAMENTAL_FIELDS:
                value = info.get(source_key)
                if value is not None:
                    try:
                        value = float(value)
                        if not math.isfinite(value):
                            value = None
                    except (TypeError, ValueError):
                        value = None
                values[key] = value
            # Prefer the company name/sector already in QPortal; Yahoo values may be missing.
            item = {"symbol": symbol, "name": meta["name"], "sector": meta["sector"],
                    "currency": info.get("currency", "INR"), "values": values,
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                    "available": any(v is not None for v in values.values())}
            _FUNDAMENTALS_CACHE[symbol] = {"cached_at": now, "data": item}
        except Exception as exc:
            item = {"symbol": symbol, "name": meta["name"], "sector": meta["sector"],
                    "currency": "INR", "values": {key: None for key, _ in _FUNDAMENTAL_FIELDS},
                    "fetched_at": None, "available": False, "error": str(exc)[:120]}
        results.append(item)
    return {"items": results, "metrics": [key for key, _ in _FUNDAMENTAL_FIELDS],
            "cache_ttl_seconds": _FUNDAMENTALS_TTL,
            "notice": "Fundamentals are sourced from Yahoo Finance, may be delayed or missing, and are not investment advice. ROCE may be unavailable from the data provider."}


@app.get("/api/screener")
def screener(user=Depends(auth.current_user)):
    core = universe.core_symbols()
    if not db.one("SELECT 1 AS x FROM price_meta WHERE status='ok' LIMIT 1"):
        try:
            prices.refresh(core + [universe.BENCHMARK])
        except Exception:
            pass
    px = prices.load(core)
    if px.empty:
        raise HTTPException(503, "Market data is still loading. Try again in a minute.")
    rows = an.stock_table(px, universe.info(core))
    return {"as_of": prices.last_updated(), "rows": rows}


@app.get("/api/stocks/{symbol}")
def stock_detail(symbol: str, user=Depends(auth.current_user)):
    symbol = symbol.upper()
    meta = universe.info([symbol]).get(symbol)
    if not meta:
        raise HTTPException(404, "Unknown stock.")
    try:
        px = an.clean(prices.get([symbol, universe.SENSEX] if symbol == universe.BENCHMARK else [symbol], need_benchmark=True))
    except prices.DataUnavailable as exc:
        raise HTTPException(503, str(exc))
    if symbol not in px.columns:
        raise HTTPException(404, "Not enough price history for this stock.")
    ret = px.pct_change().dropna(how="all")
    m = an.series_metrics(ret[symbol])
    benchmark_symbol = universe.SENSEX if symbol == universe.BENCHMARK else universe.BENCHMARK
    cols = list(dict.fromkeys([symbol] + ([benchmark_symbol] if benchmark_symbol in px.columns else [])))
    base = (px[cols].dropna() / px[cols].dropna().iloc[0]) * 100
    dates, series = an.thin(base.index, *[base[c] for c in cols])
    row = {**meta, **m, "price": float(px[symbol].dropna().iloc[-1])}
    return {"stock": row, "dates": dates, "stock_line": series[0], "benchmark_line": series[1] if len(series) > 1 else None}


@app.post("/api/recommend")
def recommend(body: Recommend, user=Depends(auth.current_user)):
    if body.mode == "basket" and body.k >= len(body.symbols):
        raise HTTPException(400, "Pick fewer stocks to hold than you shortlisted.")
    if body.engine == "qaoa_hw" and not config.IBM_TOKEN:
        raise HTTPException(400, "The IBM quantum processor is not available on this server. Choose the simulator.")
    if body.hw_key and not config.IBM_TOKEN:
        raise HTTPException(400, "The IBM quantum processor is not available on this server.")
    return {"run_id": jobs.submit(user, body.model_dump())}


@app.get("/api/runs")
def list_runs(user=Depends(auth.current_user)):
    return db.query("SELECT id,created,status,title FROM runs WHERE user_id=? ORDER BY id DESC LIMIT 100", (user["id"],))


@app.get("/api/runs/{run_id}")
def get_run(run_id: int, user=Depends(auth.current_user)):
    row = db.one("SELECT * FROM runs WHERE id=? AND user_id=?", (run_id, user["id"]))
    if not row:
        raise HTTPException(404, "Result not found.")
    out = {"id": row["id"], "created": row["created"], "status": row["status"], "title": row["title"], "error": row["error"]}
    if row["status"] == "running":
        out["stage"] = progress.get(run_id)
    if row["status"] == "done":
        try:
            out["result"] = keybank.decrypt(user["id"], row["key_id"], row["nonce_hex"], row["ciphertext_hex"])
        except Exception:
            raise HTTPException(500, "This saved result could not be decrypted.")
    return out


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: int, user=Depends(auth.current_user)):
    db.execute("DELETE FROM runs WHERE id=? AND user_id=?", (run_id, user["id"]))
    return {"ok": True}


@app.get("/api/security/status")
def security_status(user=Depends(auth.current_user)):
    return keybank.status_for(user["id"])


@app.post("/api/security/rotate")
def rotate(user=Depends(auth.current_user)):
    keybank.assign_key(user["id"])
    return keybank.status_for(user["id"])


@app.post("/api/security/attack-check")
def attack_check(user=Depends(auth.current_user)):
    """Run BB84 with an eavesdropper on the line to show that tampering is detected. Nothing is stored."""
    return keybank.generate_key(target="simulator", enable_eve=True)


# ----- the website ---------------------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
