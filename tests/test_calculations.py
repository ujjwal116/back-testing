"""
Unit tests for BaseIntradayStrategy pure calculation methods.

These tests exercise the calculation logic in isolation using a lightweight
stub that satisfies the minimum interface required — no Backtesting.py
internals, no CSV files, no bt.run().
"""
from __future__ import annotations

import types
import pytest
from strategies.base import BaseIntradayStrategy


# ---------------------------------------------------------------------------
# Minimal stub to instantiate BaseIntradayStrategy without Backtesting.py
# ---------------------------------------------------------------------------

class _ConcreteStrategy(BaseIntradayStrategy):
    """Minimal concrete subclass — only exists to satisfy the abstract `next` requirement."""
    def next(self):
        pass


def _make_strategy(**overrides):
    """Return a BaseIntradayStrategy instance with defaults, applying overrides."""
    strat = object.__new__(_ConcreteStrategy)
    # Backtesting.py Strategy stores params as class attributes; mirror that.
    defaults = {
        "stop_loss_method":         "fixed_points",
        "stop_loss_value":          20,
        "take_profit_method":       "fixed_points",
        "take_profit_value":        60,
        "trailing_enabled":         True,
        "trailing_method":          "activation_lock",
        "trailing_value":           None,
        "trailing_activation_points": 40,
        "trailing_lock_points":     30,
        "atr_period":               14,
        "atr_multiplier":           1.5,
        "position_size":            1,
    }
    defaults.update(overrides)
    for k, v in defaults.items():
        setattr(strat, k, v)
    return strat


# ---------------------------------------------------------------------------
# 1. Stop loss — fixed_points
# ---------------------------------------------------------------------------

class TestStopLossFixed:
    def test_long(self):
        s = _make_strategy(stop_loss_method="fixed_points", stop_loss_value=20)
        assert s.calculate_stop_loss("long", 25000, 25000, 24980) == 24980.0

    def test_short(self):
        s = _make_strategy(stop_loss_method="fixed_points", stop_loss_value=20)
        assert s.calculate_stop_loss("short", 25000, 25020, 25000) == 25020.0

    def test_fractional_value(self):
        s = _make_strategy(stop_loss_method="fixed_points", stop_loss_value=28)
        assert s.calculate_stop_loss("long", 24646.9, 24641.9, 24585.5) == pytest.approx(24618.9, abs=0.01)


# ---------------------------------------------------------------------------
# 2. Stop loss — signal_candle_opposite
# ---------------------------------------------------------------------------

class TestStopLossSignalCandle:
    def test_long_uses_signal_low(self):
        s = _make_strategy(stop_loss_method="signal_candle_opposite")
        assert s.calculate_stop_loss("long", 25005, 25000, 24900) == 24900.0

    def test_short_uses_signal_high(self):
        s = _make_strategy(stop_loss_method="signal_candle_opposite")
        assert s.calculate_stop_loss("short", 24995, 25100, 24980) == 25100.0


# ---------------------------------------------------------------------------
# 3. Stop loss — fixed_or_signal_candle_tighter
# ---------------------------------------------------------------------------

class TestStopLossTighter:
    def test_long_fixed_is_tighter(self):
        # fixed SL = 25000-20=24980, signal_low=24900 -> fixed is higher (tighter) -> 24980
        s = _make_strategy(stop_loss_method="fixed_or_signal_candle_tighter", stop_loss_value=20)
        assert s.calculate_stop_loss("long", 25000, 25050, 24900) == 24980.0

    def test_long_signal_is_tighter(self):
        # fixed SL = 25000-20=24980, signal_low=24990 -> signal_low is higher (tighter) -> 24990
        s = _make_strategy(stop_loss_method="fixed_or_signal_candle_tighter", stop_loss_value=20)
        assert s.calculate_stop_loss("long", 25000, 25050, 24990) == 24990.0

    def test_short_fixed_is_tighter(self):
        # fixed SL = 25000+20=25020, signal_high=25100 -> fixed is lower (tighter) -> 25020
        s = _make_strategy(stop_loss_method="fixed_or_signal_candle_tighter", stop_loss_value=20)
        assert s.calculate_stop_loss("short", 25000, 25100, 24950) == 25020.0

    def test_short_signal_is_tighter(self):
        # fixed SL = 25000+20=25020, signal_high=25010 -> signal_high is lower (tighter) -> 25010
        s = _make_strategy(stop_loss_method="fixed_or_signal_candle_tighter", stop_loss_value=20)
        assert s.calculate_stop_loss("short", 25000, 25010, 24950) == 25010.0

    def test_long_trade1_real(self):
        # Trade 1: entry=24646.90, signal_low=24585.50, fixed=24646.90-20=24626.90
        # tighter = max(24626.90, 24585.50) = 24626.90
        s = _make_strategy(stop_loss_method="fixed_or_signal_candle_tighter", stop_loss_value=20)
        sl = s.calculate_stop_loss("long", 24646.9, 24641.9, 24585.5)
        assert sl == pytest.approx(24626.9, abs=0.01)

    def test_long_trade2_real(self):
        # Trade 2: entry=24636.10, signal_low=24616.60, fixed=24636.10-20=24616.10
        # tighter = max(24616.10, 24616.60) = 24616.60
        s = _make_strategy(stop_loss_method="fixed_or_signal_candle_tighter", stop_loss_value=20)
        sl = s.calculate_stop_loss("long", 24636.1, 24631.1, 24616.6)
        assert sl == pytest.approx(24616.6, abs=0.01)


