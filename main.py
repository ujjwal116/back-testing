from __future__ import annotations

import argparse

from core.backtest_runner import run_backtest
from core.config import load_config
from core.reporting import save_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an intraday strategy backtest.")
    parser.add_argument("config", help="Path to YAML config file.")
    args = parser.parse_args()

    config = load_config(args.config)
    bt, stats = run_backtest(config)
    paths = save_reports(config, stats, bt)

    print(stats)

    # All files land in the same run folder — show it once then list files.
    run_dir = paths.get("summary", next(iter(paths.values()))).parent.resolve()
    print(f"\nSaved reports -> {run_dir}")
    for name, path in paths.items():
        print(f"  {name}: {path.name}")
    if "html_report" in paths:
        print(f"\nOpen report: {paths['html_report'].resolve()}")


if __name__ == "__main__":
    main()
