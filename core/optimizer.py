from __future__ import annotations

import itertools
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


# ---------------------------------------------------------------------------
# Candle-path grid search
# ---------------------------------------------------------------------------

def run_candle_path_optimization(
    config: dict[str, Any],
    optimization_config: dict[str, Any],
) -> tuple[pd.Series, pd.DataFrame]:
    """
    Pure grid search over the candle_path engine.
    Returns (best_stats, full_grid_df).
    """
    from core.engine import CandlePathEngine

    opt      = optimization_config.get("optimize", {})
    grid     = opt.get("parameters", {})
    maximize = opt.get("maximize", "Net Profit [pts]")
    cst      = opt.get("constraints", {})
    min_trades      = int(cst.get("min_trades", 0))
    tsl_gap_positive = bool(cst.get("tsl_gap_positive", False))

    # Build all combinations
    keys   = list(grid.keys())
    values = list(grid.values())
    combos = list(itertools.product(*values))
    total  = len(combos)
    print(f"Grid search: {total} combinations across {keys}")

    rows = []
    best_score  = float("-inf")
    best_stats  = None
    best_combo  = None

    for idx, combo in enumerate(combos, 1):
        params = dict(zip(keys, combo))

        # Constraint: TSL lock < activation
        if tsl_gap_positive:
            act  = params.get("trailing_activation_points",
                              config.get("risk", {}).get("trailing_stop", {}).get("activation_points", 50))
            lock = params.get("trailing_lock_points",
                              config.get("risk", {}).get("trailing_stop", {}).get("lock_points", 40))
            if lock >= act:
                continue

        # Build a config copy with these params applied
        # Preserve _config_dir so data_loader can resolve relative paths
        cfg = config_with_params(config, params)
        cfg["_config_dir"] = config.get("_config_dir")

        engine = CandlePathEngine(cfg)
        engine.run()
        stats = engine.get_stats()

        n = int(stats.get("# Trades", 0))
        if n < min_trades:
            continue

        score = stats.get(maximize, float("-inf"))
        if isinstance(score, float) and score == float("-inf"):
            continue

        row = {**params, "# Trades": n, maximize: round(float(score), 4)}
        # add secondary metrics
        for k in ["Win Rate [%]", "Profit Factor", "Sharpe Ratio", "SQN",
                  "Max. Drawdown [%]", "Avg Trade [pts]"]:
            if k in stats.index:
                row[k] = round(float(stats[k]), 4)
        rows.append(row)

        if float(score) > best_score:
            best_score = float(score)
            best_stats = stats
            best_combo = params

        if idx % 10 == 0 or idx == total:
            print(f"  [{idx}/{total}] best so far: {maximize}={best_score:.2f}  params={best_combo}")

    grid_df = pd.DataFrame(rows)
    print(f"\nBest config: {best_combo}")
    print(f"Best {maximize}: {best_score:.2f}")
    return best_stats, grid_df


def save_candle_path_optimization_reports(
    base_config: dict[str, Any],
    optimization_config: dict[str, Any],
    best_stats: pd.Series,
    grid_df: pd.DataFrame,
) -> dict[str, Path]:

    opt           = optimization_config.get("optimize", {})
    maximize      = opt.get("maximize", "Net Profit [pts]")
    output_dir    = Path(base_config.get("reporting", {}).get("output_dir", "reports")) / "optimization" / "candle_path"
    output_dir.mkdir(parents=True, exist_ok=True)

    strategy_name = base_config.get("strategy", {}).get("name", "strategy")

    # Reconstruct best config from best row in grid
    if not grid_df.empty:
        best_row    = grid_df.loc[grid_df[maximize].idxmax()]
        param_keys  = list(optimization_config.get("optimize", {}).get("parameters", {}).keys())
        best_params = {k: best_row[k] for k in param_keys if k in best_row}
        best_config = config_with_params(base_config, best_params)
    else:
        best_config = base_config
        best_params = {}

    best_config_path = output_dir / f"{strategy_name}_best_config.yaml"
    best_result_path = output_dir / f"{strategy_name}_best_result.csv"
    grid_path        = output_dir / f"{strategy_name}_optimization_results.csv"

    save_config(best_config, best_config_path)
    best_stats.to_csv(best_result_path, header=["value"])
    grid_df.sort_values(maximize, ascending=False).to_csv(grid_path, index=False)

    return {
        "best_config":          best_config_path,
        "best_result":          best_result_path,
        "optimization_results": grid_path,
    }


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
