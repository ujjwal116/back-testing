"""
core/models.py — Lightweight data models for the custom backtester.

No external dependencies — pure Python dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Direction(Enum):
    LONG  = 1
    SHORT = -1


@dataclass
class Order:
    """A pending stop order waiting to be triggered."""
    direction:  Direction
    stop_price: float           # trigger price
    sl:         Optional[float] # initial stop-loss
    tp:         Optional[float] # take-profit (None = no TP)
    created_at: datetime = field(default_factory=datetime.now)

    def is_long(self)  -> bool: return self.direction == Direction.LONG
    def is_short(self) -> bool: return self.direction == Direction.SHORT


@dataclass
class Trade:
    """A completed or open trade."""
    direction:   Direction
    entry_price: float
    entry_time:  datetime
    sl:          Optional[float]
    tp:          Optional[float]

    exit_price:  Optional[float] = None
    exit_time:   Optional[datetime] = None
    exit_reason: str = ""        # "sl", "tp", "tsl", "square_off", "open"

    # Trailing stop high-water mark (updated each bar)
    _tsl_level:  Optional[float] = None

    def is_long(self)  -> bool: return self.direction == Direction.LONG
    def is_short(self) -> bool: return self.direction == Direction.SHORT

    def is_open(self) -> bool: return self.exit_price is None

    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        if self.is_long():
            return self.exit_price - self.entry_price
        return self.entry_price - self.exit_price

    def duration_seconds(self) -> float:
        if self.exit_time is None or self.entry_time is None:
            return 0.0
        return (self.exit_time - self.entry_time).total_seconds()
