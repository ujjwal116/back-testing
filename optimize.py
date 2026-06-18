from __future__ import annotations

import argparse

from core.optimizer import load_optimization_pair, run_optimization, save_optimization_reports
from core.optimizer import run_candle_path_optimization, save_candle_path_optimization_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize an intraday strategy backtest.")
    parser.add_argument("config", help="Path to optimization YAML config file.")
    parser.add_argument(
        "--engine",
        choices=["backtesting", "candle_path"],
        default="backtesting",
        help="Simulation engine to use (default: backtesting).",
    )
    args = parser.parse_args()

    base_config, optimization_config = load_optimization_pair(args.config)

    if args.engine == "candle_path":
        best_stats, grid_df = run_candle_path_optimization(base_config, optimization_config)
        paths = save_candle_path_optimization_reports(base_config, optimization_config, best_stats, grid_df)
        print("\n=== BEST RESULT ===")
        print(best_stats.to_string())
    else:
        stats, heatmap = run_optimization(base_config, optimization_config)
        paths = save_optimization_reports(base_config, optimization_config, stats, heatmap)
        print(stats)

    print("\nSaved optimization reports:")
    for name, path in paths.items():
        print(f"  {name}: {path}")


if __name__ == "__main__":
    main()
