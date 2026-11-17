"""Build the case, compare hedges and export results for review."""

from pathlib import Path
from dataclasses import replace, asdict
import csv, json, hashlib
import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from .engine import *


def make_portfolio():
    """Define the four hypothetical EUR swaps used throughout the worked case."""
    start = adjust(add_tenor(advance_business(ASOF, 2), "1M"))
    terms = [
        ("S1", 3, 35e6, 0.0100, -1),
        ("S2", 7, 25e6, 0.0060, 1),
        ("S3", 12, 20e6, 0.0125, -1),
        ("S4", 5, 15e6, 0.0025, 1),
    ]
    return [
        Swap(i, start, add_tenor(start, f"{y}Y"), n, k, side)
        for i, y, n, k, side in terms
    ]


def make_hedges(portfolio, discount, projection):
    """Create 2-, 5- and 10-year hedge swaps at their initial par rates."""
    start = portfolio[0].start
    out = []
    for y in [2, 5, 10]:
        t = Swap(f"H{y}", start, add_tenor(start, f"{y}Y"), 1e6, 0, 1)
        out.append(replace(t, fixed_rate=par_rate(t, discount, projection)))
    return out


def scenario_shocks(market):
    """Return the chosen quote shocks in basis points, one value per quote."""
    tenors = np.array([i.tenor_years for i in market.instruments])
    discount = np.array([i.curve == "EUR1D" for i in market.instruments])
    slope = np.interp(tenors, [0, 2, 15, 30], [-25, -25, 25, 25])
    return {
        "Parallel up 50 bp": np.full(len(tenors), 50.0),
        "Parallel down 50 bp": np.full(len(tenors), -50.0),
        "Steeper curve": slope,
        "Flatter curve": -slope,
        "Discount quotes up 25 bp": np.where(discount, 25.0, 0.0),
        "Projection quotes up 25 bp": np.where(discount, 0.0, 25.0),
    }


def constrained_hedge(hedge_risks, original):
    """Minimise quote risk while keeping the total parallel PV01 at zero.

    A particular solution first satisfies the parallel constraint. The remaining
    adjustment lies in its null space, so it cannot undo that constraint.
    Weights are numbers of EUR 1 million pay-fixed hedge units.
    """
    parallel = hedge_risks.sum(axis=0)
    target = -original.sum()
    if np.linalg.norm(parallel) < 1e-10:
        raise ValueError("The hedge instruments cannot offset parallel risk.")
    particular = parallel * target / (parallel @ parallel)
    _, _, vh = np.linalg.svd(parallel[None, :])
    null_space = vh[1:].T
    adjustment = np.linalg.lstsq(
        hedge_risks @ null_space, -original - hedge_risks @ particular, rcond=None
    )[0]
    return particular + null_space @ adjustment


