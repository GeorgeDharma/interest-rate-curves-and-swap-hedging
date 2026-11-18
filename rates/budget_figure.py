"""Plot the worst fully repriced loss in each scenario set."""

from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def draw(output):
    output = Path(output)
    results = pd.read_csv(output / "budget_summary.csv").dropna(subset=["budget_m"])
    fig, ax = plt.subplots(figsize=(8.1, 3.0), layout="constrained")
    for column, label, colour, marker in [
        ("fit_worst_loss", "Six design shocks", "#955636", "o"),
        ("other_worst_loss", "Four additional shocks", "#344a59", "s"),
    ]:
        ax.plot(
            results.budget_m,
            results[column] / 1000,
            marker=marker,
            lw=2.2,
            color=colour,
            label=label,
        )
    ax.set(
        xlabel="Gross hedge notional limit (€m)",
        ylabel="Largest loss (€000)",
        xticks=results.budget_m,
        ylim=(0, 565),
    )
    ax.spines[["right", "top"]].set_visible(False)
    ax.grid(axis="y", color="#dfd9d2", alpha=0.65)
    ax.legend(frameon=False, loc="upper center", ncols=2)
    fig.savefig(output / "budget_tradeoff.png", dpi=180)
    plt.close(fig)
