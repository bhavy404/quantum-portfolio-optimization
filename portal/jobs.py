"""The recommendation job: prices -> risk/return -> quantum solver -> back-test -> encrypted result."""
import math
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd

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
        hold = req.get("sector_k") or len(req.get("sectors") or [])
        title = f"QAOA sector mix: {len(req.get('sectors') or [])} candidates → {hold} sectors, {req['k']} stocks ({risk_label(req['q'])})"
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


def _sector_rows(names):
    marks = ",".join("?" * len(names))
    rows = db.query(f"SELECT symbol,sector FROM stocks WHERE tier='core' AND symbol NOT LIKE '^%' AND sector IN ({marks})", tuple(names))
    by = {}
    for r in rows:
        by.setdefault(r["sector"], []).append(r["symbol"])
    return by


def _sector_proxy_moments(names, by, train):
    """Equal-weight daily return of the stocks in each sector, then annualised mean and covariance."""
    kept, cols = [], []
    for name in names:
        syms = [s for s in by.get(name, []) if s in train.columns]
        if not syms:
            continue
        cols.append(train[syms].mean(axis=1))
        kept.append(name)
    if len(kept) < 2:
        return kept, None, None
    df = pd.concat(cols, axis=1)
    df.columns = kept
    df = df.dropna(how="any")
    if len(df) < 40:
        return list(df.columns), None, None
    mu = df.mean().values * an.TD
    sigma = df.cov().values * an.TD
    return list(df.columns), mu, sigma


def _qaoa_sector_mix(req: dict) -> dict:
    """QAOA picks which sectors to hold from the user's candidate list."""
    names = list(dict.fromkeys(req["sectors"]))
    by = _sector_rows(names)
    allsyms = [s for v in by.values() for s in v]
    if not allsyms:
        raise prices.DataUnavailable("No stocks are tagged with those sectors yet.")
    px_all = prices.get(allsyms)
    px = an.clean(px_all[[s for s in allsyms if s in px_all.columns]])
    ret = px.pct_change().dropna()
    train = ret.iloc[:an.split_index(len(ret))]
    kept, mu, sigma = _sector_proxy_moments(names, by, train)
    want = int(req.get("sector_k") or min(4, len(kept)))
    want = max(2, min(want, len(kept))) if kept else 0
    meta = {
        "candidates": names, "chosen": kept, "skipped": True, "engine": "classical", "qubits": 0,
        "label": "All candidate sectors kept", "qaoa_seconds": 0.0, "matches_exact": True,
        "note": "Not enough sector history to run QAOA on the mix; every candidate with prices is kept.",
    }
    if mu is None or len(kept) < 2:
        if len(kept) < 2:
            raise prices.DataUnavailable("Not enough price history in those sectors. Choose sectors with more listed stocks.")
        return meta
    if len(kept) > 12:
        sharpe = mu / np.sqrt(np.clip(np.diag(sigma), 1e-12, None))
        idx = np.argsort(-sharpe)[:12]
        kept = [kept[i] for i in idx]
        mu, sigma = mu[idx], sigma[np.ix_(idx, idx)]
        want = min(want, len(kept))
    mix_engine = "classical" if req.get("engine") == "classical" else "qaoa_sim"
    progress.say(f"Training QAOA on {len(kept)} sector proxies to pick {want}")
    sol = sector.pick_sectors(mu, sigma, want, req["q"], engine=mix_engine, reps=req.get("reps", 2), maxiter=req.get("maxiter", 100))
    chosen = [kept[i] for i in sol["idx"]]
    meta.update(candidates=kept, chosen=chosen, skipped=bool(sol.get("skipped")), engine=sol.get("engine"),
                qubits=sol.get("qubits", 0), label=sol.get("label"), qaoa_seconds=sol.get("qaoa_seconds", 0.0),
                matches_exact=sol.get("matches_exact"), note=sol.get("note") or "",
                objective_exact=sol.get("objective_exact"), objective_selected=sol.get("objective_selected"),
                objective_gap_pct=sol.get("objective_gap_pct"))
    if not meta["skipped"] and mix_engine != "classical":
        meta["label"] = "Sector mix QAOA (quantum circuit simulator)"
        meta["note"] = (meta.get("note") or "") or (
            f"QAOA searched combinations of {want} sectors out of {len(kept)} using equal-weight sector return and risk. "
            "The stock pick below then chooses names inside those sectors."
        )
    return meta


