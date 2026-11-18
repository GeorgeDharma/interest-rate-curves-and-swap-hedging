import unittest
import numpy as np
from rates.stress_budget import minimax


class NotionalBudgetTests(unittest.TestCase):
    def test_budget_changes_the_feasible_hedge(self):
        shocks = np.array([-10.0, 8.0])
        change = np.array([[2.0], [-1.0]])
        fees = np.array([0.1])
        no_hedge, loss0 = minimax(shocks, change, fees, 0)
        limited, loss1 = minimax(shocks, change, fees, 3)
        np.testing.assert_allclose(no_hedge, [0])
        self.assertLessEqual(abs(limited).sum(), 3 + 1e-8)
        self.assertAlmostEqual(
            loss1, -min(shocks + change @ limited - np.abs(limited) @ fees)
        )
        self.assertLess(loss1, loss0)
