"""Checks for the end-to-end (all candidate sectors) exact baseline used by the sector-mode fair comparison.

Run from the repository root:  python -m unittest tests.test_sector_global -v
No Qiskit, IBM credentials or internet needed.
"""
import itertools
import unittest

import numpy as np

from portal import sector, solver


def instance(n, seed=1):
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(n, n))
    return rng.normal(0.12, 0.05, n), a @ a.T / 10


class GlobalBaseline(unittest.TestCase):
    def setUp(self):
        self.mu, self.sigma = instance(12, seed=5)
        self.members = {"A": [0, 1, 2], "B": [3, 4, 5], "C": [6, 7, 8], "D": [9, 10, 11]}
        self.k, self.q, self.m = 4, 0.5, 2

    def brute(self):
        mu_s, sig_s = solver.normalise(self.mu, self.sigma)
        best = None
        for names in itertools.combinations(self.members, self.m):
            sizes = [len(self.members[n]) for n in names]
            counts = sector.allocate_counts([1.0] * self.m, sizes, self.k)
            for parts in itertools.product(*[itertools.combinations(self.members[n], c) for n, c in zip(names, counts)]):
                b = tuple(sorted(sum(parts, ())))
                x = np.zeros(12)
                x[list(b)] = 1.0
                f = solver.objective(x, mu_s, sig_s, self.k, self.q)
                if best is None or f < best[0]:
                    best = (f, b)
        return best

    def test_matches_independent_brute_force(self):
        out = sector.global_baseline(self.mu, self.sigma, self.k, self.q, self.members, self.m, [0, 1, 3, 4])
        f, b = self.brute()
        self.assertAlmostEqual(out["objective_exact"], f, places=10)
        self.assertEqual(tuple(out["exact_idx"]), b)
        self.assertGreater(out["feasible_baskets"], 0)

    def test_selected_never_beats_baseline_and_gap_is_consistent(self):
        for sel in ([0, 1, 3, 4], [0, 3, 6, 9], [2, 5, 7, 10]):
            out = sector.global_baseline(self.mu, self.sigma, self.k, self.q, self.members, self.m, sel)
            self.assertGreaterEqual(out["objective_selected"], out["objective_exact"] - 1e-12)
            self.assertGreaterEqual(out["rank"], 1)
            if out["matches_exact"]:
                self.assertEqual(out["rank"], 1)
                self.assertAlmostEqual(out["objective_selected"], out["objective_exact"])

    def test_poor_sector_mix_gives_a_different_score(self):
        worst_pick = max(itertools.combinations(range(12), 4), key=lambda b: solver.objective(
            np.bincount(list(b), minlength=12).astype(float), *solver.normalise(self.mu, self.sigma), self.k, self.q))
        out = sector.global_baseline(self.mu, self.sigma, self.k, self.q, self.members, self.m, list(worst_pick))
        self.assertFalse(out["matches_exact"])
        self.assertGreater(out["objective_selected"], out["objective_exact"])

    def test_too_large_search_returns_none(self):
        self.assertIsNone(sector.global_baseline(self.mu, self.sigma, self.k, self.q, self.members, self.m, [0, 1, 3, 4], max_baskets=10))


if __name__ == "__main__":
    unittest.main()