def calculate_case(bump_bps=1):
    """Build curves, value the trades, size hedges and reprice each scenario."""
    market = Market()
    discount, projection = market.build()
    portfolio = make_portfolio()
    hedge_units = make_hedges(portfolio, discount, projection)
    trades = portfolio + hedge_units
    values = np.array([swap_value(t, discount, projection) for t in trades])
    risks = market.pv01(trades, bump_bps)
    original = risks[:, :4].sum(axis=1)
    hedge_risks = risks[:, 4:]
    single = np.array([0.0, 0.0, -original.sum() / hedge_risks[:, 2].sum()])
    unconstrained, residuals, rank, singular = np.linalg.lstsq(
        hedge_risks, -original, rcond=None
    )
    if rank != 3:
        raise ValueError("Hedge system does not have full column rank.")
    multi = constrained_hedge(hedge_risks, original)
    positions = {"Unhedged": np.zeros(3), "One swap": single, "Three swaps": multi}
    risks_by_policy = {k: original + hedge_risks @ w for k, w in positions.items()}
    # An illustrative 0.5 bp adverse fixed-rate adjustment, charged once.
    cost_per_million = np.array(
        [fixed_annuity(t, discount) * 1e6 * 0.5 * BP for t in hedge_units]
    )
    costs = {k: float(np.abs(w) @ cost_per_million) for k, w in positions.items()}
    calibration = []
    for i in market.instruments:
        implied = market.implied_quote(i, discount, projection)
        calibration.append(
            dict(
                curve=i.curve,
                key=i.key,
                kind=i.kind,
                tenor=i.tenor,
                rate=i.rate,
                implied_rate=implied,
                error_bp=(implied - i.rate) / BP,
                maturity=i.maturity.isoformat(),
                source_line=i.line,
            )
        )
    scenarios = []
    scenario_trades = []
    for name, shock in scenario_shocks(market).items():
        change = market.values(trades, market.rates + shock * BP) - values
        scenario_trades.extend(
            dict(
                scenario=name,
                trade_id=t.trade_id,
                pnl=float(v),
                linear_pnl=float(risks[:, j] @ shock),
            )
            for j, (t, v) in enumerate(zip(trades, change))
        )
        for policy, w in positions.items():
            exact = float(change[:4].sum() + change[4:] @ w)
            approx = float(risks_by_policy[policy] @ shock)
            scenarios.append(
                dict(
                    scenario=name,
                    policy=policy,
                    full_pnl=exact,
                    linear_pnl=approx,
                    residual=exact - approx,
                    entry_cost=costs[policy],
                    after_cost=exact - costs[policy],
                )
            )
    summary = []
    for name, w in positions.items():
        r = risks_by_policy[name]
        ss = [s for s in scenarios if s["policy"] == name]
        summary.append(
            dict(
                policy=name,
                initial_pv=float(values[:4].sum()),
                parallel_pv01=float(r.sum()),
                sum_absolute_pv01=float(np.abs(r).sum()),
                risk_norm=float(np.linalg.norm(r)),
                gross_hedge_notional=float(np.abs(w).sum() * 1e6),
                entry_cost=costs[name],
                max_absolute_scenario=float(max(abs(s["full_pnl"]) for s in ss)),
                worst_after_cost=float(min(s["after_cost"] for s in ss)),
            )
        )
    risk_rows = []
    for j, i in enumerate(market.instruments):
        row = dict(curve=i.curve, tenor=i.tenor, tenor_years=i.tenor_years, key=i.key)
        row.update({t.trade_id: float(risks[j, k]) for k, t in enumerate(trades)})
        row.update({p: float(r[j]) for p, r in risks_by_policy.items()})
        risk_rows.append(row)
    # Illustrative next-business-day case. These are chosen shocks, not observed returns.
    next_date = advance_business(ASOF, 1)
    daily_shock = np.array(
        [4.0 if i.curve == "EUR1D" else 10.0 for i in market.instruments]
    )
    sd, sp = market.build(market.rates + daily_shock * BP)
    roll_d, roll_p = discount.rolled(next_date), projection.rolled(next_date)
    end_d, end_p = sd.rolled(next_date), sp.rolled(next_date)
    rolled = np.array([swap_value(t, roll_d, roll_p) for t in trades])
    ending = np.array([swap_value(t, end_d, end_p) for t in trades])
    pnl = []
    for policy, w in positions.items():
        v0 = float(values[:4].sum() + values[4:] @ w)
        vroll = float(rolled[:4].sum() + rolled[4:] @ w)
        v1 = float(ending[:4].sum() + ending[4:] @ w)
        carry = vroll - v0
        explained = float(risks_by_policy[policy] @ daily_shock)
        pnl.append(
            dict(
                policy=policy,
                start_date=ASOF.isoformat(),
                end_date=next_date.isoformat(),
                opening_pv=v0,
                closing_pv=v1,
                cash_received=0.0,
                gross_pnl=v1 - v0,
                time_effect=carry,
                linear_market_effect=explained,
                residual=v1 - v0 - carry - explained,
                entry_cost=costs[policy],
                net_pnl=v1 - v0 - costs[policy],
            )
        )
    trade_rows = []
    for i, t in enumerate(trades):
        row = asdict(t)
        row["start"] = t.start.isoformat()
        row["raw_end"] = t.raw_end.isoformat()
        row.update(
            par_rate=par_rate(t, discount, projection),
            pv=float(values[i]),
            annuity=fixed_annuity(t, discount),
            parallel_pv01=float(risks[:, i].sum()),
        )
        trade_rows.append(row)
    curve_rows = []
    for name, c in [("Discount", discount), ("Projection", projection)]:
        for d, logdf in zip(c.dates[1:], c.log_discounts[1:]):
            t = (d - ASOF).days / 365
            curve_rows.append(
                dict(
                    curve=name,
                    date=d.isoformat(),
                    years=t,
                    factor=float(np.exp(logdf)),
                    zero_rate=float(-logdf / t),
                )
            )
    return dict(
        summary=summary,
        calibration=calibration,
        trades=trade_rows,
        hedge_positions=[
            dict(
                policy=p,
                trade_id=t.trade_id,
                signed_notional=float(w[j] * 1e6),
                fixed_rate=t.fixed_rate,
                cost=float(abs(w[j]) * cost_per_million[j]),
            )
            for p, w in positions.items()
            if p != "Unhedged"
            for j, t in enumerate(hedge_units)
            if abs(w[j]) > 1e-10
        ],
        risk=risk_rows,
        scenarios=scenarios,
        scenario_trades=scenario_trades,
        pnl=pnl,
        curves=curve_rows,
        pnl_trades=[
            dict(
                trade_id=t.trade_id,
                opening_pv=float(values[j]),
                rolled_pv=float(rolled[j]),
                closing_pv=float(ending[j]),
                linear_market_effect=float(risks[:, j] @ daily_shock),
            )
            for j, t in enumerate(trades)
        ],
        cashflows=[r for t in trades for r in cashflows(t, discount, projection)],
        metadata=dict(
            asof=ASOF.isoformat(),
            next_date=next_date.isoformat(),
            quote_count=len(market.rates),
            discount_count=sum(i.curve == "EUR1D" for i in market.instruments),
            projection_count=sum(i.curve == "EUR6M" for i in market.instruments),
            hedge_condition=float(singular[0] / singular[-1]),
            bump_bps=bump_bps,
            max_quote_error_bp=max(abs(r["error_bp"]) for r in calibration),
            quote_risk_definition="Central change per +1 bp to one market quote, with both curves rebuilt.",
            hedge_objective="Minimise squared quote sensitivities subject to zero parallel PV01; no covariance estimate.",
            unconstrained_parallel_pv01=float(
                (original + hedge_risks @ unconstrained).sum()
            ),
            unconstrained_risk_norm=float(
                np.linalg.norm(original + hedge_risks @ unconstrained)
            ),
            cost_assumption="One-way 0.5 bp adverse fixed-rate adjustment; illustrative, not a dealer quote.",
            pnl_assumption="Next business day: discount quotes +4 bp, projection quotes +10 bp; no intervening payments.",
        ),
    )


