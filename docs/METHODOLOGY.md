# Interest rate curves and swap hedging

George Dharma

A Python project that values a small EUR interest-rate swap portfolio, measures
its exposure to market quotes, and compares two ways to hedge that exposure.
The report and Excel companion present the same calculation in forms that are
easy to review. Python contains the curve construction and the full repricing.

## Start here

Open [the notebook](../notebooks/analysis.ipynb) to follow the worked example.
Its saved outputs can be read without running anything.

The case uses published ORE sample inputs dated **5 February 2016**. The four
portfolio trades are hypothetical. The portfolio's EUR 1.588 million starting
value is the value of future net payments, not a trading profit. Every stress
and the next-business-day P&L example is a chosen scenario, not an observed
historical return.

## Running this version

See [RUNNING.md](RUNNING.md). Open [the notebook](../notebooks/analysis.ipynb) for the worked calculation.

## The financial question

How much does the value of these future swap payments change when quoted
interest rates move, and how effectively can additional swaps offset that
change?

An interest-rate swap exchanges fixed and floating interest payments on an
agreed notional amount. The notional determines payment size; it is not
exchanged. A pay-fixed swap pays the fixed leg and receives the floating leg.
Negative floating rates can make the floating receipt negative.

The model uses two curves:

- The **discount curve** converts future payments into today's value.
- The **projection curve** estimates the future six-month floating rates.

The model builds each curve in maturity order, choosing its next point so that
the instrument at that maturity reproduces its input quote. This is called
**bootstrapping**. Log-linear interpolation of factors fills the gaps between
points. Projection factors are mathematical tools, not prices of traded bonds.

## What the comparison finds

The unhedged book loses about EUR 9,804 for an equal, small one-basis-point
increase in every selected quote. One basis point means 0.01 percentage point.
A EUR 9.847 million 10-year pay-fixed swap cancels this initial parallel
sensitivity. It reduces the loss in the +50 bp parallel scenario from
EUR 469,202 to a gain of EUR 7,357 before the illustrative entry cost.

A second approach uses 2-, 5- and 10-year swaps. It minimises the sum of squared
individual quote sensitivities while also keeping the total parallel
sensitivity at zero. This reduces that objective relative to the one-swap
hedge, but needs EUR 47.315 million gross hedge notional and suffers a larger
loss in the steepening scenario. The one-swap hedge is preferred for the
stated goal and selected scenarios. It still leaves substantial curve-shape
risk; a zero total sensitivity does not make the portfolio risk-free.

## Model conventions and scope

- Valuation: 5 February 2016; market deposits and swaps start under the sample's
  spot conventions. The portfolio and hedge trades start on 9 March 2016,
  before their first rate fixing. The model rejects trades that need past
  floating-rate fixings; it does not silently invent them.
- Inputs: 30 EUR1D and 10 EUR6M configured quotes through 20 years. Only the
  active quote keys in the pinned source configuration are selected. Missing
  or duplicate selected quotes fail explicitly.
- EUR1D: historical EONIA overnight curve, TARGET calendar, two-business-day
  spot lag for OIS, annual fixed accruals, Actual/360, Following adjustment and
  a one-business-day payment lag. The overnight deposit starts on valuation.
- EUR6M: EURIBOR six-month projection, annual fixed payments using US 30/360,
  semiannual floating payments using Actual/360, Modified Following dates.
  Source ORE interprets the plain `30/360` convention as US 30/360.
- Portfolio: the same term-swap payment conventions, no principal exchange.
  Rates may be negative. Positive discount factors above one are valid in
  this historical negative-rate setting.
- Time interpolation: Actual/365 between valuation and curve nodes, linear
  in log factors. No extrapolation beyond the curve horizon.
- Value: a clean two-curve value under an overnight-discounting assumption.
  Counterparty default, funding adjustments and margin cash flows are outside
  this calculation. This is not an executable dealer quote.
