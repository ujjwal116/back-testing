from __future__ import annotations

import argparse

from core.backtest_runner import run_backtest
from core.config import load_config
from core.reporting import save_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Run an intraday strategy backtest.")
    parser.add_argument("config", help="Path to YAML config file.")
    parser.add_argument(
        "--engine",
        choices=["backtesting", "candle_path"],
        default="backtesting",
        help="Simulation engine to use (default: backtesting). "
             "Use 'candle_path' for the candle-colour-aware custom engine.",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    if args.engine == "candle_path":
        from core.engine import CandlePathEngine
        engine = CandlePathEngine(config)
        engine.run()
        stats  = engine.get_stats()
        trades = engine.get_trades_df()
        paths  = save_reports(config, stats, bt=None, trades_override=trades, engine="candle_path")
        print(stats.to_string())
    else:
        bt, stats = run_backtest(config)
        paths = save_reports(config, stats, bt, engine="backtesting")
        print(stats)

    run_dir = paths.get("summary", next(iter(paths.values()))).parent.resolve()
    print(f"\nSaved reports -> {run_dir}")
    for name, path in paths.items():
        print(f"  {name}: {path.name}")
    if "html_report" in paths:
        print(f"\nOpen report: {paths['html_report'].resolve()}")


if __name__ == "__main__":
    main()
