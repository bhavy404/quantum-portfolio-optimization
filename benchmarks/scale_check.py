"""Optional larger-size check of QAOA against ordinary methods (same seeded instances and objective as compare_methods_benchmark).

Run from the repository root:   python -m benchmarks.scale_check N K SEEDS      e.g.  python -m benchmarks.scale_check 14 5 5
Time grows about 2x per extra stock on the simulator (12 stocks ~4 s per instance, 18 stocks ~100 s).
Nothing here claims quantum advantage; annealing and greedy are reported next to QAOA on purpose.
"""
import sys
import time

from benchmarks.compare_methods_benchmark import run_one, summarise
from benchmarks.qaoa_mixer_benchmark import instance

if __name__ == "__main__":
    n, k, seeds = (int(a) for a in sys.argv[1:4])
    t = time.time()
    inst = []
    for sd in range(seeds):
        mu, sigma = instance(n, sd)
        inst.append(run_one(mu, sigma, k, [(list(range(n)), k)], False))
    s, feas, lift = summarise(inst)
    print(f"n={n} k={k} valid baskets={feas:.0f} QAOA best-basket chance={lift:.1f}x blind ({time.time() - t:.0f}s, {seeds} seeds)")
    for nm, m in s.items():
        if any(w in nm for w in ("QAOA", "Greedy", "Random sampling", "annealing [QAOA", "Random search [QAOA")):
            print(f"  {nm:44s} hit {m['hit_pct']:4.0f}%  gap {m['mean_gap_pct']:6.2f}%  rank {m['mean_rank']:6.1f}  evals {m['mean_evals']:6.0f}")
