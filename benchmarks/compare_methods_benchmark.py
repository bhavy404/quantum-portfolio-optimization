"""Reproducible benchmark: QAOA against ordinary methods, on basket instances and on sector-structured instances.

Run from the repository root:   python -m benchmarks.compare_methods_benchmark
Every instance is seeded. For each one, QAOA (XY circuit, p=2, noiseless exact simulation) and four ordinary
methods solve the SAME problem with the SAME objective and constraints:
  exact enumeration, greedy, simulated annealing, random search.
Sampling methods run twice: with a small budget (10% of all valid baskets, at least 40 evaluations) and with a budget
equal to the number of circuit evaluations QAOA used while training (capped at the number of valid baskets).
Reported per method (10 seeded instances per size): share of instances where it found the exact best basket, mean and standard deviation of the gap to the best objective,
mean rank (1 = best), and mean objective evaluations.  Nothing here claims quantum advantage.
"""
import json
import sys
import time

import numpy as np

from portal import combo, sector, solver
from benchmarks.qaoa_mixer_benchmark import instance

SEEDS = range(10)
Q = 0.5
BASKET = [(6, 3), (8, 3), (10, 3), (12, 3)]                       # (stocks, hold)
SECTOR = [("3 sectors x 3 stocks", 3, 3, (2, 1, 1)), ("4 sectors x 3 stocks", 4, 3, (1, 1, 1, 1))]  # name, sectors, size, counts


def run_one(mu, sigma, k, groups, sectorwise):
    mu_s, sig_s = solver.normalise(mu, sigma)
    t = time.time()
    if sectorwise:
        sol = sector.solve_sector(mu, sigma, k, Q, groups, engine="qaoa_sim")
    else:
        sol = solver.solve(mu, sigma, k, Q, engine="qaoa_sim", mixer="xy")
    q_seconds = time.time() - t
    small = combo.compare(mu_s, sig_s, k, Q, groups)
    big = combo.compare(mu_s, sig_s, k, Q, groups, budget=min(sol["train_evals"], small["feasible"]))
    best = small["best"]["fval"]
    denom = abs(best) if abs(best) > 1e-12 else None
    top = sol["samples"][0]
    rnd, _ = combo.random_search(mu_s, sig_s, k, Q, groups, solver.SHOTS, seed=0)  # same number of shots, but no circuit
    rnd_t = tuple(sorted(rnd))
    rows = {"QAOA best of 2048 shots (portal rule)": dict(fval=sol["fval"], rank=sol["rank"], hit=bool(sol["matches_exact"]), evals=sol["train_evals"], seconds=q_seconds),
            "QAOA most frequent basket": dict(fval=top["fval"], rank=top["rank"], hit=tuple(top["idx"]) == tuple(small["best"]["idx"]), evals=sol["train_evals"], seconds=q_seconds),
            "Random sampling best of 2048 (no circuit)": dict(fval=solver.objective(combo._basket(len(mu_s), rnd_t), mu_s, sig_s, k, Q), rank=small["rank_of"].get(rnd_t),
                                                              hit=rnd_t == tuple(small["best"]["idx"]), evals=solver.SHOTS, seconds=0.0)}
    for tag, res in (("small budget", small), ("QAOA-sized budget", big)):
        for r in res["rows"][1:]:
            short = r["name"].split(" (")[0]
            if short == "Greedy" and tag != "small budget":
                continue  # greedy has no budget; report it once
            rows[f"{short} [{'no budget' if short == 'Greedy' else tag}]"] = dict(fval=r["fval"], rank=r["rank"], hit=bool(r["matches_exact"]), evals=r["evals"], seconds=r["seconds"])
    for r in rows.values():
        r["gap_pct"] = 100 * (r["fval"] - best) / denom if denom else 0.0
    return rows, small["feasible"], sol["p_best"], sol["p_best_blind"]


def summarise(per_instance):
    names = list(per_instance[0][0].keys())
    out = {}
    for nm in names:
        rs = [inst[0][nm] for inst in per_instance]
        out[nm] = {"hit_pct": 100 * float(np.mean([r["hit"] for r in rs])), "mean_gap_pct": float(np.mean([r["gap_pct"] for r in rs])), "std_gap_pct": float(np.std([r["gap_pct"] for r in rs], ddof=1)),
                   "mean_rank": float(np.mean([r["rank"] for r in rs])), "mean_evals": float(np.mean([r["evals"] for r in rs])),
                   "mean_seconds": float(np.mean([r["seconds"] for r in rs]))}
    return out, float(np.mean([i[1] for i in per_instance])), float(np.mean([i[2] / i[3] for i in per_instance]))


def main(out_path=None):
    report = {}
    for n, k in BASKET:
        inst = []
        for seed in SEEDS:
            mu, sigma = instance(n, seed)
            inst.append(run_one(mu, sigma, k, [(list(range(n)), k)], False))
        s, feas, lift = summarise(inst)
        report[f"basket n={n}, k={k}"] = {"valid_baskets": feas, "qaoa_lift_over_blind": lift, "methods": s}
        print(f"basket n={n} done", file=sys.stderr, flush=True)
    for name, nsec, size, counts in SECTOR:
        n = nsec * size
        groups = [(list(range(i * size, (i + 1) * size)), c) for i, c in enumerate(counts)]
        inst = []
        for seed in SEEDS:
            mu, sigma = instance(n, seed + 50)
            inst.append(run_one(mu, sigma, sum(counts), groups, True))
        s, feas, lift = summarise(inst)
        report[f"sector {name}, hold {sum(counts)} as {counts}"] = {"valid_baskets": feas, "qaoa_lift_over_blind": lift, "methods": s}
        print(f"sector {name} done", file=sys.stderr, flush=True)
    if out_path:
        json.dump(report, open(out_path, "w"), indent=2)
    for title, block in report.items():
        print(f"\n{title}: {block['valid_baskets']:.0f} valid baskets, QAOA best-basket chance = {block['qaoa_lift_over_blind']:.1f}x blind")
        print(f"{'method':44s} {'hit %':>6s} {'gap %':>7s} {'sd':>6s} {'rank':>6s} {'evals':>7s}")
        for nm, m in block["methods"].items():
            print(f"{nm:44s} {m['hit_pct']:6.0f} {m['mean_gap_pct']:7.2f} {m['std_gap_pct']:6.2f} {m['mean_rank']:6.1f} {m['mean_evals']:7.0f}")
    return report


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
