"""The recommendation job: prices -> risk/return -> quantum solver -> back-test -> encrypted result."""
import math
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import analytics as an
from . import combo, db, keybank, prices, progress, sector, solver, universe
from .universe import BENCHMARK

_pool = ThreadPoolExecutor(max_workers=2)
_key_pool = ThreadPoolExecutor(max_workers=2)

RISK_LABELS = [(0.35, "Safety first"), (0.65, "Balanced"), (1.01, "Growth first")]


def risk_label(q: float) -> str:
    return next(label for cap, label in RISK_LABELS if q < cap)


def submit(user: dict, req: dict) -> int:
    if req.get("mode") == "sector":
        title = f"Sector picks: {', '.join(req['sectors'])}, hold {req['k']} ({risk_label(req['q'])})"
    else:
        title = f"{len(req['symbols'])} stocks, pick {req['k']} ({risk_label(req['q'])})"
    run_id = db.execute("INSERT INTO runs(user_id,status,title) VALUES (?,?,?)", (user["id"], "running", title))
    _pool.submit(_run, run_id, user["id"], req)
    return run_id


def _fail(run_id: int, message: str):
    db.execute("UPDATE runs SET status='failed', error=? WHERE id=?", (message, run_id))


def _make_key(run_id: int):
    """Make the per-result BB84 key (one IBM job). Runs beside the QAOA step so the two IBM queue waits overlap."""
    progress.begin(run_id)
    try:
        progress.say("Making a fresh BB84 key on IBM")
        return keybank.make_key_batched(), None
    except Exception as exc:
        traceback.print_exc()
        return None, f"The IBM key step failed ({exc.__class__.__name__})."


def _run(run_id: int, user_id: int, req: dict):
    progress.begin(run_id)
    key_future = _key_pool.submit(_make_key, run_id) if req.get("hw_key") else None  # starts waiting in IBM's queue now
    try:
        progress.say("Loading prices and measuring risk and return")
        result = compute(req)
        key_id, key_note = None, None
        if key_future is not None:
            progress.say("Waiting for the BB84 key from IBM")
            ks, err = key_future.result()
            if err:
                key_note = err
            elif ks["status"] == "SECURE_KEY_ESTABLISHED":
                key_id = ks["key_id"]
            else:
                key_note = ks["reason"]
                result["key_attempt"] = {k2: ks[k2] for k2 in ("backend", "qber", "qber_upper95", "secret_bits", "job_ids")}
        if key_id is None:
            u = db.one("SELECT key_id FROM users WHERE id=?", (user_id,))
            key_id = u["key_id"] or keybank.assign_key(user_id)
        result["key"] = {**keybank.key_summary(key_id), "fresh_for_this_result": bool(req.get("hw_key")) and key_note is None,
                         "fallback_reason": key_note}
        progress.say("Encrypting and saving the result")
        enc = keybank.encrypt(user_id, key_id, result)
        db.execute("UPDATE runs SET status='done', key_id=?, nonce_hex=?, ciphertext_hex=? WHERE id=?",
                   (key_id, enc["nonce_hex"], enc["ciphertext_hex"], run_id))
    except prices.DataUnavailable as exc:
        _fail(run_id, str(exc))
    except Exception as exc:
        traceback.print_exc()
        _fail(run_id, f"Something went wrong while analysing these stocks ({exc.__class__.__name__}). Try again or pick different stocks.")
    finally:
        progress.end(run_id)


