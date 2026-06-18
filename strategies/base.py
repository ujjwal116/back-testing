from __future__ import annotations

from datetime import time

import numpy as np
import pandas as pd
from backtesting import Strategy


def atr(high, low, close, period: int):
    high = pd.Series(high)
    low = pd.Series(low)
    close = pd.Series(close)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.rolling(period).mean().to_numpy()


class BaseIntradayStrategy(Strategy):
    trading_start_time = "09:15"
    trading_end_time = "15:30"
    force_square_off = True
    square_off_time = "15:30"
    max_trades_per_day = 3
    stop_after_first_profit = False
    position_size = 1

    stop_loss_method = "fixed_points"
    stop_loss_value = 28
    take_profit_method = "fixed_points"
    take_profit_value = 60
    trailing_enabled = False
    trailing_method = "none"
    trailing_value = None
    # activation_lock trailing: activate once profit >= activation_points,
    # then lock SL at entry + lock_points and trail point-for-point from there.
    # The gap (activation_points - lock_points) is the maintained trail distance.
    trailing_activation_points = 80
    trailing_lock_points = 60
    # activation_lock_step trailing: same as activation_lock but SL moves in
    # discrete steps of step_points.  SL = entry + floor(profit / step_points) * step_points - gap
    # where gap = activation_points - lock_points.
    # e.g. act=40, lock=30, step=40:
    #   profit [40-79]  → SL = entry+30   (step 1)
    #   profit [80-119] → SL = entry+40   (step 2)
    #   profit [120-159]→ SL = entry+80   (step 3)
    #   profit [160+]   → SL = entry+120  (step 4)
    trailing_step_points = 40

    atr_period = 14
    atr_multiplier = 1.5

    def init(self):
        needs_atr = self.stop_loss_method == "atr" or (
            bool(self.trailing_enabled) and self.trailing_method == "atr"
        )
        self.atr_values = (
            self.I(atr, self.data.High, self.data.Low, self.data.Close, self.atr_period)
            if needs_atr
            else None
        )
        self._current_day = None
        self._trades_today = 0
        self._closed_trades_seen = 0
        self._halt_for_day = False

    def is_inside_session(self) -> bool:
        current_time = self.data.index[-1].time()
        return self._parse_time(self.trading_start_time) <= current_time < self._parse_time(self.trading_end_time)

    def can_open_trade(self) -> bool:
        self._reset_day_if_needed()
        return (
            self.is_inside_session()
            and not self._halt_for_day
            and self._trades_today < int(self.max_trades_per_day)
            and not self.position
        )

    def should_square_off(self) -> bool:
        if not self.force_square_off:
            return False
        sq_time = self.square_off_time or self.trading_end_time
        return self.data.index[-1].time() >= self._parse_time(sq_time)

    def square_off_open_position(self) -> bool:
        if self.position and self.should_square_off():
            self.position.close()
            return True
        return False

    def register_entry(self) -> None:
        self._reset_day_if_needed()
        self._trades_today += 1

    def build_brackets(self, direction: str, ref_price: float, signal_high: float, signal_low: float):
        stop_loss = self.calculate_stop_loss(direction, ref_price, signal_high, signal_low)
        take_profit = self.calculate_take_profit(direction, ref_price, stop_loss)
        return stop_loss, take_profit

    def calculate_stop_loss(self, direction: str, ref_price: float, signal_high: float, signal_low: float):
        method = self.stop_loss_method
        if method in (None, "none"):
            return None
        if method == "signal_candle_opposite":
            return signal_low if direction == "long" else signal_high
        if method == "fixed_points":
            points = float(self.stop_loss_value)
            return ref_price - points if direction == "long" else ref_price + points
        if method == "fixed_or_signal_candle_tighter":
            # Use whichever SL is closer to entry (tighter risk).
            # Long:  max(entry - fixed_pts, signal_low)   → higher value = closer to entry
            # Short: min(entry + fixed_pts, signal_high)  → lower value = closer to entry
            points = float(self.stop_loss_value)
            if direction == "long":
                return max(ref_price - points, signal_low)
            else:
                return min(ref_price + points, signal_high)
        if method == "percentage":
            pct = float(self.stop_loss_value) / 100
            return ref_price * (1 - pct) if direction == "long" else ref_price * (1 + pct)
        if method == "atr":
            distance = self._current_atr_distance()
            return ref_price - distance if direction == "long" else ref_price + distance
        raise ValueError(f"Unsupported stop loss method: {method}")

    def calculate_take_profit(self, direction: str, ref_price: float, stop_loss: float | None):
        method = self.take_profit_method
        if method in (None, "none"):
            return None
        if method == "fixed_points":
            points = float(self.take_profit_value)
            return ref_price + points if direction == "long" else ref_price - points
        if method == "percentage":
            pct = float(self.take_profit_value) / 100
            return ref_price * (1 + pct) if direction == "long" else ref_price * (1 - pct)
        if method == "risk_reward":
            if stop_loss is None:
                return None
            risk = abs(ref_price - stop_loss)
            reward = risk * float(self.take_profit_value)
            return ref_price + reward if direction == "long" else ref_price - reward
        raise ValueError(f"Unsupported take profit method: {method}")

    def update_trailing_stop(self) -> None:
        if not self.trailing_enabled or not self.trades:
            return
        for trade in self.trades:
            if trade.is_long:
                candidate = self._long_trailing_stop(trade)
                if candidate is not None and (trade.sl is None or candidate > trade.sl):
                    trade.sl = candidate
            else:
                candidate = self._short_trailing_stop(trade)
                if candidate is not None and (trade.sl is None or candidate < trade.sl):
                    trade.sl = candidate

    def _long_trailing_stop(self, trade=None):
        method = self.trailing_method
        if method == "fixed_points":
            return float(self.data.Close[-1]) - float(self.trailing_value)
        if method == "atr":
            return float(self.data.Close[-1]) - self._current_atr_distance()
        if method == "previous_candle_high_low" and len(self.data.Close) >= 2:
            return float(self.data.Low[-2])
        if method == "activation_lock":
            if trade is None:
                return None
            entry = float(trade.entry_price)
            current_close = float(self.data.Close[-1])
            activation = float(self.trailing_activation_points)
            lock = float(self.trailing_lock_points)
            trail_gap = activation - lock          # e.g. 80 - 60 = 20 pts gap
            profit = current_close - entry
            if profit < activation:
                return None                        # not yet activated; keep original SL
            # Once activated: SL = current_price - trail_gap (maintains the gap)
            return current_close - trail_gap
        if method == "activation_lock_step":
            if trade is None:
                return None
            entry = float(trade.entry_price)
            current_close = float(self.data.Close[-1])
            activation = float(self.trailing_activation_points)
            lock = float(self.trailing_lock_points)
            step = float(self.trailing_step_points)
            profit = current_close - entry
            if profit < activation:
                return None                        # not yet activated; keep original SL
            # How many full steps has price moved beyond activation?
            # step=0 (first activation): SL = entry + lock
            # step=1 (profit >= activation+step): SL = entry + lock + step
            # step=2: SL = entry + lock + 2*step  ...
            steps_beyond = int((profit - activation) / step)
            sl_offset = lock + steps_beyond * step
            return entry + sl_offset
        raise ValueError(f"Unsupported trailing stop method: {method}")

    def _short_trailing_stop(self, trade=None):
        method = self.trailing_method
        if method == "fixed_points":
            return float(self.data.Close[-1]) + float(self.trailing_value)
        if method == "atr":
            return float(self.data.Close[-1]) + self._current_atr_distance()
        if method == "previous_candle_high_low" and len(self.data.Close) >= 2:
            return float(self.data.High[-2])
        if method == "activation_lock":
            if trade is None:
                return None
            entry = float(trade.entry_price)
            current_close = float(self.data.Close[-1])
            activation = float(self.trailing_activation_points)
            lock = float(self.trailing_lock_points)
            trail_gap = activation - lock          # e.g. 80 - 60 = 20 pts gap
            profit = entry - current_close
            if profit < activation:
                return None                        # not yet activated; keep original SL
            # Once activated: SL = current_price + trail_gap (maintains the gap)
            return current_close + trail_gap
        if method == "activation_lock_step":
            if trade is None:
                return None
            entry = float(trade.entry_price)
            current_close = float(self.data.Close[-1])
            activation = float(self.trailing_activation_points)
            lock = float(self.trailing_lock_points)
            step = float(self.trailing_step_points)
            profit = entry - current_close
            if profit < activation:
                return None                        # not yet activated; keep original SL
            steps_beyond = int((profit - activation) / step)
            sl_offset = lock + steps_beyond * step
            return entry - sl_offset
        raise ValueError(f"Unsupported trailing stop method: {method}")

    def _current_atr_distance(self) -> float:
        if self.atr_values is None:
            raise RuntimeError("ATR was not initialised — set stop_loss_method or trailing_method to 'atr' to enable it.")
        value = float(self.atr_values[-1])
        if np.isnan(value):
            value = float(self.data.High[-1] - self.data.Low[-1])
        return value * float(self.atr_multiplier)

    def _reset_day_if_needed(self) -> None:
        current_day = self.data.index[-1].date()
        if self._current_day != current_day:
            self._current_day = current_day
            self._trades_today = 0
            self._halt_for_day = False
            # Advance the seen pointer so forced square-off trades from end-of-day
            # do not trigger stop_after_first_profit on the next day.
            self._closed_trades_seen = len(self.closed_trades)

    @staticmethod
    def _parse_time(value: str) -> time:
        return time.fromisoformat(value)