def plot_results(result, out):
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 10,
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )
    colors = ["#242B33", "#A85E3B", "#8B9BA5"]
    fig, ax = plt.subplots(figsize=(8.5, 3.4))
    for k, name in enumerate(["Discount", "Projection"]):
        rows = [r for r in result["curves"] if r["curve"] == name]
        ax.plot(
            [r["years"] for r in rows],
            [r["zero_rate"] * 100 for r in rows],
            color=colors[k],
            label=name,
        )
    ax.axhline(0, color="#CCCCCC", lw=0.7)
    ax.set(xlabel="Years from valuation", ylabel="Annual zero rate (%)")
    ax.legend(frameon=False)
    fig.tight_layout()
    fig.savefig(out / "curves.png", dpi=180)
    plt.close(fig)
    risk = pd.DataFrame(result["risk"])
    risk["band"] = pd.cut(
        risk.tenor_years,
        [0, 2, 5, 10, 25],
        labels=["Up to 2Y", "2Y to 5Y", "5Y to 10Y", "Over 10Y"],
    )
    grouped = risk.groupby("band", observed=False)[
        ["Unhedged", "One swap", "Three swaps"]
    ].sum()
    fig, ax = plt.subplots(figsize=(8.5, 3.6))
    x = np.arange(len(grouped))
    w = 0.25
    for k, name in enumerate(grouped.columns):
        ax.bar(x + (k - 1) * w, grouped[name] / 1000, w, label=name, color=colors[k])
    ax.set_xticks(x, grouped.index)
    ax.set_ylabel("PV01 (EUR thousands per bp)")
    ax.axhline(0, color="#777777", lw=0.7)
    ax.legend(frameon=False, ncols=3)
    fig.tight_layout()
    fig.savefig(out / "risk_by_maturity.png", dpi=180)
    plt.close(fig)
    sc = pd.DataFrame(result["scenarios"])
    names = list(sc.scenario.unique())
    fig, ax = plt.subplots(figsize=(8.5, 3.6))
    x = np.arange(len(names))
    for k, name in enumerate(["Unhedged", "One swap", "Three swaps"]):
        vals = sc[sc.policy == name].full_pnl.to_numpy() / 1000
        ax.bar(x + (k - 1) * w, vals, w, label=name, color=colors[k])
    ax.set_xticks(
        x,
        [
            "Rates\n+50 bp",
            "Rates\n-50 bp",
            "Steeper",
            "Flatter",
            "Discount\n+25 bp",
            "Projection\n+25 bp",
        ],
    )
    ax.set_ylabel("Change in value before entry cost (EUR thousands)")
    ax.axhline(0, color="#777777", lw=0.7)
    ax.legend(frameon=False, ncols=3)
    fig.tight_layout()
    fig.savefig(out / "scenario_comparison.png", dpi=180)
    plt.close(fig)


def export_results(result, out):
    """Write the calculated tables, machine-readable results and charts."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    for key, value in result.items():
        if isinstance(value, list):
            pd.DataFrame(value).to_csv(
                out / (key + ".csv"), index=False, float_format="%.12g"
            )
    (out / "results.json").write_text(json.dumps(result, indent=2))
    plot_results(result, out)
