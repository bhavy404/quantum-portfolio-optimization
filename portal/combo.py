"""Classical methods on the same basket problem, so QAOA can be compared fairly.

A "group layout" says which stocks may be swapped with which: a list of (stock indices, how many to hold).
A plain basket problem is one group holding all stocks; a sector problem is one group per sector.
Every method below sees the same normalised mu/sigma, the same objective and the same constraints.

Cost is counted in OBJECTIVE EVALUATIONS (how many candidate baskets a method scored), not only in seconds,
because run time on a simulator or an IBM queue says nothing about the algorithm itself.
"""
import itertools
import math
import time

import numpy as np

from .solver import objective


def _basket(n, idx):
    x = np.zeros(n)
    x[list(idx)] = 1.0
    return x


def feasible_baskets(groups):
    """Every valid basket (sorted tuple of stock indices) under the group counts."""
    parts = [list(itertools.combinations(sorted(g), c)) for g, c in groups]
    return [tuple(sorted(sum(p, ()))) for p in itertools.product(*parts)]


def ranked(mu_s, sig_s, k, q, groups):
    n = len(mu_s)
    rows = [(objective(_basket(n, b), mu_s, sig_s, k, q), b) for b in feasible_baskets(groups)]
    rows.sort(key=lambda t: t[0])
    return rows


def _row(name, idx, fval, evals, seconds, rank_of, best):
    idx = tuple(sorted(idx))
    return {"name": name, "idx": list(idx), "fval": float(fval), "evals": int(evals), "seconds": round(seconds, 4),
            "rank": rank_of.get(idx), "matches_exact": idx == best[1],
            "gap_pct": (100.0 * (fval - best[0]) / abs(best[0])) if abs(best[0]) > 1e-12 else None}


def greedy(mu_s, sig_s, k, q, groups):
    """Add the single best stock at a time (respecting each group's count)."""
    n = len(mu_s)
    left = {gi: c for gi, (_, c) in enumerate(groups)}
    owner = {i: gi for gi, (g, _) in enumerate(groups) for i in g}
    chosen, evals = [], 0
    for _ in range(k):
        best = None
        for i in owner:
            if i in chosen or left[owner[i]] == 0:
                continue
            x = _basket(n, chosen + [i])
            f = objective(x, mu_s, sig_s, len(chosen) + 1, q)
            evals += 1
            if best is None or f < best[0]:
                best = (f, i)
        chosen.append(best[1])
        left[owner[best[1]]] -= 1
    return chosen, evals


def _random_feasible(groups, rng):
    out = []
    for g, c in groups:
        out += list(rng.choice(sorted(g), size=c, replace=False))
    return [int(v) for v in out]


def random_search(mu_s, sig_s, k, q, groups, budget, seed=0):
    rng = np.random.default_rng(seed)
    n = len(mu_s)
    best = None
    for _ in range(budget):
        b = _random_feasible(groups, rng)
        f = objective(_basket(n, b), mu_s, sig_s, k, q)
        if best is None or f < best[0]:
            best = (f, b)
    return best[1], budget


def annealing(mu_s, sig_s, k, q, groups, budget, seed=0):
    """Simulated annealing: swap one held stock for one not held in the same group; accept worse swaps less over time."""
    rng = np.random.default_rng(seed)
    n = len(mu_s)
    owner = {i: gi for gi, (g, _) in enumerate(groups) for i in g}
    cur = _random_feasible(groups, rng)
    fcur = objective(_basket(n, cur), mu_s, sig_s, k, q)
    best, fbest, evals = list(cur), fcur, 1
    t0, t1 = 0.25 * (abs(fcur) + 1e-3), 1e-4
    for step in range(max(1, budget - 1)):
        t = t0 * (t1 / t0) ** (step / max(1, budget - 2))
        out_i = int(rng.choice(cur))
        pool = [i for i in groups[owner[out_i]][0] if i not in cur]
        if not pool:
            continue
        new = [i for i in cur if i != out_i] + [int(rng.choice(pool))]
        fnew = objective(_basket(n, new), mu_s, sig_s, k, q)
        evals += 1
        if fnew < fcur or rng.random() < math.exp(-(fnew - fcur) / t):
            cur, fcur = new, fnew
            if fcur < fbest:
                best, fbest = list(cur), fcur
    return best, evals


def compare(mu_s, sig_s, k, q, groups, budget=None, seed=0):
    """Exact, greedy, simulated annealing and random search on one instance. Budget (evaluations) is shared by the
    two sampling methods and defaults to 10% of all valid baskets (at least 40)."""
    t = time.time()
    table = ranked(mu_s, sig_s, k, q, groups)
    t_exact = time.time() - t
    rank_of = {b: r + 1 for r, (_, b) in enumerate(table)}
    best = table[0]
    budget = budget or max(40, len(table) // 10)
    rows = [_row("Exact search (all baskets)", best[1], best[0], len(table), t_exact, rank_of, best)]
    for name, fn, args in (("Greedy (one stock at a time)", greedy, ()),
                           ("Simulated annealing", annealing, (budget, seed)),
                           ("Random search", random_search, (budget, seed))):
        t = time.time()
        idx, evals = fn(mu_s, sig_s, k, q, groups, *args)
        f = objective(_basket(len(mu_s), idx), mu_s, sig_s, k, q)
        rows.append(_row(name, idx, f, evals, time.time() - t, rank_of, best))
    return {"rows": rows, "feasible": len(table), "budget": budget, "best": {"idx": list(best[1]), "fval": float(best[0])},
            "rank_of": rank_of}