- Sensitivity: each quote is moved up and down by 1 bp; both curves are rebuilt.
  Half the resulting value difference is the central estimate in EUR per bp.
  A separate test repeats the calculation with 0.5 bp bumps.
- Hedge: signed positions are multiples of a EUR 1 million pay-fixed swap.
  Negative positions mean receive fixed. Initial hedge coupons are par rates,
  so their initial clean values are zero. The three-swap objective assigns
  equal weight to each quote. It is not a covariance-based risk forecast.
- Cost: one illustrative 0.5 bp adverse fixed-rate adjustment on each hedge,
  valued using that swap's fixed-leg annuity. It is charged once, not as a
  recurring expense or as 0.5 bp of notional. It is not sourced from a dealer.
- P&L: discount quotes +4 bp and projection quotes +10 bp, then a move to the
  next business day with the same calendar-date curve normalised to the new
  origin. There are no intervening payments. This isolates one illustrative
  change; it is not a next-day market reconstruction or a backtest.

## Where each result comes from

| File | Purpose |
| --- | --- |
| `rates/dates.py` | Calendar, day-count and payment schedules |
| `rates/engine.py` | Source selection, two-curve build, cash-flow valuation and quote sensitivity |
| `rates/analysis.py` | Portfolio, hedge objectives, scenarios, P&L and figures |
| `tests/test_rates.py` | Nineteen financial and numerical checks |
| `data/source/provenance.json` | Pinned source commit, URLs and SHA-256 hashes |
| `outputs/calibration.csv` | Every selected quote and its repriced rate |
| `outputs/cashflows.csv` | Each payment, forward rate, factor and present value |
| `outputs/risk.csv` | The complete quote-by-trade sensitivity matrix |
| `outputs/hedge_positions.csv` | Signed hedge sizes and entry costs |
| `outputs/scenarios.csv` | Full repricing, linear approximation and cost by scenario |
| `outputs/pnl.csv` | Opening-to-closing value bridge |

The Excel file rebuilds cash-flow amounts, trade totals, hedge exposures and
P&L arithmetic from exported curve factors and pricing results. Its blue
hedge-size and cost inputs support local comparisons. Changes to market
quotes, dates, contracts, calibration or optimisation require rerunning
Python and rebuilding the companion file; Excel does not calibrate curves.

## Published examples adapted

1. [LSEG Quantile's use of ORE](https://www.lseg.com/en/insights/post-trade/quantiles-use-of-ore):
   curve calibration, pricing and risk, and hedging as a connected workflow.
2. [ORE source examples](https://github.com/OpenSourceRisk/Engine/tree/3d75a69087911a7e2cf1e6882da7bc27135c0762/Examples/Input):
   sample market quotes, curve configuration and conventions. See the pinned
   copies and license in `data/source`. These are published sample inputs;
   they are not independently verified historical dealer observations.
3. [QuantLib Guide on curve bootstrapping](https://www.quantlibguide.com/Curve%20bootstrapping.html):
   sequential curve construction and checking instrument repricing.
4. [Goldman Sachs GS Quant scenario examples](https://developer.gs.com/docs/gsquant/pricing-and-risk/scenarios/):
   comparison of market shifts and full scenario valuations. No GS service or
   authenticated API is used in this project.
5. [ORE User Guide](https://opensourcerisk.org/content/uploads/2026/04/userguide.pdf):
   curve consistency and P&L-explain examples, sections 4.5.1 and 4.6.10.

The implementation is independent Python. It has not been compared numerically
with an ORE or QuantLib engine. Validation covers calibration, direct flat-curve
arithmetic, payment reconstruction, pricing identities, hedge constraints,
sensitivity convergence and P&L reconciliation.

## Rights

Original project code and text: George Dharma. Third-party ORE sample files
retain their original terms in `data/source/ORE_license.txt`. The company
examples inform the workflow; they do not endorse this project.
