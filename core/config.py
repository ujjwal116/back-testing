from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import numpy as np
import yaml


def load_config(path: str | Path) -> dict[str, Any]:
    path = Path(path).resolve()
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    # Stash the directory so data_loader can resolve relative paths correctly,
    # regardless of the process working directory.  Prefixed with _ so it is
    # never written back to disk by save_config / config_with_params.
    config["_config_dir"] = path.parent
    return config


def save_config(config: dict[str, Any], path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Strip runtime-only keys (prefixed with _) before writing.
    serialisable = {k: v for k, v in config.items() if not str(k).startswith("_")}
    with path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(to_builtin(serialisable), handle, sort_keys=False)


def to_builtin(value: Any) -> Any:
    if isinstance(value, dict):
        return {to_builtin(key): to_builtin(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_builtin(item) for item in value]
    if isinstance(value, tuple):
        return tuple(to_builtin(item) for item in value)
    if isinstance(value, np.generic):
        return value.item()
    return value


def build_strategy_params(config: dict[str, Any]) -> dict[str, Any]:
    trading = config.get("trading", {})
    risk = config.get("risk", {})
    stop_loss = risk.get("stop_loss", {})
    take_profit = risk.get("take_profit", {})
    trailing = risk.get("trailing_stop", {})
    position_size = risk.get("position_size", {})
    atr = config.get("indicators", {}).get("atr", {})

    params = {
        "trading_start_time": trading.get("trading_start_time", "09:15"),
        "trading_end_time": trading.get("trading_end_time", "15:30"),
        "force_square_off": trading.get("force_square_off", True),
        "square_off_time": trading.get("square_off_time", trading.get("trading_end_time", "15:30")),
        "max_trades_per_day": trading.get("max_trades_per_day", 3),
        "stop_after_first_profit": trading.get("stop_after_first_profit", False),
        "position_size": position_size.get("value", 1),
        "stop_loss_method": stop_loss.get("method", "fixed_points"),
        "stop_loss_value": stop_loss.get("value", 28),
        "take_profit_method": take_profit.get("method", "fixed_points"),
        "take_profit_value": take_profit.get("value", 60),
        "trailing_enabled": trailing.get("enabled", False),
        "trailing_method": trailing.get("method", "none"),
        "trailing_value": trailing.get("value"),
        "trailing_activation_points": trailing.get("activation_points", 80),
        "trailing_lock_points": trailing.get("lock_points", 60),
        "trailing_step_points": trailing.get("step_points", 40),
        "atr_period": atr.get("period", 14),
        "atr_multiplier": stop_loss.get("atr_multiplier", trailing.get("atr_multiplier", 1.5)),
    }
    params.update(config.get("strategy_params", {}))
    return params


def config_with_params(config: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(config)
    # Drop runtime-only keys so they are never serialised.
    for key in list(updated.keys()):
        if str(key).startswith("_"):
            del updated[key]
    updated.setdefault("trading", {})
    updated.setdefault("risk", {})
    updated["risk"].setdefault("stop_loss", {})
    updated["risk"].setdefault("take_profit", {})
    updated["risk"].setdefault("trailing_stop", {})

    mapping = {
        "trading_start_time": ("trading", "trading_start_time"),
        "trading_end_time": ("trading", "trading_end_time"),
        "force_square_off": ("trading", "force_square_off"),
        "square_off_time": ("trading", "square_off_time"),
        "max_trades_per_day": ("trading", "max_trades_per_day"),
        "stop_after_first_profit": ("trading", "stop_after_first_profit"),
        "position_size": ("risk", "position_size", "value"),
        "stop_loss_method": ("risk", "stop_loss", "method"),
        "stop_loss_value": ("risk", "stop_loss", "value"),
        "take_profit_method": ("risk", "take_profit", "method"),
        "take_profit_value": ("risk", "take_profit", "value"),
        "trailing_enabled": ("risk", "trailing_stop", "enabled"),
        "trailing_method": ("risk", "trailing_stop", "method"),
        "trailing_value": ("risk", "trailing_stop", "value"),
        "trailing_activation_points": ("risk", "trailing_stop", "activation_points"),
        "trailing_lock_points": ("risk", "trailing_stop", "lock_points"),
        "trailing_step_points": ("risk", "trailing_stop", "step_points"),
        "atr_period": ("indicators", "atr", "period"),
        "atr_multiplier": ("risk", "stop_loss", "atr_multiplier"),
    }

    for key, value in params.items():
        path = mapping.get(key)
        if not path:
            updated.setdefault("strategy_params", {})[key] = value
            continue
        target = updated
        for part in path[:-1]:
            target = target.setdefault(part, {})
        target[path[-1]] = value

    return updated
