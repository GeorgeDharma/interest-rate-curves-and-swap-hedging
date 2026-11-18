"""Choose swap positions that minimise the largest loss across six design shocks."""

from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from .analysis import make_hedges, make_portfolio, scenario_shocks
from .engine import BP, Market, fixed_annuity, swap_value


def minimax(initial_pnl, unit_pnl, fee, budget):
    """Fit signed EUR 1m hedge units with an absolute-notional cap. Positive pays fixed."""
    initial_pnl = np.asarray(initial_pnl, dtype=float)
    unit_pnl = np.asarray(unit_pnl, dtype=float)
    fee = np.asarray(fee, dtype=float)
    if budget < 0 or unit_pnl.shape != (len(initial_pnl), len(fee)):
        raise ValueError("Invalid stress matrix or notional budget.")
    hedge_count = len(fee)
    # Optimise positive and negative trade sizes separately so both incur the entry cost.
    stress_rows = np.column_stack(
        (-unit_pnl + fee, unit_pnl + fee, -np.ones(len(initial_pnl)))
    )
    budget_row = np.r_[np.ones(2 * hedge_count), 0.0]
    fit = linprog(
        np.r_[np.zeros(2 * hedge_count), 1.0],
        A_ub=np.vstack((stress_rows, budget_row)),
        b_ub=np.r_[initial_pnl, budget],
        bounds=[(0, None)] * (2 * hedge_count) + [(None, None)],
        method="highs",
    )
    if not fit.success:
        raise RuntimeError(fit.message)
    return fit.x[:hedge_count] - fit.x[hedge_count : 2 * hedge_count], fit.fun


def run(directory, output=None):
    directory = Path(directory)
    out = Path(output) if output is not None else directory / "outputs"
    out.mkdir(exist_ok=True)
    market = Market()
    discount, projection = market.build()
    book = make_portfolio()
    hedges = make_hedges(book, discount, projection)
    trades = book + hedges
    initial = np.array([swap_value(t, discount, projection) for t in trades])
    fees = np.array([fixed_annuity(t, discount) * 1e6 * 0.5 * BP for t in hedges])

    design_shocks = scenario_shocks(market)
    tenors = np.array([quote.tenor_years for quote in market.instruments])
    discount_quotes = np.array([quote.curve == "EUR1D" for quote in market.instruments])
    hump = 35 * np.exp(-(((tenors - 5) / 2.7) ** 2))
    additional_shocks = {
        "Five-year hump": hump,
        "Five-year dip": -hump,
        "Opposing curve basis": np.where(discount_quotes, 20.0, -15.0),
        "Front-end shock": np.interp(tenors, [0, 2, 5, 10, 30], [45, 45, 10, -10, -10]),
    }
    shocks = {**design_shocks, **additional_shocks}
    changes = np.array(
        [
            market.values(trades, market.rates + move * BP) - initial
            for move in shocks.values()
        ]
    )
    initial_pnl = changes[:, : len(book)].sum(axis=1)
    unit_pnl = changes[:, len(book) :]
    design_count = len(design_shocks)
    quote_pv01 = market.pv01(trades, 1)
    book_pv01 = quote_pv01[:, : len(book)].sum(axis=1)
    hedge_pv01 = quote_pv01[:, len(book) :]

    policies = {}
    for budget in [0, 10, 20, 40, 60]:
        positions, _ = minimax(
            initial_pnl[:design_count], unit_pnl[:design_count], fees, budget
        )
        policies[f"Cap €{budget}m"] = budget, positions
    single = np.zeros(len(hedges))
    single[-1] = -book_pv01.sum() / hedge_pv01[:, -1].sum()
    policies["Parallel neutral"] = None, single

    summary, detail, position_rows = [], [], []
    for label, (budget, positions) in policies.items():
        cost = float(np.abs(positions) @ fees)
        gross = initial_pnl + unit_pnl @ positions
        net = gross - cost
        residual = book_pv01 + hedge_pv01 @ positions
        summary.append(
            dict(
                policy=label,
                budget_m=budget,
                gross_notional_m=float(np.abs(positions).sum()),
                entry_cost=cost,
                fit_worst_loss=-min(net[:design_count]),
                other_worst_loss=-min(net[design_count:]),
                parallel_pv01=residual.sum(),
                quote_risk_norm=np.linalg.norm(residual),
            )
        )
        for tenor, size, unit_fee in zip(["2Y", "5Y", "10Y"], positions, fees):
            position_rows.append(
                dict(
                    policy=label,
                    tenor=tenor,
                    signed_eur_m=float(size),
                    unit_cost=float(unit_fee),
                )
            )
        for i, (name, shock) in enumerate(shocks.items()):
            detail.append(
                dict(
                    policy=label,
                    shock=name,
                    sample="Design" if i < design_count else "Additional",
                    gross_pnl=gross[i],
                    after_cost=net[i],
                    unhedged_pnl=initial_pnl[i],
                    linear_pnl=residual @ shock,
                    unit_2y_pnl=unit_pnl[i, 0],
                    unit_5y_pnl=unit_pnl[i, 1],
                    unit_10y_pnl=unit_pnl[i, 2],
                )
            )
    for name, records in [
        ("budget_summary", summary),
        ("budget_positions", position_rows),
        ("budget_stresses", detail),
    ]:
        pd.DataFrame(records).to_csv(
            out / f"{name}.csv", index=False, float_format="%.12g"
        )
    return summary, detail, position_rows