def _sector_prepare(req: dict) -> dict:
    """Short-list the best-looking few stocks in each chosen sector so the stock circuit stays small but has a choice."""
    names = req["sectors"]
    by = _sector_rows(names)
    allsyms = [s for v in by.values() for s in v]
    if not allsyms:
        raise prices.DataUnavailable("No stocks are tagged with those sectors yet.")
    pa = prices.get(allsyms)
    px = an.clean(pa[[s for s in allsyms if s in pa.columns]])
    ret = px.pct_change().iloc[1:]
    train = ret.iloc[:an.split_index(len(ret))]
    score = (train.mean() / train.std(ddof=1)).replace([np.inf, -np.inf], np.nan).dropna()
    usable_names = [n for n in names if any(s in score.index for s in by.get(n, []))]
    if len(usable_names) < 2:
        raise prices.DataUnavailable("Not enough price history in those sectors. Choose sectors with more listed stocks.")
    k = int(req["k"])
    per = sector.shortlist_per_sector(len(usable_names), k)
    cands = {n: sorted([s for s in by.get(n, []) if s in score.index], key=lambda s: -score[s])[:per] for n in usable_names}
    usable_sectors = [n for n in usable_names if len(cands[n]) >= 1]
    if len(usable_sectors) < 2:
        raise prices.DataUnavailable("Not enough price history in those sectors. Choose sectors with more listed stocks.")
    symbols = [s for n in usable_sectors for s in cands[n]]
    if k >= len(symbols) and len(symbols) > len(usable_sectors):
        k = len(symbols) - 1
    if k < len(usable_sectors):
        k = len(usable_sectors)
    return {**req, "sectors": usable_sectors, "symbols": symbols, "k": k}


def _sector_global_compare(req: dict, mix_meta: dict, usable: list, chosen: list, k: int, q: float):
    """Exact classical search over the WHOLE sector problem (every candidate sector, every stock combination),
    scored with the same objective as QAOA's final portfolio. Returns None if it cannot be built; the panel then
    falls back to the stock-step-only comparison."""
    cand_names = list((mix_meta or {}).get("candidates") or [])
    held = list(req["sectors"])
    others = [n for n in cand_names if n not in held]
    if not others:
        return None  # QAOA kept every candidate sector, so there was no sector choice to compare
    by = _sector_rows(cand_names)
    per = sector.shortlist_per_sector(len(held), k)
    pool = [s for n in others for s in by.get(n, [])]
    if not pool:
        return None
    pa = prices.get(pool)
    px_o = an.clean(pa[[s for s in pool if s in pa.columns]])
    ret_o = px_o.pct_change().iloc[1:]
    train_o = ret_o.iloc[:an.split_index(len(ret_o))]
    score = (train_o.mean() / train_o.std(ddof=1)).replace([np.inf, -np.inf], np.nan).dropna()
    members_syms = {n: [s for s in usable if s in by.get(n, [])] for n in held}  # the exact stocks QAOA could choose from
    for n in others:  # same short-list rule as the portal: best training-period return per unit of risk
        top = sorted([s for s in by.get(n, []) if s in score.index], key=lambda s: -score[s])[:per]
        if top:
            members_syms[n] = top
    members_syms = {n: v for n, v in members_syms.items() if v}
    universe_syms = [s for v in members_syms.values() for s in v]
    pg = prices.get(universe_syms)
    pxg = an.clean(pg[[s for s in universe_syms if s in pg.columns]])
    if any(s not in pxg.columns for s in chosen):
        return None
    universe_syms = [s for s in universe_syms if s in pxg.columns]
    pxg = pxg[universe_syms].dropna()
    if len(pxg) < an.MIN_DAYS:
        return None
    ret = pxg.pct_change().dropna()
    train = ret.iloc[:an.split_index(len(ret))]
    mu, sigma = train.mean().values * an.TD, train.cov().values * an.TD
    pos = {s: i for i, s in enumerate(universe_syms)}
    members = {n: [pos[s] for s in v if s in pos] for n, v in members_syms.items()}
    out = sector.global_baseline(mu, sigma, k, q, members, len(held), [pos[s] for s in chosen])
    if out is None:
        return None
    out["exact_stocks"] = [universe_syms[i] for i in out.pop("exact_idx")]
    return out


