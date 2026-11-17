"""Financial and numerical checks. Run: python -m unittest discover -s tests -v."""

import hashlib
import json
import math
import unittest
from dataclasses import replace
from datetime import date
from pathlib import Path
import numpy as np

from rates.dates import (
    add_months,
    adjust,
    advance_business,
    business_day,
    schedule,
    year_fraction,
)
from rates.engine import (
    ASOF,
    BP,
    ROOT,
    Curve,
    Market,
    Swap,
    cashflows,
    fixed_annuity,
    par_rate,
    swap_value,
)
from rates.analysis import calculate_case, constrained_hedge, make_portfolio


class DateTests(unittest.TestCase):
    def test_target_holidays_and_spot(self):
        for d in [date(2016, 3, 25), date(2016, 3, 28), date(2016, 12, 26)]:
            self.assertFalse(business_day(d))
        self.assertEqual(advance_business(ASOF, 2), date(2016, 2, 9))
        self.assertEqual(advance_business(date(2016, 3, 24), 1), date(2016, 3, 29))

    def test_modified_following_stays_in_month(self):
        self.assertEqual(adjust(date(2016, 4, 30)), date(2016, 4, 29))
        self.assertEqual(adjust(date(2016, 4, 30), "Following"), date(2016, 5, 2))

    def test_us_day_count_at_month_end(self):
        self.assertAlmostEqual(
            year_fraction(date(2016, 2, 29), date(2017, 2, 28), "30U360"), 1
        )
        self.assertAlmostEqual(
            year_fraction(date(2016, 2, 29), date(2016, 3, 31), "30U360"), 1 / 12
        )
        self.assertAlmostEqual(
            year_fraction(date(2016, 1, 29), date(2016, 1, 31), "30U360"), 2 / 360
        )

    def test_schedule_and_payment_lag(self):
        rows = schedule(
            date(2016, 2, 9), date(2018, 2, 9), 12, "Following", 1, "ACT360"
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1][2], date(2018, 2, 12))
        self.assertEqual(rows[0][1], rows[1][0])
        self.assertEqual(add_months(date(2016, 1, 31), 1), date(2016, 2, 29))

    def test_invalid_schedule(self):
        with self.assertRaises(ValueError):
            schedule(ASOF, ASOF)
        with self.assertRaises(ValueError):
            adjust(ASOF, "unknown")


class PricingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.market = Market()
        cls.discount, cls.projection = cls.market.build()
        cls.trades = make_portfolio()

    def test_every_calibration_quote_reprices(self):
        errors = [
            abs(self.market.implied_quote(i, self.discount, self.projection) - i.rate)
            / BP
            for i in self.market.instruments
        ]
        self.assertEqual(len(errors), 40)
        self.assertLess(max(errors), 1e-7)
        for i in self.market.instruments:
            if i.tenor == "2Y":
                self.assertEqual(i.tenor_years, 2)

    def test_overnight_deposit_closed_form(self):
        i = self.market.instruments[0]
        expected = 1 / (1 + i.rate * (i.maturity - i.start).days / 360)
        self.assertAlmostEqual(self.discount.df(i.maturity), expected, places=12)
        self.assertGreater(expected, 1)  # Negative sample rate is valid.

    def test_flat_curves_against_direct_exponentials(self):
        # An independent arithmetic implementation, without Curve.df in the expected value.
        t = self.trades[0]
        horizon = date(2040, 1, 1)
        d_rate, p_rate = 0.02, 0.03
        years = (horizon - ASOF).days / 365
        dc = Curve((ASOF, horizon), np.array([0.0, -d_rate * years]))
        pc = Curve((ASOF, horizon), np.array([0.0, -p_rate * years]))
        fixed = sum(
            alpha * math.exp(-d_rate * (pay - ASOF).days / 365)
            for _, _, pay, alpha in t.fixed_schedule()
        )
        floating = sum(
            math.expm1(p_rate * (b - a).days / 365)
            * math.exp(-d_rate * (pay - ASOF).days / 365)
            for a, b, pay, _ in t.float_schedule()
        )
        expected = t.payer * t.notional * (floating - t.fixed_rate * fixed)
        self.assertAlmostEqual(swap_value(t, dc, pc), expected, places=7)

    def test_cashflows_reconcile_by_trade(self):
        for t in self.trades:
            actual = swap_value(t, self.discount, self.projection)
            detail = sum(r["pv"] for r in cashflows(t, self.discount, self.projection))
            self.assertAlmostEqual(actual, detail, places=7)

    def test_direction_notional_and_par_value(self):
        for t in self.trades:
            value = swap_value(t, self.discount, self.projection)
            self.assertAlmostEqual(
                swap_value(replace(t, payer=-t.payer), self.discount, self.projection),
                -value,
            )
            self.assertAlmostEqual(
                swap_value(
                    replace(t, notional=2 * t.notional), self.discount, self.projection
                ),
                2 * value,
            )
            at_par = replace(t, fixed_rate=par_rate(t, self.discount, self.projection))
            self.assertLess(
                abs(swap_value(at_par, self.discount, self.projection)), 1e-7
            )

    def test_coupon_change_has_exact_annuity_effect(self):
        t = self.trades[0]
        delta = 0.0001
        change = swap_value(
            replace(t, fixed_rate=t.fixed_rate + delta), self.discount, self.projection
        ) - swap_value(t, self.discount, self.projection)
        expected = -t.payer * t.notional * fixed_annuity(t, self.discount) * delta
        self.assertAlmostEqual(change, expected, places=7)

    def test_invalid_quotes_and_horizon_fail(self):
        with self.assertRaises(ValueError):
            self.market.build([0.01])
        q = self.market.rates.copy()
        q[0] = float("nan")
        with self.assertRaises(ValueError):
            self.market.build(q)
        with self.assertRaises(ValueError):
            self.discount.df(date(2100, 1, 1))

    def test_historical_fixing_is_not_silently_forecast(self):
        t = replace(self.trades[0], start=advance_business(ASOF, 1))
        with self.assertRaisesRegex(ValueError, "historical fixings"):
            swap_value(t, self.discount, self.projection)

    def test_curve_roll_preserves_calendar_date_forward_ratios(self):
        next_day = advance_business(ASOF, 1)
        rolled = self.discount.rolled(next_day)
        self.assertEqual(rolled.df(next_day), 1)
        a, b = date(2018, 3, 9), date(2020, 3, 9)
        self.assertAlmostEqual(
            rolled.df(a) / rolled.df(b),
            self.discount.df(a) / self.discount.df(b),
            places=13,
        )

    def test_source_hashes(self):
        meta = json.loads(
            (ROOT / "data/source/provenance.json").read_text(encoding="utf-8")
        )
        for item in meta["files"]:
            if "sha256" not in item:
                continue
            path = ROOT / "data/source" / Path(item["path"]).name
            self.assertEqual(
                hashlib.sha256(path.read_bytes()).hexdigest(), item["sha256"]
            )


class HedgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = calculate_case()
        cls.market = Market()

    def test_parallel_constraint_and_optimality(self):
        rows = self.result["risk"]
        H = np.array([[r[k] for k in ["H2", "H5", "H10"]] for r in rows])
        original = np.array([r["Unhedged"] for r in rows])
        w = constrained_hedge(H, original)
        residual = original + H @ w
        self.assertLess(abs(residual.sum()), 1e-6)
        _, _, vh = np.linalg.svd(H.sum(0)[None, :])
        gradient = (H @ vh[1:].T).T @ residual
        self.assertLess(np.linalg.norm(gradient), 1e-4)
        one = np.array([r["One swap"] for r in rows])
        self.assertLessEqual(np.linalg.norm(residual), np.linalg.norm(one) + 1e-7)

    def test_half_bp_sensitivities_are_stable(self):
        half = self.market.pv01(make_portfolio(), 0.5).sum(axis=1)
        full = np.array([r["Unhedged"] for r in self.result["risk"]])
        relative = np.linalg.norm(half - full) / np.linalg.norm(full)
        self.assertLess(relative, 1e-5)

    def test_small_parallel_move_matches_first_order(self):
        trades = make_portfolio()
        before = self.market.values(trades).sum()
        exact = self.market.values(trades, self.market.rates + BP * 0.1).sum() - before
        predicted = self.result["summary"][0]["parallel_pv01"] * 0.1
        self.assertLess(abs(exact - predicted), 1)

    def test_scenario_and_daily_pnl_reconcile(self):
        for r in self.result["scenarios"]:
            self.assertAlmostEqual(
                r["full_pnl"], r["linear_pnl"] + r["residual"], places=7
            )
            self.assertAlmostEqual(
                r["after_cost"], r["full_pnl"] - r["entry_cost"], places=7
            )
        for r in self.result["pnl"]:
            self.assertAlmostEqual(
                r["gross_pnl"],
                r["closing_pv"] - r["opening_pv"] + r["cash_received"],
                places=7,
            )
            self.assertAlmostEqual(
                r["gross_pnl"],
                r["time_effect"] + r["linear_market_effect"] + r["residual"],
                places=7,
            )
            self.assertAlmostEqual(
                r["net_pnl"], r["gross_pnl"] - r["entry_cost"], places=7
            )


if __name__ == "__main__":
    unittest.main()