def _sector_prepare(req: dict) -> dict:
    """Sector mode: short-list the best-looking few stocks in each chosen sector (by return per unit of risk on the
    training period) so the quantum circuit stays small. The circuit then chooses among them."""
    names = req["sectors"]
    marks = ",".join("?" * len(names))
    rows = db.query(f"SELECT symbol,sector FROM stocks WHERE tier='core' AND symbol NOT LIKE '^%' AND sector IN ({marks})", tuple(names))
    by = {}
    for r in rows:
        by.setdefault(r["sector"], []).append(r["symbol"])
    allsyms = [s for v in by.values() for s in v]
    if not allsyms:
        raise prices.DataUnavailable("No stocks are tagged with those sectors yet.")
    pa = prices.get(allsyms)
    px = an.clean(pa[[s for s in allsyms if s in pa.columns]])
    ret = px.pct_change().iloc[1:]
    train = ret.iloc[:an.split_index(len(ret))]
    score = (train.mean() / train.std(ddof=1)).replace([np.inf, -np.inf], np.nan).dropna()
    per = max(2, min(3, 12 // len(names)))
    cands = {n: sorted([s for s in by.get(n, []) if s in score.index], key=lambda s: -score[s])[:per] for n in names}
    usable_sectors = [n for n in names if len(cands[n]) >= 1]
    if len(usable_sectors) < 2:
        raise prices.DataUnavailable("Not enough price history in those sectors. Choose sectors with more listed stocks.")
    return {**req, "sectors": usable_sectors, "symbols": [s for n in usable_sectors for s in cands[n]]}


def compute(req: dict) -> dict:
    sector_mode = req.get("mode") == "sector"
    if sector_mode:
        progress.say("Short-listing the strongest stocks in each sector")
        req = _sector_prepare(req)
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

    info = universe.info(usable)
    sector_info = None
    if sector_mode:
        names = [n for n in req["sectors"] if any(info.get(s, {}).get("sector") == n for s in usable)]
        members = {n: [i for i, s in enumerate(usable) if info.get(s, {}).get("sector") == n] for n in names}
        counts = sector.allocate_counts([req.get("sector_weights", {}).get(n, 1.0) for n in names], [len(members[n]) for n in names], k)
        groups = [(members[n], c) for n, c in zip(names, counts)]
        sol = sector.solve_sector(mu, sigma, k, q, groups, engine=req["engine"], reps=req["reps"], maxiter=req["maxiter"])
        sector_info = [{"name": n, "count": c, "weight": float(req.get("sector_weights", {}).get(n, 1.0)),
                        "candidates": [usable[i] for i in members[n]], "chosen": [usable[i] for i in sol["idx"] if i in members[n]]}
                       for n, c in zip(names, counts)]
    else:
        groups = [(list(range(len(usable))), k)]
        sol = solver.solve(mu, sigma, k, q, engine=req["engine"], reps=req["reps"], maxiter=req["maxiter"], mixer=req.get("mixer", "xy"))
    chosen = [usable[i] for i in sol["idx"]]
    exact_names = [usable[i] for i in sol["exact"]["idx"]]

    # the same instance through ordinary methods, for the fair comparison table
    progress.say("Comparing with classical methods")
    mu_s, sig_s = solver.normalise(mu, sigma)
    cmp_ = combo.compare(mu_s, sig_s, k, q, groups)
    cmp_rows = []
    if sol["engine"] != "classical":
        cmp_rows.append({"name": sol["label"], "kind": "quantum", "stocks": chosen, "fval": sol.get("objective_selected"),
                         "gap_pct": sol.get("objective_gap_pct"), "rank": sol.get("rank"), "matches_exact": sol.get("matches_exact"),
                         "evals": sol.get("train_evals", 0), "seconds": sol.get("qaoa_seconds", 0.0)})
    for r in cmp_["rows"]:
        cmp_rows.append({"name": r["name"], "kind": "classical", "stocks": [usable[i] for i in r["idx"]], "fval": r["fval"],
                         "gap_pct": r["gap_pct"], "rank": r["rank"], "matches_exact": r["matches_exact"],
                         "evals": r["evals"], "seconds": r["seconds"]})
    comparison = {"rows": cmp_rows, "feasible": cmp_["feasible"], "budget": cmp_["budget"], "qubits": sol["qubits"],
                  "note": "Cost is counted in objective evaluations (how many candidate baskets a method scored). The quantum row counts "
                          "circuit evaluations made while training on a classical simulator, so its seconds measure the simulator, "
                          "not a quantum computer. The quantum row keeps the best of 2,048 measurements; at this size random sampling with the same number "
                          "of shots would find the same basket, so the stronger test is how often the circuit puts the best basket first (see the paper). "
                          "Greedy, annealing and random search used the same objective and constraints."}

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
        "mode": "sector" if sector_mode else "basket", "sector": sector_info, "comparison": comparison,
        "chosen": chosen, "allocation": allocation, "cash_left": round(amount - sum(a["invest"] for a in allocation), 2),
        "quantum": {
            "label": sol["label"], "engine": sol["engine"], "backend": sol["backend"], "job_id": sol["job_id"],
            "qubits": sol["qubits"], "reps": sol["reps"], "mixer": sol["mixer"], "circuit_depth": sol["circuit_depth"], "cx_gates": sol["cx_gates"],
            "seconds": sol["seconds"], "exact_seconds": sol.get("exact_seconds"),
            "qaoa_seconds": sol.get("qaoa_seconds", 0.0),
            "objective_exact": sol.get("objective_exact"),
            "objective_selected": sol.get("objective_selected", sol.get("fval")),
            "objective_gap_pct": sol.get("objective_gap_pct"),
            "note": sol["note"], "feasible_baskets": sol["feasible_baskets"],
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
