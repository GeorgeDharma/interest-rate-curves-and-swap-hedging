# Designing a hedge for a euro swap portfolio

George Dharma

How much swap notional should the desk commit when a hedge that fits one set of rate shocks can perform poorly on other curve shapes?

A €20m limit produces a €179k maximum loss across six design shocks and a €235k maximum loss across four further shocks. The three potential hedges receive or pay fixed against six-month Euribor. All numbers use the fully repriced swaps and an up-front charge on executed notional.

[Open the notebook](notebooks/analysis.ipynb) · [Read the report (PDF)](docs/report.pdf) · [View the result table](outputs/budget_summary.csv) · [Download ZIP](https://github.com/GeorgeDharma/interest-rate-curves-and-swap-hedging/archive/refs/heads/main.zip)

## Run the analysis

All market inputs are included. Use Python 3.12. Download and extract the ZIP, then follow the [Windows, macOS or Linux setup instructions](docs/RUNNING.md). In an activated environment, run these commands from the project folder.

```bash
python -m pip install -r requirements.txt
python run_analysis.py
python -m unittest discover -s tests -v
```

## Code and results

`rates/engine.py` values the cash flows. `rates/analysis.py` calibrates the curves and reprices the study. `rates/stress_budget.py` solves the hedge with a gross notional limit. `rates/budget_figure.py` draws the loss curves.

`budget_summary.csv` contains the loss frontier. `budget_positions.csv` gives signed EUR millions for each tenor. `budget_stresses.csv` gives the gains and losses by shock. `calibration.csv`, `risk.csv` and `pnl.csv` cover calibration, quote sensitivity and one-business-day attribution.

## Source

Open Source Risk, pinned sample market and curve conventions for 5 February 2016.

[Original source](https://github.com/OpenSourceRisk/Engine/tree/3d75a69087911a7e2cf1e6882da7bc27135c0762/Examples/Input)

[Methodology](docs/METHODOLOGY.md) · [Validation](docs/VALIDATION.md)
