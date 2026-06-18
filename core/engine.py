"""
core/engine.py — Candle-path-aware backtesting engine.

Replaces backtesting.py with a pure Python simulation loop that uses the
candle-colour heuristic to determine intra-bar price path:

    GREEN candle (close >= open):  Open → Low → High → Close
    RED   candle (close <  open):  Open → High → Low → Close

This eliminates the same-bar SL ambiguity that backtesting.py cannot solve
with OHLC data:
  - GREEN long entry:  Low came before High → SL (below) already visited → cannot be hit same bar
  - GREEN short entry: Low came first → SL (above) reached AFTER entry → can be hit same bar ✓
  - RED   short entry: High came before Low → SL (above) already visited → cannot be hit same bar
  - RED   long entry:  High came first → SL (below) reached AFTER entry → can be hit same bar ✓
"""
from __future__ import annotations

from datetime import time, datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd

from core.models import Direction, Order, Trade


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_time(s: str) -> time:
    return time.fromisoformat(str(s))


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low,
                    (high - prev_close).abs(),
                    (low  - prev_close).abs()], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ---------------------------------------------------------------------------
# Signal detector — pure function, no state
# ---------------------------------------------------------------------------

def _detect_signal(
    prev_open: float, prev_close: float,
    sig_open:  float, sig_close:  float,
    sig_high:  float, sig_low:    float,
    buffer:    float,
) -> Optional[tuple[float, float, float, float]]:
    """
    Returns (sig_high, sig_low, long_stop, short_stop) if a valid
    RED→GREEN or GREEN→RED flip is found, else None.
    """
    prev_green = prev_close > prev_open
    prev_red   = prev_close < prev_open
    sig_green  = sig_close  > sig_open
    sig_red    = sig_close  < sig_open

    if not ((prev_red and sig_green) or (prev_green and sig_red)):
        return None

    long_stop  = sig_high + buffer
    short_stop = sig_low  - buffer
    return sig_high, sig_low, long_stop, short_stop


# ---------------------------------------------------------------------------
# Bracket calculator — mirrors base.py logic
# ---------------------------------------------------------------------------

def _calc_sl(
    direction: Direction,
    fill:      float,
    sig_high:  float,
    sig_low:   float,
    method:    str,
    value:     float,
) -> Optional[float]:
    if method in (None, "none"):
        return None
    if method == "fixed_points":
        return fill - value if direction == Direction.LONG else fill + value
    if method == "fixed_or_signal_candle_tighter":
        if direction == Direction.LONG:
            return max(fill - value, sig_low)
        else:
            return min(fill + value, sig_high)
    if method == "signal_candle_opposite":
        return sig_low if direction == Direction.LONG else sig_high
    return fill - value if direction == Direction.LONG else fill + value


def _calc_tp(
    direction: Direction,
    fill:      float,
    sl:        Optional[float],
    method:    str,
    value:     float,
) -> Optional[float]:
    if method in (None, "none"):
        return None
    if method == "fixed_points":
        return fill + value if direction == Direction.LONG else fill - value
    if method == "risk_reward" and sl is not None:
        risk = abs(fill - sl)
        reward = risk * value
        return fill + reward if direction == Direction.LONG else fill - reward
    return None


# ---------------------------------------------------------------------------
# Trailing stop updater
# ---------------------------------------------------------------------------