def compute(req: dict) -> dict:
    sector_mode = req.get("mode") == "sector"
    mix_meta = None
    if sector_mode:
        progress.say("Choosing the sector mix with QAOA")
        mix_meta = _qaoa_sector_mix(req)
        equal_w = 1.0 / max(len(mix_meta["chosen"]), 1)
        req = {**req, "sectors": mix_meta["chosen"], "sector_weights": {n: equal_w for n in mix_meta["chosen"]}}
        progress.say("Short-listing the strongest stocks in each chosen sector")
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
        total = sum(counts) or 1
        sector_info = [{"name": n, "count": c, "weight": c / total,
                        "candidates": [usable[i] for i in members[n]], "chosen": [usable[i] for i in sol["idx"] if i in members[n]]}
                       for n, c in zip(names, counts)]
    else:
        groups = [(list(range(len(usable))), k)]
        sol = solver.solve(mu, sigma, k, q, engine=req["engine"], reps=req["reps"], maxiter=req["maxiter"], mixer=req.get("mixer", "xy"))
    chosen = [usable[i] for i in sol["idx"]]
    exact_names = [usable[i] for i in sol["exact"]["idx"]]
    global_cmp = None
    if sector_mode and req["engine"] != "classical":
        try:
            progress.say("Checking against the full classical sector search")
            global_cmp = _sector_global_compare(req, mix_meta, usable, chosen, k, q)
        except Exception as exc:  # the extra check must never break the recommendation itself
            print("[sector global compare]", exc)

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
                          "Greedy, annealing and random search used the same objective and constraints."
                          + (" In sector mode this table compares methods on the stock-pick step, given the sector mix QAOA chose; "
                             "the end-to-end check over all candidate sectors is in the panel above." if sector_mode else "")}

    basket = an.basket_returns(ret, chosen)
    everything = an.basket_returns(ret, usable)
    bench = bench_px.pct_change().reindex(ret.index).fillna(0.0) if bench_px is not None else None
    series = [an.growth_series(basket), an.growth_series(everything)] + ([an.growth_series(bench)] if bench is not None else [])
    dates, grown = an.thin(ret.index, *series)
    dd_series = (an.growth_series(basket) / an.growth_series(basket).cummax() - 1.0) * 100
    _, (dd,) = an.thin(ret.index, dd_series)

    cloud = an.risk_return_cloud(mu, sigma, k)
    last = px.iloc[-1]
    sector_weight = {x["name"]: x["weight"] for x in (sector_info or [])}
    chosen_in = {}
    for s in chosen:
        chosen_in.setdefault(info.get(s, {}).get("sector", "Other"), []).append(s)
    allocation = []
    for s in chosen:
        sec = info.get(s, {}).get("sector", "Other")
        if sector_mode and sector_weight:
            n_here = max(len(chosen_in.get(sec, [])), 1)
            weight = float(sector_weight.get(sec, 0.0)) / n_here
        else:
            weight = 1.0 / k
        money = amount * weight
        shares = int(money // last[s]) if last[s] else 0
        allocation.append({"symbol": s, "name": info.get(s, {}).get("name", s), "sector": sec,
                           "weight": weight, "price": float(last[s]), "shares": shares, "invest": round(shares * float(last[s]), 2)})

    stocks = []
    for i, s in enumerate(usable):
        m = an.series_metrics(ret[s])
        stocks.append({"symbol": s, "name": info.get(s, {}).get("name", s), "sector": info.get(s, {}).get("sector", "Other"),
                       "annual_return": m["annual_return"], "volatility": m["volatility"], "ratio": m["ratio"],
                       "max_drawdown": m["max_drawdown"], "chosen": s in chosen})

    return {
        "params": {"symbols": usable, "k": k, "q": q, "risk_label": risk_label(q), "engine": req["engine"], "amount": amount,
                   "sector_k": req.get("sector_k")},
        "dropped": dropped,
        "data": {"first": str(px.index[0].date()), "last": str(px.index[-1].date()), "days": int(len(px)),
                 "train_until": str(train.index[-1].date()), "test_from": str(test.index[0].date())},
        "mode": "sector" if sector_mode else "basket", "sector": sector_info, "sector_mix": mix_meta, "global_compare": global_cmp, "comparison": comparison,
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