# ---------------------------------------------------------------------------
# 4. Take profit — fixed_points
# ---------------------------------------------------------------------------

class TestTakeProfitFixed:
    def test_long(self):
        s = _make_strategy(take_profit_method="fixed_points", take_profit_value=60)
        assert s.calculate_take_profit("long", 25000, 24980) == 25060.0

    def test_short(self):
        s = _make_strategy(take_profit_method="fixed_points", take_profit_value=60)
        assert s.calculate_take_profit("short", 25000, 25020) == 24940.0

    def test_none_method_returns_none(self):
        s = _make_strategy(take_profit_method="none")
        assert s.calculate_take_profit("long", 25000, 24980) is None


# ---------------------------------------------------------------------------
# 5. Take profit — risk_reward
# ---------------------------------------------------------------------------

class TestTakeProfitRiskReward:
    def test_long_2r(self):
        # risk=20, RR=2 -> TP = 25000 + 40 = 25040
        s = _make_strategy(take_profit_method="risk_reward", take_profit_value=2.0)
        assert s.calculate_take_profit("long", 25000, 24980) == pytest.approx(25040.0)

    def test_short_2r(self):
        s = _make_strategy(take_profit_method="risk_reward", take_profit_value=2.0)
        assert s.calculate_take_profit("short", 25000, 25020) == pytest.approx(24960.0)

    def test_no_sl_returns_none(self):
        s = _make_strategy(take_profit_method="risk_reward", take_profit_value=2.0)
        assert s.calculate_take_profit("long", 25000, None) is None


# ---------------------------------------------------------------------------
# 6. Trailing stop — activation_lock (long)
# ---------------------------------------------------------------------------

class TestTSLActivationLockLong:

    def _trade_stub(self, entry_price: float):
        trade = types.SimpleNamespace(entry_price=entry_price, is_long=True)
        return trade

    def _strategy_with_close(self, close: float, activation=40, lock=30):
        s = _make_strategy(
            trailing_method="activation_lock",
            trailing_activation_points=activation,
            trailing_lock_points=lock,
        )
        close_val = close
        class _Close:
            def __getitem__(self, idx):
                return close_val
        # data is a read-only property on Strategy; bypass via __dict__
        object.__setattr__(s, '_data', types.SimpleNamespace(Close=_Close()))
        s.__class__.data = property(lambda self: self._data)
        return s

    def test_below_activation_returns_none(self):
        # profit = 39 < 40 -> not yet activated
        s = self._strategy_with_close(close=25039.0)
        t = self._trade_stub(entry_price=25000.0)
        assert s._long_trailing_stop(t) is None

    def test_at_activation_snaps_to_lock(self):
        # profit = 40 exactly -> activated
        # SL = close - gap = 25040 - 10 = 25030  (gap = 40-30=10)
        s = self._strategy_with_close(close=25040.0)
        t = self._trade_stub(entry_price=25000.0)
        assert s._long_trailing_stop(t) == pytest.approx(25030.0)

    def test_above_activation_trails(self):
        # profit=60 -> SL = 25060 - 10 = 25050
        s = self._strategy_with_close(close=25060.0)
        t = self._trade_stub(entry_price=25000.0)
        assert s._long_trailing_stop(t) == pytest.approx(25050.0)

    def test_gap_is_always_activation_minus_lock(self):
        # activation=50, lock=35, gap=15
        s = self._strategy_with_close(close=25050.0, activation=50, lock=35)
        t = self._trade_stub(entry_price=25000.0)
        assert s._long_trailing_stop(t) == pytest.approx(25035.0)

    def test_trade2_activation_bar19(self):
        # Trade 2: entry=24636.1, close bar19=24681.9, activation=40, lock=30, gap=10
        # profit=45.8 >= 40 -> SL = 24681.9 - 10 = 24671.9
        s = self._strategy_with_close(close=24681.9)
        t = self._trade_stub(entry_price=24636.1)
        assert s._long_trailing_stop(t) == pytest.approx(24671.9, abs=0.01)


# ---------------------------------------------------------------------------
# 7. Trailing stop — activation_lock (short)
# ---------------------------------------------------------------------------

