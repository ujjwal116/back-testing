from __future__ import annotations

import argparse

from core.optimizer import load_optimization_pair, run_optimization, save_optimization_reports


def main() -> None:
    parser = argparse.ArgumentParser(description="Optimize an intraday strategy backtest.")
    parser.add_argument("config", help="Path to optimization YAML config file.")
    args = parser.parse_args()

    base_config, optimization_config = load_optimization_pair(args.config)
    stats, heatmap = run_optimization(base_config, optimization_config)
    paths = save_optimization_reports(base_config, optimization_config, stats, heatmap)

    print(stats)
    print("\nSaved optimization reports:")
    for name, path in paths.items():
        print(f"- {name}: {path}")


if __name__ == "__main__":
    main()
