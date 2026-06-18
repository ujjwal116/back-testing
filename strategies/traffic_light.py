from __future__ import annotations

from strategies.base import BaseIntradayStrategy


class TrafficLightStrategy(BaseIntradayStrategy):
    # Signal rules
    signal_expiry_candles: int = 20

    # Entry
    entry_buffer_points: float = 0.0

    # Direction filters
    allow_long: bool = True
    allow_short: bool = True

    def init(self):
        super().init()
        self._clear_signal()
        self._clear_pending()

    def next(self):
        self._reset_day_if_needed()
        self._expire_cross_day_signal()
        had_closure = self._process_closed_trades()

        # At square-off time: cancel any pending entry orders then close open position.
        if self.should_square_off():
            self._cancel_pending_entries()
            self.square_off_open_position()
            return

        self.update_trailing_stop()

        # A pending entry order exists — wait for it to fill or get cancelled.
        if self._has_pending_entries():
            if self.position:
                # One of the two OCO orders filled; cancel the other and adjust brackets.
                self._on_entry_filled()
            # Else still waiting — do nothing until next bar.
            return

        if self.position:
            return

        # If a trade just closed on this bar, skip signal detection here.
        # The just-closed candle (data[-1]) will be evaluated as data[-2]
        # (signal candidate) on the following bar.
        if had_closure:
            return

        # ---- Signal detection & order placement ----
        # Both happens in the same next() call: detect pattern from completed
        # candles (data[-2], data[-1]) and immediately place both OCO stop orders.
        # The broker processes them at the OPEN of the next bar, giving correct entry timing.

        if self.signal_high is not None:
            # Active signal: check expiry.
            candles_elapsed = len(self.data.Close) - 1 - self.signal_detected_at
            if candles_elapsed >= int(self.signal_expiry_candles):
                self._clear_signal()
            # Either still valid or just cleared — either way, do not scan for a new
            # pattern when a signal is already active.
            if self.signal_high is not None:
                # Signal still alive but no breakout yet; orders are already placed.
                return

        # No active signal: scan for a new pattern.
        if not self.can_open_trade():
            return

        self._detect_signal_and_place_orders()

    # ---------- signal state ----------

    def _clear_signal(self) -> None:
        self.signal_high: float | None = None
        self.signal_low: float | None = None
        self.signal_detected_at: int | None = None
        self.signal_day = None

    def _clear_pending(self) -> None:
        self._pending_long_order = None
        self._pending_short_order = None
        self._pending_long_stop: float | None = None
        self._pending_short_stop: float | None = None
        self._pending_long_sl: float | None = None
        self._pending_long_tp: float | None = None
        self._pending_short_sl: float | None = None
        self._pending_short_tp: float | None = None
        self._pending_signal_high: float | None = None
        self._pending_signal_low: float | None = None
        self._signal_was_green: bool = False

    def _has_pending_entries(self) -> bool:
        return self._pending_long_order is not None or self._pending_short_order is not None

    def _cancel_pending_entries(self) -> None:
        for attr in ("_pending_long_order", "_pending_short_order"):
            order = getattr(self, attr)
            if order is not None:
                try:
                    order.cancel()
                except Exception:
                    pass
                setattr(self, attr, None)

    def _expire_cross_day_signal(self) -> None:
        if self.signal_day is not None and self.signal_day != self.data.index[-1].date():
            self._cancel_pending_entries()
            self._clear_signal()
            self._clear_pending()

    # ---------- detect signal & place OCO stop orders ----------

    def _detect_signal_and_place_orders(self) -> None:
        """
        Use the two most-recently completed candles (data[-2] and data[-1]) to
        detect an opposite-colour pattern.  data[-1] is the signal candle.

        If a valid pattern is found and both candles belong to the current session
        day, place two OCO stop orders:
          - buy  stop at signal_high + buffer  (if allow_long)
          - sell stop at signal_low  - buffer  (if allow_short)

        These orders are processed by the broker at the OPEN of the next bar,
        so the entry bar is the first bar after the signal candle closes — which
        is the correct, no-look-ahead behaviour.
        """
        if len(self.data.Close) < 2:
            return

        current_day = self.data.index[-1].date()

        # Both setup candles must lie within the current session day.
        if self.data.index[-1].date() != current_day:
            return
        if self.data.index[-2].date() != current_day:
            return

        prev_open = float(self.data.Open[-2])
        prev_close = float(self.data.Close[-2])
        sig_open = float(self.data.Open[-1])
        sig_close = float(self.data.Close[-1])

        prev_red = prev_close < prev_open
        prev_green = prev_close > prev_open
        sig_red = sig_close < sig_open
        sig_green = sig_close > sig_open

        if not ((prev_red and sig_green) or (prev_green and sig_red)):
            return

        sig_high = float(self.data.High[-1])
        sig_low = float(self.data.Low[-1])
        buffer = float(self.entry_buffer_points)
        long_level = sig_high + buffer
        short_level = sig_low - buffer

        # Record signal state.
        self.signal_high = sig_high
        self.signal_low = sig_low
        self.signal_detected_at = len(self.data.Close) - 1
        self.signal_day = current_day

        # Store the levels and signal bounds for bracket recalculation on gap fill.
        self._pending_signal_high = sig_high
        self._pending_signal_low = sig_low
        self._pending_long_stop = long_level
        self._pending_short_stop = short_level

        # Place stop orders in the direction(s) allowed.
        # sl= is attached to the order so backtesting.py evaluates it intra-bar
        # for cases where SL can genuinely be hit same bar (RED long, GREEN short).
        # For GREEN long / RED short the SL was already visited before entry, so
        # the same-bar hit is a false loss — this is a known backtesting.py
        # limitation (~19 trades in 4yrs, ~1% of trades, conservative bias).
        # _on_entry_filled recalculates brackets from actual fill on gap-through.
        if bool(self.allow_long):
            sl_long, tp_long = self.build_brackets("long", long_level, sig_high, sig_low)
            self._pending_long_order = self.buy(
                size=self.position_size,
                stop=long_level,
                sl=sl_long,
                tp=tp_long,
            )
            self._pending_long_sl = sl_long
            self._pending_long_tp = tp_long
        if bool(self.allow_short):
            sl_short, tp_short = self.build_brackets("short", short_level, sig_high, sig_low)
            self._pending_short_order = self.sell(
                size=self.position_size,
                stop=short_level,
                sl=sl_short,
                tp=tp_short,
            )
            self._pending_short_sl = sl_short
            self._pending_short_tp = tp_short

        self.register_entry()

    # ---------- post-fill bracket adjustment ----------

    def _on_entry_filled(self) -> None:
        """
        Called when a position opens after one of the two OCO stop orders fills.
        Cancels the other order and recalculates SL/TP from the actual fill price
        in case of a gap-through.
        """
        if not self.trades:
            return

        trade = self.trades[-1]
        fill  = float(trade.entry_price)

        if trade.is_long:
            intended_stop = self._pending_long_stop
            direction     = "long"
            base_sl       = self._pending_long_sl
            base_tp       = self._pending_long_tp
            if self._pending_short_order is not None:
                try:
                    self._pending_short_order.cancel()
                except Exception:
                    pass
            self._pending_long_order  = None
            self._pending_short_order = None
        else:
            intended_stop = self._pending_short_stop
            direction     = "short"
            base_sl       = self._pending_short_sl
            base_tp       = self._pending_short_tp
            if self._pending_long_order is not None:
                try:
                    self._pending_long_order.cancel()
                except Exception:
                    pass
            self._pending_long_order  = None
            self._pending_short_order = None

        # Recalculate brackets from actual fill if there was a gap-through.
        if intended_stop is not None and abs(fill - intended_stop) >= 0.01:
            new_sl, new_tp = self.build_brackets(
                direction, fill,
                self._pending_signal_high,
                self._pending_signal_low,
            )
            trade.sl = new_sl
            if new_tp is not None:
                trade.tp = new_tp

        self._clear_signal()
        self._clear_pending()

    # ---------- closed-trade bookkeeping ----------

    def _process_closed_trades(self) -> bool:
        """
        Inspect trades closed since the last bar.  Returns True if any new
        closure was detected so that next() can skip signal detection on the
        exit bar — allowing the exit candle to be evaluated as data[-1]
        (signal candidate) on the following bar instead.
        """
        closed_count = len(self.closed_trades)
        if closed_count <= self._closed_trades_seen:
            return False

        for trade in self.closed_trades[self._closed_trades_seen:closed_count]:
            if bool(self.stop_after_first_profit) and float(trade.pl) > 0:
                self._halt_for_day = True

        self._closed_trades_seen = closed_count

        # If there are still pending entry orders at closure time (edge case:
        # both stop orders placed but a forced close happened), cancel them.
        self._cancel_pending_entries()
        self._clear_signal()
        self._clear_pending()

        return True