def _update_tsl(
    trade:      Trade,
    cur_price:  float,
    method:     str,
    act_pts:    float,
    lock_pts:   float,
    step_pts:   float,
) -> None:
    """Update trade._tsl_level in-place based on current price."""
    if method in (None, "none"):
        return

    if method == "activation_lock":
        entry = trade.entry_price
        if trade.is_long():
            profit = cur_price - entry
            if profit < act_pts:
                return
            gap = act_pts - lock_pts
            candidate = cur_price - gap
            if trade._tsl_level is None or candidate > trade._tsl_level:
                trade._tsl_level = candidate
        else:
            profit = entry - cur_price
            if profit < act_pts:
                return
            gap = act_pts - lock_pts
            candidate = cur_price + gap
            if trade._tsl_level is None or candidate < trade._tsl_level:
                trade._tsl_level = candidate

    elif method == "activation_lock_step":
        entry = trade.entry_price
        if trade.is_long():
            profit = cur_price - entry
            if profit < act_pts:
                return
            steps = int((profit - act_pts) / step_pts)
            sl_offset = lock_pts + steps * step_pts
            candidate = entry + sl_offset
            if trade._tsl_level is None or candidate > trade._tsl_level:
                trade._tsl_level = candidate
        else:
            profit = entry - cur_price
            if profit < act_pts:
                return
            steps = int((profit - act_pts) / step_pts)
            sl_offset = lock_pts + steps * step_pts
            candidate = entry - sl_offset
            if trade._tsl_level is None or candidate < trade._tsl_level:
                trade._tsl_level = candidate


def _effective_sl(trade: Trade) -> Optional[float]:
    """Return the tighter of the fixed SL and the TSL level."""
    sl  = trade.sl
    tsl = trade._tsl_level
    if sl is None and tsl is None:
        return None
    if sl is None:
        return tsl
    if tsl is None:
        return sl
    # For long: higher value = tighter (closer to price)
    if trade.is_long():
        return max(sl, tsl)
    else:
        return min(sl, tsl)


# ---------------------------------------------------------------------------
# Intra-bar price path checker
# ---------------------------------------------------------------------------

def _check_price_path(
    price:     float,
    trade:     Optional[Trade],
    order:     Optional[Order],
    sl_config: dict,
    tp_config: dict,
    tsl_config: dict,
    sig_high:  Optional[float],
    sig_low:   Optional[float],
    ts:        datetime,
    bar_open:  float,
) -> tuple[Optional[Trade], Optional[Order], Optional[Trade]]:
    """
    Process a single price point in the intra-bar path.
    Returns (trade, order, closed_trade).
      - trade: updated open trade (or new trade if order filled)
      - order: remaining order (None if filled or irrelevant)
      - closed_trade: trade object if it closed at this price point
    """
    closed_trade = None

    # --- Check if open trade hits SL or TP ---
    if trade is not None and trade.is_open():
        eff_sl = _effective_sl(trade)
        tp     = trade.tp

        if trade.is_long():
            if eff_sl is not None and price <= eff_sl:
                trade.exit_price  = eff_sl
                trade.exit_time   = ts
                trade.exit_reason = "tsl" if trade._tsl_level is not None and eff_sl == trade._tsl_level else "sl"
                closed_trade = trade
                trade = None
                return trade, order, closed_trade
            if tp is not None and price >= tp:
                trade.exit_price  = tp
                trade.exit_time   = ts
                trade.exit_reason = "tp"
                closed_trade = trade
                trade = None
                return trade, order, closed_trade
        else:  # short
            if eff_sl is not None and price >= eff_sl:
                trade.exit_price  = eff_sl
                trade.exit_time   = ts
                trade.exit_reason = "tsl" if trade._tsl_level is not None and eff_sl == trade._tsl_level else "sl"
                closed_trade = trade
                trade = None
                return trade, order, closed_trade
            if tp is not None and price <= tp:
                trade.exit_price  = tp
                trade.exit_time   = ts
                trade.exit_reason = "tp"
                closed_trade = trade
                trade = None
                return trade, order, closed_trade

    # --- Check if pending order fills ---
    if order is not None and trade is None:
        fill = None
        if order.is_long()  and price >= order.stop_price:
            fill = order.stop_price
        elif order.is_short() and price <= order.stop_price:
            fill = order.stop_price

        if fill is not None:
            # Use bar_open if it gapped through the stop
            actual_fill = bar_open if (
                (order.is_long()  and bar_open >= order.stop_price) or
                (order.is_short() and bar_open <= order.stop_price)
            ) else fill

            sl = _calc_sl(
                order.direction, actual_fill,
                sig_high or 0, sig_low or 0,
                sl_config.get("method", "fixed_points"),
                float(sl_config.get("value") or 20),
            )
            tp = _calc_tp(
                order.direction, actual_fill, sl,
                tp_config.get("method", "none"),
                float(tp_config.get("value") or 0),
            )
            trade = Trade(
                direction=order.direction,
                entry_price=actual_fill,
                entry_time=ts,
                sl=sl,
                tp=tp,
            )
            order = None

    return trade, order, closed_trade


