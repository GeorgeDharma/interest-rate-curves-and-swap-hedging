"""Run the complete analytical case from the bundled, pinned sample inputs."""

from pathlib import Path
from rates.budget_figure import draw
from rates.analysis import calculate_case, export_results
import argparse
from rates.stress_budget import run as stress_budget

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", default=str(Path(__file__).resolve().parent / "outputs")
    )
    args = parser.parse_args()
    result = calculate_case()
    export_results(result, args.output)
    stress_budget(Path(__file__).resolve().parent, args.output)
    draw(args.output)
    print(f"Calibrated {result['metadata']['quote_count']} quotes.")
    for row in result["summary"]:
        print(
            f"{row['policy']}: parallel sensitivity EUR {row['parallel_pv01']:,.2f}/bp; "
            f"absolute quote sensitivity EUR {row['sum_absolute_pv01']:,.2f}/bp; "
            f"entry cost EUR {row['entry_cost']:,.2f}"
        )
