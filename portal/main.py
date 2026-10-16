"""QPortal web app: login, stock screener, quantum basket recommendation, encrypted history.

Run from the repository root:  uvicorn portal.main:app --port 8000
"""
import threading
import time
from pathlib import Path
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import analytics as an
from . import auth, config, db, jobs, keybank, prices, universe

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
    universe.seed_core()
    universe.refresh_from_nse_async()
    keybank.top_up_async()

    def warm():
        while True:
            try:
                prices.refresh(universe.core_symbols() + [universe.BENCHMARK])
            except Exception as exc:
                print(f"[prices] refresh failed: {exc}")
            time.sleep(6 * 3600)

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
    symbols: List[str]
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
        v = list(dict.fromkeys(s.strip().upper() for s in v if s and s.strip()))
        if not (config.MIN_SHORTLIST <= len(v) <= config.MAX_SHORTLIST):
            raise ValueError(f"Choose between {config.MIN_SHORTLIST} and {config.MAX_SHORTLIST} stocks.")
        return v

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
        px = an.clean(prices.get([symbol], need_benchmark=True))
    except prices.DataUnavailable as exc:
        raise HTTPException(503, str(exc))
    if symbol not in px.columns:
        raise HTTPException(404, "Not enough price history for this stock.")
    ret = px.pct_change().dropna(how="all")
    m = an.series_metrics(ret[symbol])
    cols = [symbol] + ([universe.BENCHMARK] if universe.BENCHMARK in px.columns else [])
    base = (px[cols].dropna() / px[cols].dropna().iloc[0]) * 100
    dates, series = an.thin(base.index, *[base[c] for c in cols])
    row = {**meta, **m, "price": float(px[symbol].dropna().iloc[-1])}
    return {"stock": row, "dates": dates, "stock_line": series[0], "benchmark_line": series[1] if len(series) > 1 else None}


@app.post("/api/recommend")
def recommend(body: Recommend, user=Depends(auth.current_user)):
    if body.k >= len(body.symbols):
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