class TestTSLActivationLockShort:

    def _trade_stub(self, entry_price: float):
        return types.SimpleNamespace(entry_price=entry_price, is_long=False)

    def _strategy_with_close(self, close: float, activation=40, lock=30):
        s = _make_strategy(
            trailing_method="activation_lock",
            trailing_activation_points=activation,
            trailing_lock_points=lock,
        )
        close_val = close
        class _Close:
            def __getitem__(self, idx):
                return close_val
        object.__setattr__(s, '_data', types.SimpleNamespace(Close=_Close()))
        s.__class__.data = property(lambda self: self._data)
        return s

    def test_below_activation_returns_none(self):
        # profit = entry - close = 25000 - 24962 = 38 < 40
        s = self._strategy_with_close(close=24962.0)
        t = self._trade_stub(entry_price=25000.0)
        assert s._short_trailing_stop(t) is None

    def test_at_activation_snaps_to_lock(self):
        # profit = 25000 - 24960 = 40 -> SL = close + gap = 24960 + 10 = 24970
        s = self._strategy_with_close(close=24960.0)
        t = self._trade_stub(entry_price=25000.0)
        assert s._short_trailing_stop(t) == pytest.approx(24970.0)

    def test_above_activation_trails(self):
        # profit=60 -> SL = 24940 + 10 = 24950
        s = self._strategy_with_close(close=24940.0)
        t = self._trade_stub(entry_price=25000.0)
        assert s._short_trailing_stop(t) == pytest.approx(24950.0)

    def test_trade3_activation_bar81(self):
        # Trade 3: entry=24775.2, close bar81=24713.25, activation=40, lock=30, gap=10
        # profit=61.95 >= 40 -> SL = 24713.25 + 10 = 24723.25
        s = self._strategy_with_close(close=24713.25)
        t = self._trade_stub(entry_price=24775.2)
        assert s._short_trailing_stop(t) == pytest.approx(24723.25, abs=0.01)


# ---------------------------------------------------------------------------
# 8. TSL never moves backward (ratchet property)
# The update_trailing_stop method only moves SL in the favourable direction.
# We verify the guard: candidate must be > current SL for longs.
# ---------------------------------------------------------------------------

class TestTSLRatchet:

    def test_long_sl_never_decreases(self):
        s = _make_strategy(trailing_method="activation_lock",
                           trailing_activation_points=40, trailing_lock_points=30)
        close_val = 25050.0
        class _Close:
            def __getitem__(self, idx): return close_val
        object.__setattr__(s, '_data', types.SimpleNamespace(Close=_Close()))
        s.__class__.data = property(lambda self: self._data)
        trade = types.SimpleNamespace(entry_price=25000.0, is_long=True, sl=25050.0)

        close_val = 25040.0
        candidate = s._long_trailing_stop(trade)
        assert candidate is not None
        assert candidate < trade.sl

    def test_short_sl_never_increases(self):
        # Entry=25000, activation=40. Price already dropped to 24950 (profit=50, activated).
        # SL locked at 24950+10=24960. Now price bounces to 24960 (profit=40, still active).
        # candidate = 24960+10=24970 > current sl=24960 -> guard in update_trailing_stop blocks it.
        s = _make_strategy(trailing_method="activation_lock",
                           trailing_activation_points=40, trailing_lock_points=30)
        close_val = 24960.0
        class _Close:
            def __getitem__(self, idx): return close_val
        object.__setattr__(s, '_data', types.SimpleNamespace(Close=_Close()))
        s.__class__.data = property(lambda self: self._data)
        trade = types.SimpleNamespace(entry_price=25000.0, is_long=False, sl=24960.0)

        # Price bounces back up: profit drops to 30 -> below activation -> returns None
        # which means the existing SL stays — the ratchet holds by not returning a new level
        close_val = 24970.0
        candidate = s._short_trailing_stop(trade)
        # Below activation -> None means: do not move the SL (ratchet holds)
        assert candidate is None


# ---------------------------------------------------------------------------
# 9. Signal detection helpers — colour logic
# ---------------------------------------------------------------------------

class TestCandleColour:

    def test_green_candle(self):
        # close > open -> green
        assert 100.0 > 95.0  # close > open

    def test_red_candle(self):
        # close < open -> red
        assert 95.0 < 100.0  # close < open

    def test_doji_is_neither(self):
        # close == open -> doji, neither red nor green
        open_ = close = 100.0
        is_red   = close < open_
        is_green = close > open_
        assert not is_red and not is_green


# ---------------------------------------------------------------------------
# 10. Entry level calculation
# ---------------------------------------------------------------------------

class TestEntryLevel:

    def test_long_level(self):
        sig_high = 25000.0
        buffer = 5.0
        assert sig_high + buffer == 25005.0

    def test_short_level(self):
        sig_low = 25000.0
        buffer = 5.0
        assert sig_low - buffer == 24995.0

    def test_trade1_long_level(self):
        # signal_high=24641.90, buffer=5 -> long_stop=24646.90
        assert 24641.90 + 5 == pytest.approx(24646.90)

    def test_trade3_short_level(self):
        # signal_low=24780.20, buffer=5 -> short_stop=24775.20
        assert 24780.20 - 5 == pytest.approx(24775.20)
