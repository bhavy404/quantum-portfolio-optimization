"""The recommendation job: prices -> risk/return -> quantum solver -> back-test -> encrypted result."""
import math
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import analytics as an
from . import db, keybank, prices, solver, universe
from .universe import BENCHMARK

_pool = ThreadPoolExecutor(max_workers=2)

RISK_LABELS = [(0.35, "Safety first"), (0.65, "Balanced"), (1.01, "Growth first")]


def risk_label(q: float) -> str:
    return next(label for cap, label in RISK_LABELS if q < cap)


def submit(user: dict, req: dict) -> int:
    title = f"{len(req['symbols'])} stocks, pick {req['k']} ({risk_label(req['q'])})"
    run_id = db.execute("INSERT INTO runs(user_id,status,title) VALUES (?,?,?)", (user["id"], "running", title))
    _pool.submit(_run, run_id, user["id"], req)
    return run_id


def _fail(run_id: int, message: str):
    db.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (message, run_id))


def _run(run_id: int, user_id: int, req: dict):
    try:
        result = compute(req)
        u = db.one("SELECT key_id FROM users WHERE id=?", (user_id,))
        key_id = u["key_id"] or keybank.assign_key(user_id)
        enc = keybank.encrypt(user_id, key_id, result)
        db.execute("UPDATE runs SET status='done', key_id=?, nonce_hex=?, ciphertext_hex=? WHERE id=?",
                   (key_id, enc["nonce_hex"], enc["ciphertext_hex"], run_id))
    except prices.DataUnavailable as exc:
        _fail(run_id, str(exc))
    except Exception as exc:
        traceback.print_exc()
        _fail(run_id, f"Something went wrong while analysing these stocks ({exc.__class__.__name__}). Try again or pick different stocks.")


def compute(req: dict) -> dict:
    symbols, k, q, amount = req["symbols"], req["k"], req["q"], req["amount"]
    px_all = prices.get(symbols, need_benchmark=True)
    bench_px = px_all[BENCHMARK].dropna() if BENCHMARK in px_all.columns else None
    px = an.clean(px_all[[s for s in symbols if s in px_all.columns]])
    usable = [s for s in symbols if s in px.columns]
    dropped = [s for s in symbols if s not in usable]
    if len(usable) < max(k + 1, 3):
        raise prices.DataUnavailable("Not enough price history for these stocks. Choose stocks that have traded for at least a year.")
    px = px[usable].dropna()
    if len(px) < an.MIN_DAYS:
        raise prices.DataUnavailable("These stocks do not share enough trading days. Try a different mix.")
    ret = px.pct_change().dropna()
    cut = an.split_index(len(ret))
    train, test = ret.iloc[:cut], ret.iloc[cut:]
    mu, sigma = train.mean().values * an.TD, train.cov().values * an.TD

    sol = solver.solve(mu, sigma, k, q, engine=req["engine"], reps=req["reps"], maxiter=req["maxiter"])
    chosen = [usable[i] for i in sol["idx"]]
    exact_names = [usable[i] for i in sol["exact"]["idx"]]
    info = universe.info(usable)

    basket = an.basket_returns(ret, chosen)
    everything = an.basket_returns(ret, usable)
    bench = bench_px.pct_change().reindex(ret.index).fillna(0.0) if bench_px is not None else None
    series = [an.growth_series(basket), an.growth_series(everything)] + ([an.growth_series(bench)] if bench is not None else [])
    dates, grown = an.thin(ret.index, *series)
    dd_series = (an.growth_series(basket) / an.growth_series(basket).cummax() - 1.0) * 100
    _, (dd,) = an.thin(ret.index, dd_series)

    cloud = an.risk_return_cloud(mu, sigma, k)
    last = px.iloc[-1]
    per_stock = amount / k
    allocation = []
    for s in chosen:
        shares = int(per_stock // last[s])
        allocation.append({"symbol": s, "name": info.get(s, {}).get("name", s), "sector": info.get(s, {}).get("sector", "Other"),
                           "weight": 1.0 / k, "price": float(last[s]), "shares": shares, "invest": round(shares * float(last[s]), 2)})

    stocks = []
    for i, s in enumerate(usable):
        m = an.series_metrics(ret[s])
        stocks.append({"symbol": s, "name": info.get(s, {}).get("name", s), "sector": info.get(s, {}).get("sector", "Other"),
                       "annual_return": m["annual_return"], "volatility": m["volatility"], "ratio": m["ratio"],
                       "max_drawdown": m["max_drawdown"], "chosen": s in chosen})

    return {
        "params": {"symbols": usable, "k": k, "q": q, "risk_label": risk_label(q), "engine": req["engine"], "amount": amount},
        "dropped": dropped,
        "data": {"first": str(px.index[0].date()), "last": str(px.index[-1].date()), "days": int(len(px)),
                 "train_until": str(train.index[-1].date()), "test_from": str(test.index[0].date())},
        "chosen": chosen, "allocation": allocation, "cash_left": round(amount - sum(a["invest"] for a in allocation), 2),
        "quantum": {
            "label": sol["label"], "engine": sol["engine"], "backend": sol["backend"], "job_id": sol["job_id"],
            "qubits": sol["qubits"], "reps": sol["reps"], "circuit_depth": sol["circuit_depth"], "cx_gates": sol["cx_gates"],
            "seconds": sol["seconds"], "note": sol["note"], "feasible_baskets": sol["feasible_baskets"],
            "feasible_mass": sol["feasible_mass"], "matches_exact": sol["matches_exact"], "rank": sol["rank"],
            "shots": sol["shots"], "p_best": sol["p_best"], "p_best_blind": sol["p_best_blind"],
            "exact_choice": exact_names,
            "samples": [{"stocks": [usable[i] for i in s["idx"]], "prob": s["prob"], "feasible": s["feasible"], "rank": s["rank"]} for s in sol["samples"]],
        },
        "metrics": {
            "train": an.series_metrics(basket.iloc[:cut]), "test": an.series_metrics(basket.iloc[cut:]), "all": an.series_metrics(basket),
            "benchmark": an.series_metrics(bench) if bench is not None else None,
            "everything": an.series_metrics(everything),
        },
        "charts": {
            "dates": dates, "basket": grown[0], "everything": grown[1], "benchmark": grown[2] if bench is not None else None,
            "drawdown": dd, "split_date": str(test.index[0].date()),
            "corr_names": chosen if len(chosen) > 1 else usable, "corr": an.correlation(ret, chosen if len(chosen) > 1 else usable),
            "cloud": [{"r": round(r * 100, 2), "v": round(v * 100, 2), "chosen": list(c) == sol["idx"], "best": list(c) == sol["exact"]["idx"]} for r, v, c in cloud],
        },
        "stocks": stocks,
    }
