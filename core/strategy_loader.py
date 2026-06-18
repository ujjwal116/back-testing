from __future__ import annotations

import importlib
from typing import Type

from backtesting import Strategy


def load_strategy(strategy_name: str) -> Type[Strategy]:
    module = importlib.import_module(f"strategies.{strategy_name}")
    class_name = "".join(part.capitalize() for part in strategy_name.split("_")) + "Strategy"
    strategy_class = getattr(module, class_name)
    if not issubclass(strategy_class, Strategy):
        raise TypeError(f"{class_name} must subclass backtesting.Strategy")
    return strategy_class
