"""Reproducible benchmark: standard X-mixer QAOA versus the constraint-preserving XY-mixer QAOA.

Run from the repository root:   python -m benchmarks.qaoa_mixer_benchmark
Random (seeded) portfolio instances, noiseless exact simulation, QAOA depth p=2, COBYLA from 6 starts.
Reports, per instance size n, the probability p_best of measuring the exact best basket relative to blind
guessing among valid baskets (1 / C(n,k)), the share of valid baskets, and the transpiled circuit depth.
"""
import json
import sys
import time

import numpy as np

from portal import solver

SEEDS = range(10)
SIZES = (6, 8, 10, 12)
K = 3
Q = 0.5


def instance(n, seed):
    rng = np.random.default_rng(1000 * n + seed)
    vol = rng.uniform(0.18, 0.38, n)
    a = rng.normal(size=(n, n + 3))
    corr = a @ a.T
    d = np.sqrt(np.diag(corr))
    corr = corr / np.outer(d, d)
    sigma = corr * np.outer(vol, vol)
    mu = rng.normal(0.12, 0.08, n)
    return mu, sigma


def main(out_path=None):
    rows = []
    for n in SIZES:
        for seed in SEEDS:
            mu, sigma = instance(n, seed)
            for mixer in ("x", "xy"):
                t = time.time()
                r = solver.solve(mu, sigma, K, Q, engine="qaoa_sim", mixer=mixer)
                rows.append({"n": n, "seed": seed, "mixer": mixer, "p_best": r["p_best"],
                             "blind": r["p_best_blind"], "lift": r["p_best"] / r["p_best_blind"],
                             "valid_mass": r.get("feasible_mass"), "depth": r["circuit_depth"],
                             "cx": r["cx_gates"], "seconds": round(time.time() - t, 2)})
                print(rows[-1], flush=True)
    if out_path:
        with open(out_path, "w") as f:
            json.dump(rows, f, indent=1)
    return rows


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
