from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from core.backtest_runner import create_backtest
from core.config import config_with_params, load_config, save_config


def run_optimization(config: dict[str, Any], optimization_config: dict[str, Any]):
    bt, base_params = create_backtest(config)
    optimize_config = optimization_config.get("optimize", {})
    parameter_grid = optimize_config.get("parameters", {})
    maximize_key = optimize_config.get("maximize", "Equity Final [$]")
    constraints = optimize_config.get("constraints", {})
    min_trades = constraints.get("min_trades", 0)
    tsl_gap_positive = constraints.get("tsl_gap_positive", False)

    def objective(stats):
        if min_trades and stats.get("# Trades", 0) < min_trades:
            return float("-inf")
        if tsl_gap_positive:
            strategy = stats.get("_strategy")
            if strategy is not None:
                act = float(getattr(strategy, "trailing_activation_points", 0))
                lock = float(getattr(strategy, "trailing_lock_points", 0))
                if lock >= act:
                    return float("-inf")
        return stats[maximize_key]

    run_params = {**base_params, **parameter_grid}
    stats, heatmap = bt.optimize(
        **run_params,
        maximize=objective,
        return_heatmap=True,
    )
    return stats, heatmap


def save_optimization_reports(
    base_config: dict[str, Any],
    optimization_config: dict[str, Any],
    stats,
    heatmap,
) -> dict[str, Path]:
    output_dir = Path(base_config.get("reporting", {}).get("output_dir", "reports")) / "optimization"
    output_dir.mkdir(parents=True, exist_ok=True)

    strategy_name = base_config.get("strategy", {}).get("name", "strategy")
    best_params = dict(stats.get("_strategy")._params) if hasattr(stats.get("_strategy"), "_params") else {}
    best_config = config_with_params(base_config, best_params)

    best_config_path = output_dir / f"{strategy_name}_best_config.yaml"
    best_result_path = output_dir / f"{strategy_name}_best_result.csv"
    grid_path = output_dir / f"{strategy_name}_optimization_results.csv"

    save_config(best_config, best_config_path)
    pd.Series(stats.drop(labels=[k for k in stats.index if str(k).startswith("_")], errors="ignore")).to_csv(best_result_path)

    if heatmap is not None:
        heatmap.rename("Score").reset_index().to_csv(grid_path, index=False)

    return {
        "best_config": best_config_path,
        "best_result": best_result_path,
        "optimization_results": grid_path,
    }


def load_optimization_pair(path: str | Path) -> tuple[dict[str, Any], dict[str, Any]]:
    optimization_config = load_config(path)
    base_config_path = optimization_config["base_config"]
    return load_config(base_config_path), optimization_config