# ---------------------------------------------------------------------------
# Main engine
# ---------------------------------------------------------------------------

class CandlePathEngine:
    """
    Pure Python backtesting engine using the candle-colour price-path heuristic.

    Usage:
        engine = CandlePathEngine(config)
        engine.run()
        stats  = engine.get_stats()
        trades = engine.get_trades_df()
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

        sp   = config.get("strategy_params", {})
        trd  = config.get("trading", {})
        rsk  = config.get("risk", {})
        ind  = config.get("indicators", {})

        self.buffer          = float(sp.get("entry_buffer_points", 5))
        self.expiry_candles  = int(sp.get("signal_expiry_candles", 20))
        self.allow_long      = bool(sp.get("allow_long", True))
        self.allow_short     = bool(sp.get("allow_short", True))

        self.start_time      = _parse_time(trd.get("trading_start_time", "09:15"))
        self.end_time        = _parse_time(trd.get("trading_end_time",   "13:00"))
        self.sq_off_time     = _parse_time(trd.get("square_off_time",    "15:15"))
        self.max_trades      = int(trd.get("max_trades_per_day", 3))
        self.stop_after_profit = bool(trd.get("stop_after_first_profit", False))

        self.sl_config       = rsk.get("stop_loss",    {})
        self.tp_config       = rsk.get("take_profit",  {})
        tsl                  = rsk.get("trailing_stop", {})
        self.tsl_enabled     = bool(tsl.get("enabled", False))
        self.tsl_method      = tsl.get("method", "none")
        self.tsl_act         = float(tsl.get("activation_points", 50))
        self.tsl_lock        = float(tsl.get("lock_points", 40))
        self.tsl_step        = float(tsl.get("step_points", 40))

        self.atr_period      = int(ind.get("atr", {}).get("period", 14))

        self._trades:  list[Trade] = []
        self._equity:  list[float] = []
        self._cash     = float(config.get("backtest", {}).get("cash", 100_000))

    # ------------------------------------------------------------------
    def run(self) -> None:
        from core.data_loader import load_csv_data
        df = load_csv_data(self.config)
        self._run_on(df)

    def _run_on(self, df: pd.DataFrame) -> None:
        cash          = self._cash
        trade: Optional[Trade]  = None
        long_order:  Optional[Order] = None
        short_order: Optional[Order] = None

        # signal state
        sig_high: Optional[float] = None
        sig_low:  Optional[float] = None
        long_stop: Optional[float] = None
        short_stop: Optional[float] = None
        sig_detected_at: Optional[int] = None
        sig_day = None

        # day counters
        cur_day     = None
        trades_today = 0
        halt_day    = False
        profit_today = False

        equity_curve = [cash]
        bars = list(df.itertuples())

        for i, bar in enumerate(bars):
            ts:  datetime = bar.Index
            o, h, l, c = bar.Open, bar.High, bar.Low, bar.Close
            bar_time = ts.time()
            bar_day  = ts.date()

            # ── Day reset ──────────────────────────────────────────────
            if bar_day != cur_day:
                cur_day      = bar_day
                trades_today = 0
                halt_day     = False
                profit_today = False

            # ── Square-off ─────────────────────────────────────────────
            if bar_time >= self.sq_off_time and trade is not None and trade.is_open():
                trade.exit_price  = o   # fill at open of square-off bar
                trade.exit_time   = ts
                trade.exit_reason = "square_off"
                self._trades.append(trade)
                cash += trade.pnl()
                if trade.pnl() > 0:
                    profit_today = True
                trade = None
                long_order  = None
                short_order = None
                sig_high = sig_low = long_stop = short_stop = sig_detected_at = sig_day = None

            # ── Cancel pending orders past end_time ────────────────────
            if bar_time >= self.end_time:
                long_order  = None
                short_order = None
                sig_high = sig_low = None

            # ── Cancel cross-day signals ───────────────────────────────
            if sig_day is not None and sig_day != bar_day:
                long_order  = None
                short_order = None
                sig_high = sig_low = long_stop = short_stop = sig_detected_at = sig_day = None

            # ── Build intra-bar price path ─────────────────────────────
            is_green = c >= o
            if is_green:
                path = [o, l, h, c]   # Open → Low → High → Close
            else:
                path = [o, h, l, c]   # Open → High → Low → Close

            # For the bar open specifically, use open price for gap-through fills
            bar_open = o

            # ── TSL update before bar (use previous close as reference) ─
            if trade is not None and trade.is_open() and self.tsl_enabled:
                _update_tsl(trade, o, self.tsl_method,
                            self.tsl_act, self.tsl_lock, self.tsl_step)

            # ── Process price path ─────────────────────────────────────
            closed_this_bar = None
            for price in path:
                # Update TSL continuously through bar
                if trade is not None and trade.is_open() and self.tsl_enabled:
                    _update_tsl(trade, price, self.tsl_method,
                                self.tsl_act, self.tsl_lock, self.tsl_step)

                # Check long order
                if long_order is not None and trade is None:
                    trade, long_order, closed = _check_price_path(
                        price, trade, long_order,
                        self.sl_config, self.tp_config, {},
                        sig_high, sig_low, ts, bar_open,
                    )
                    if closed is not None:
                        closed_this_bar = closed

                # Check short order
                if short_order is not None and trade is None:
                    trade, short_order, closed = _check_price_path(
                        price, trade, short_order,
                        self.sl_config, self.tp_config, {},
                        sig_high, sig_low, ts, bar_open,
                    )
                    if closed is not None:
                        closed_this_bar = closed

                # Check open trade SL/TP/TSL
                if trade is not None and trade.is_open():
                    _, _, closed = _check_price_path(
                        price, trade, None,
                        self.sl_config, self.tp_config, {},
                        sig_high, sig_low, ts, bar_open,
                    )
                    if closed is not None:
                        closed_this_bar = closed
                        trade = None
                        long_order  = None
                        short_order = None

            # ── Record closed trade ────────────────────────────────────
            if closed_this_bar is not None:
                self._trades.append(closed_this_bar)
                cash += closed_this_bar.pnl()
                if closed_this_bar.pnl() > 0:
                    profit_today = True
                if self.stop_after_profit and profit_today:
                    halt_day = True
                sig_high = sig_low = long_stop = short_stop = sig_detected_at = sig_day = None

            # ── Signal expiry ──────────────────────────────────────────
            if sig_detected_at is not None and trade is None:
                elapsed = i - sig_detected_at
                if elapsed >= self.expiry_candles:
                    long_order  = None
                    short_order = None
                    sig_high = sig_low = long_stop = short_stop = sig_detected_at = sig_day = None

            # ── Detect new signal ──────────────────────────────────────
            can_trade = (
                self.start_time <= bar_time < self.end_time
                and not halt_day
                and trades_today < self.max_trades
                and trade is None
                and long_order is None
                and short_order is None
                and closed_this_bar is None   # skip signal on exit bar
                and i >= 1
            )

            if can_trade:
                prev_bar = bars[i - 1]
                # Both candles must be same day
                if prev_bar.Index.date() == bar_day:
                    result = _detect_signal(
                        float(prev_bar.Open), float(prev_bar.Close),
                        float(o), float(c),
                        float(h), float(l),
                        self.buffer,
                    )
                    if result is not None:
                        sig_high, sig_low, long_stop, short_stop = result
                        sig_detected_at = i
                        sig_day = bar_day
                        trades_today += 1

                        if self.allow_long:
                            long_order = Order(
                                direction=Direction.LONG,
                                stop_price=long_stop,
                                sl=None, tp=None,
                            )
                        if self.allow_short:
                            short_order = Order(
                                direction=Direction.SHORT,
                                stop_price=short_stop,
                                sl=None, tp=None,
                            )

            equity_curve.append(cash + (trade.pnl() if trade and trade.is_open() else 0))

        # ── Close any open trade at end of data ────────────────────────
        if trade is not None and trade.is_open():
            last_bar = bars[-1]
            trade.exit_price  = last_bar.Close
            trade.exit_time   = last_bar.Index
            trade.exit_reason = "open"
            self._trades.append(trade)
            cash += trade.pnl()

        self._final_cash  = cash
        self._equity      = equity_curve

    # ------------------------------------------------------------------
    def get_trades_df(self) -> pd.DataFrame:
        if not self._trades:
            return pd.DataFrame()
        rows = []
        for t in self._trades:
            rows.append({
                "EntryTime":   t.entry_time,
                "ExitTime":    t.exit_time,
                "Duration":    t.exit_time - t.entry_time if t.exit_time else None,
                "Size":        1 if t.is_long() else -1,
                "EntryPrice":  round(t.entry_price, 2),
                "ExitPrice":   round(t.exit_price, 2) if t.exit_price else None,
                "SL":          round(t.sl, 2) if t.sl else None,
                "TP":          round(t.tp, 2) if t.tp else None,
                "ExitReason":  t.exit_reason,
                "PnL [pts]":   round(t.pnl(), 2),
            })
        return pd.DataFrame(rows)

    def get_stats(self) -> pd.Series:
        trades_df = self.get_trades_df()
        if trades_df.empty:
            return pd.Series({"# Trades": 0})

        pnl    = trades_df["PnL [pts]"]
        wins   = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        n      = len(pnl)

        win_rate      = len(wins) / n * 100 if n else 0
        gross_profit  = wins.sum()
        gross_loss    = losses.sum()
        net_profit    = pnl.sum()
        profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else float("inf")
        avg_trade     = pnl.mean()
        expectancy    = pnl.mean()

        # Sharpe (annualised, ~252 trading days, 3 trades/day ≈ 756 observations/yr)
        if pnl.std() > 0:
            sharpe = (pnl.mean() / pnl.std()) * (252 ** 0.5)
        else:
            sharpe = 0.0

        # SQN = mean/std * sqrt(n)
        sqn = (pnl.mean() / pnl.std() * (n ** 0.5)) if pnl.std() > 0 else 0.0

        # Equity curve max drawdown
        eq = pd.Series(self._equity)
        roll_max = eq.cummax()
        dd = (eq - roll_max) / roll_max * 100
        max_dd = dd.min()

        start = trades_df["EntryTime"].min()
        end   = trades_df["ExitTime"].max()

        return pd.Series({
            "Start":               start,
            "End":                 end,
            "# Trades":            n,
            "Win Rate [%]":        round(win_rate, 5),
            "Winning Trades":      int(len(wins)),
            "Losing Trades":       int(len(losses)),
            "Net Profit [pts]":    round(net_profit, 2),
            "Gross Profit [pts]":  round(gross_profit, 2),
            "Gross Loss [pts]":    round(gross_loss, 2),
            "Avg Trade [pts]":     round(avg_trade, 2),
            "Best Trade [pts]":    round(pnl.max(), 2),
            "Worst Trade [pts]":   round(pnl.min(), 2),
            "Profit Factor":       round(profit_factor, 5),
            "Sharpe Ratio":        round(sharpe, 5),
            "SQN":                 round(sqn, 5),
            "Max. Drawdown [%]":   round(max_dd, 5),
            "Return [%]":          round((self._final_cash - self._cash) / self._cash * 100, 5),
        })
