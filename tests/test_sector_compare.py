"""Checks for the sector-wise QAOA solver and the classical comparison methods.

Run from the repository root:  python -m unittest discover -s tests -v
No IBM credentials or internet needed.
"""
import unittest

import numpy as np

from portal import combo, sector, solver


def instance(n, seed=1):
    rng = np.random.default_rng(seed)
    a = rng.normal(size=(n, n))
    return rng.normal(0.12, 0.05, n), a @ a.T / 10


class AllocateCounts(unittest.TestCase):
    def test_adds_up_and_respects_sizes(self):
        c = sector.allocate_counts([0.5, 0.3, 0.2], [3, 3, 3], 5)
        self.assertEqual(sum(c), 5)
        self.assertTrue(all(x <= 3 for x in c))

    def test_capped_by_sector_size(self):
        c = sector.allocate_counts([0.9, 0.1], [2, 5], 5)
        self.assertEqual(sum(c), 5)
        self.assertLessEqual(c[0], 2)

    def test_shortlist_leaves_a_choice_for_qaoa(self):
        self.assertGreater(sector.shortlist_per_sector(3, 9) * 3, 9)
        self.assertLessEqual(sector.shortlist_per_sector(5, 5) * 5, 12)
        self.assertGreaterEqual(sector.shortlist_per_sector(4, 4), 2)


class SectorMixQaoa(unittest.TestCase):
    def test_picks_requested_number_of_sectors(self):
        mu, sigma = instance(6, seed=9)
        out = sector.pick_sectors(mu, sigma, 3, 0.5, maxiter=30)
        self.assertFalse(out.get("skipped"))
        self.assertEqual(len(out["idx"]), 3)
        self.assertEqual(out["qubits"], 6)
        self.assertNotEqual(out["engine"], "classical")

    def test_skips_qaoa_when_every_sector_is_kept(self):
        mu, sigma = instance(3, seed=8)
        out = sector.pick_sectors(mu, sigma, 3, 0.5)
        self.assertTrue(out["skipped"])
        self.assertEqual(out["idx"], [0, 1, 2])


class SectorSolver(unittest.TestCase):
    def test_every_sample_keeps_sector_counts(self):
        mu, sigma = instance(9)
        groups = [([0, 1, 2], 1), ([3, 4, 5], 2), ([6, 7, 8], 1)]
        out = sector.solve_sector(mu, sigma, 4, 0.5, groups, maxiter=30)
        self.assertEqual(out["qubits"], 9)
        self.assertAlmostEqual(out["feasible_mass"], 1.0, places=6)
        for s in out["samples"]:
            for g, c in groups:
                self.assertEqual(sum(1 for i in s["idx"] if i in g), c)
        self.assertGreater(out["p_best"], out["p_best_blind"])  # the circuit does better than blind guessing

    def test_sector_with_nothing_to_choose_uses_no_qubits(self):
        mu, sigma = instance(7, 2)
        groups = [([0, 1, 2], 3), ([3, 4, 5, 6], 1)]  # the first sector keeps all of its stocks
        out = sector.solve_sector(mu, sigma, 4, 0.5, groups, maxiter=30)
        self.assertEqual(out["qubits"], 4)
        self.assertTrue({0, 1, 2} <= set(out["idx"]))

    def test_circuit_matches_simulator(self):
        from qiskit.quantum_info import Statevector

        mu, sigma = instance(7, 3)
        mu_s, sig_s = solver.normalise(mu, sigma)
        groups = [([0, 1, 2], 1), ([3, 4, 5, 6], 2)]
        qubits, local, fixed = sector._layout(groups)
        f, valid, _ = sector._energies(mu_s, sig_s, 3, 0.5, qubits, local, fixed)
        rng_ = float(f[valid].max() - f[valid].min())
        en = np.where(valid, (f - f[valid].min()) / rng_, 0.0)
        params = np.array([0.7, 1.1, 0.4, 0.9])
        psi = sector._state(params, en, valid, sector._edge_index(len(qubits), sector._edges(local)), 2)
        circ = sector._circuit(sector._qubo_op(mu_s, sig_s, 3, 0.5, qubits, fixed), len(qubits), local, sector._edges(local), params, 2, rng_)
        self.assertGreater(abs(np.vdot(psi, Statevector(circ.decompose(reps=4)).data)) ** 2, 0.999)


class Baselines(unittest.TestCase):
    def test_methods_respect_constraints_and_exact_is_best(self):
        mu, sigma = instance(9, 4)
        mu_s, sig_s = solver.normalise(mu, sigma)
        groups = [([0, 1, 2], 1), ([3, 4, 5], 1), ([6, 7, 8], 2)]
        res = combo.compare(mu_s, sig_s, 4, 0.5, groups)
        best = res["rows"][0]["fval"]
        for r in res["rows"]:
            self.assertGreaterEqual(r["fval"], best - 1e-12)
            for g, c in groups:
                self.assertEqual(sum(1 for i in r["idx"] if i in g), c)

    def test_basket_group_count(self):
        self.assertEqual(len(combo.feasible_baskets([(list(range(6)), 3)])), 20)


if __name__ == "__main__":
    unittest.main()
