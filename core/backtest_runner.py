from __future__ import annotations

from typing import Any

from backtesting import Backtest

from core.config import build_strategy_params
from core.data_loader import load_csv_data
from core.strategy_loader import load_strategy


def create_backtest(config: dict[str, Any]) -> tuple[Backtest, dict[str, Any]]:
    data = load_csv_data(config)
    strategy = load_strategy(config["strategy"]["name"])
    backtest_config = config.get("backtest", {})
    bt = Backtest(
        data,
        strategy,
        cash=backtest_config.get("cash", 100000),
        commission=backtest_config.get("commission", 0.0),
        trade_on_close=backtest_config.get("trade_on_close", False),
        exclusive_orders=backtest_config.get("exclusive_orders", True),
        finalize_trades=backtest_config.get("finalize_trades", True),
    )
    return bt, build_strategy_params(config)


def run_backtest(config: dict[str, Any]):
    bt, strategy_params = create_backtest(config)
    return bt, bt.run(**strategy_params)
