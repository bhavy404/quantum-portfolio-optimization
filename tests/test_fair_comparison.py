"""Regression checks for QPortal's fair-comparison feature.

Run from the repository root with: python -m unittest discover -s tests -v
These tests do not require IBM credentials, yfinance, or Qiskit hardware access.
"""
import pathlib
import unittest

import numpy as np

from portal import solver


ROOT = pathlib.Path(__file__).resolve().parents[1]


class FairComparisonTests(unittest.TestCase):
    def test_classical_baseline_returns_exact_optimum_metadata(self):
        mu = np.array([0.08, 0.12, 0.05, 0.10])
        sigma = np.array([
            [0.04, 0.01, 0.00, 0.01],
            [0.01, 0.09, 0.01, 0.02],
            [0.00, 0.01, 0.03, 0.00],
            [0.01, 0.02, 0.00, 0.06],
        ])
        result = solver.solve(mu, sigma, k=2, q=0.5, engine="classical")
        self.assertTrue(result["matches_exact"])
        self.assertEqual(result["rank"], 1)
        self.assertEqual(result["idx"], result["exact"]["idx"])
        self.assertAlmostEqual(result["fval"], result["objective_exact"])
        self.assertEqual(result["qaoa_seconds"], 0.0)

    def test_comparison_panel_is_rendered_in_each_result_view(self):
        source = (ROOT / "portal" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('function fairComparison(R)', source)
        self.assertIn('fairComparison(R),', source)
        self.assertIn('Fair comparison: QAOA vs classical search', source)
        self.assertIn('Matches exact optimum', source)
        self.assertIn('does not demonstrate a quantum speedup', source.lower())


if __name__ == "__main__":
    unittest.main()
