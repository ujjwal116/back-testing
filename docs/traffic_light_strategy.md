# Traffic Light Strategy — Wiki

> Intraday breakout strategy for NIFTY 5-minute candles.
> Implemented in `strategies/traffic_light.py`, built on `strategies/base.py`.

---

## 1. Concept

The "traffic light" pattern looks for a **colour flip** between two consecutive
candles and trades the breakout of the second candle (the *signal candle*).

```
Red  → Green   (bullish flip)
Green → Red    (bearish flip)
```

The **second** candle of the pair is the signal candle. Its high and low become
the breakout levels:

- Break **above** signal-candle high → go **long**
- Break **below** signal-candle low  → go **short**

Only the breakout level is traded; the colour flip is just the trigger that arms
a signal.

---

## 2. Lifecycle of a single bar (`next()`)

Each 5-minute bar runs through this sequence:

1. **Day reset** — on a new session day, reset the per-day trade counter and the
   `stop_after_first_profit` halt flag (`_reset_day_if_needed`).
2. **Cross-day signal expiry** — discard any signal left over from a previous day
   (`_expire_cross_day_signal`).
3. **Closed-trade bookkeeping** — detect trades that closed on the previous bar
   (`_process_closed_trades`), returns `had_closure`.
4. **Square-off** — at/after `square_off_time`, cancel any pending entry order and
   close the open position. Nothing else runs this bar.
5. **Trailing stop** — update trailing SL on the open trade if enabled.
6. **Pending-order tracking** —
   - if a stop order has filled → adjust brackets for gap (see §6), clear the
     pending reference, and continue;
   - if still unfilled → **return** (wait, do not look for new signals).
7. **Position guard** — if a position is open, **return**.
8. **Post-exit guard** — if a trade just closed this bar (`had_closure`),
   **return** so the exit candle can be re-evaluated as a signal candidate on the
   *next* bar (see §5).
9. **Signal detection** — scan for a colour-flip pair (`_detect_signal`).
10. **Entry** — if a valid signal exists and a trade can be opened, attempt entry
    (`_try_entry`).

---

## 3. Signal detection (`_detect_signal`)

At most **one** active signal is held at a time.

- If a signal is already active, check its age. If `candles_elapsed >=
  signal_expiry_candles` (default **20**), discard it; otherwise keep it and stop.
- Otherwise inspect the two most recently **completed** candles:
  - `data[-3]` = previous candle
  - `data[-2]` = signal candle
  - `data[-1]` = current (forming) bar — never used for detection
- **Both** setup candles must belong to the current session day (no overnight
  pairs).
- A signal is set when the pair is an opposite-colour flip:

  ```
  (prev RED  and signal GREEN)  → arm long-biased breakout
  (prev GREEN and signal RED)   → arm short-biased breakout
  ```

  On arming, record `signal_high = High[-2]`, `signal_low = Low[-2]`, the bar index
  (`signal_detected_at`), and the day (`signal_day`).

> **Doji handling:** a candle with `close == open` is neither red nor green, so it
> can never form a flip pair.

---

## 4. Entry (`_try_entry` → `_enter`)

On the bar **after** a signal is armed:

```
long_level  = signal_high + entry_buffer_points
short_level = signal_low  - entry_buffer_points

broke_high = allow_long  and  bar.high > long_level
broke_low  = allow_short and  bar.low  < short_level
```

- If **both** levels are breached on the same bar, the first mover cannot be
  determined → the signal is discarded and no trade is taken.
- Otherwise a **stop order** is placed at the breakout level:
  - long → `self.buy(stop=long_level,  sl=…, tp=…)`
  - short → `self.sell(stop=short_level, sl=…, tp=…)`

Using a **stop order** (not a market order) is what anchors the fill to the exact
signal level rather than the breakout bar's close. The order fills on a subsequent
bar when price actually reaches the level.

The signal is cleared immediately on order placement, and the per-day trade counter
is incremented (`register_entry`).

---

## 5. Re-signal after a trade closes

When a trade closes (SL, TP, or square-off), the spec requires the **exit candle**
to be eligible as the next signal candle ("the SL candle becomes the signal
candle").

This is handled by timing, not special-case code:

- On the bar where the trade closes, `had_closure` is `True` and `next()` returns
  early **before** `_detect_signal()`.
- On the **following** bar, the exit candle is now `data[-2]` and is evaluated
  normally as a signal candidate against `data[-3]`.

> **Why the early return matters:** without it, `_detect_signal()` would run on the
> exit bar itself and evaluate a stale pair (`data[-3]`/`data[-2]` from *before* the
> trade), producing phantom signals that never existed in the price action. This was
> a real bug — see §8.

---

## 6. Gap-fill bracket adjustment (`_adjust_brackets_for_gap`)

A stop order is intended to fill at the breakout level, but if the next bar **gaps
through** that level, Backtesting.py fills at the bar's open instead. Left
unadjusted, SL/TP would still be measured from the *intended* level, so the actual
risk could exceed the configured hard stop.

After a fill, the strategy compares the actual `entry_price` to the intended stop
price:

- **No gap** (difference < 0.01) → brackets are already correct, do nothing.
- **Gap** → recompute SL and TP from the **actual fill price** via
  `build_brackets`, then overwrite `trade.sl` / `trade.tp`.

This guarantees the configured stop distance (e.g. 28 points) is always measured
from the real entry.

> **Worked example (3 Jul 2025, Trade 1):** intended short stop at 25459.35, but the
> 09:30 bar opened at 25434.85 — a 24.5-point gap. SL was recomputed to
> 25434.85 + 28 = 25462.85 (instead of 25487.35), keeping the loss at the intended
> −28.00 rather than −52.50.

---

## 7. Risk model (inherited from `BaseIntradayStrategy`)

All SL/TP maths lives in the base class so every strategy shares it.

**Stop loss** (`stop_loss_method`):

| Method                         | Long SL                              | Short SL                             |
|--------------------------------|--------------------------------------|--------------------------------------|
| `signal_candle_opposite`       | `signal_low`                         | `signal_high`                        |
| `fixed_points`                 | `ref - value`                        | `ref + value`                        |
| `fixed_or_signal_candle_tighter` | `max(ref - value, signal_low)`     | `min(ref + value, signal_high)`      |
| `percentage`                   | `ref * (1 - value/100)`              | `ref * (1 + value/100)`              |
| `atr`                          | `ref - atr*mult`                     | `ref + atr*mult`                     |

**Take profit** (`take_profit_method`):

| Method          | Long TP                  | Short TP                 |
|-----------------|--------------------------|--------------------------|
| `none`          | none                     | none                     |
| `fixed_points`  | `ref + value`            | `ref - value`            |
| `percentage`    | `ref * (1 + value/100)`  | `ref * (1 - value/100)`  |
| `risk_reward`   | `ref + risk*value`       | `ref - risk*value`       |

`ref` is the entry reference price (breakout level, or actual fill after a gap).
`risk = |ref - stop_loss|`.

**Trailing stop** (`trailing_method`, only when `trailing_enabled`):

| Method                    | Behaviour                                                                                      |
|---------------------------|------------------------------------------------------------------------------------------------|
| `fixed_points`            | SL = current price ± `trailing_value`                                                          |
| `atr`                     | SL = current price ± `atr * atr_multiplier`                                                   |
| `previous_candle_high_low`| SL = previous bar's low (long) / high (short)                                                  |
| `activation_lock`         | Inactive until profit ≥ `activation_points`; then locks SL at entry + `lock_points` and trails point-for-point (gap = `activation_points − lock_points`) |

`activation_lock` example (`activation_points: 60`, `lock_points: 40`, trail gap = 20 pts):
- Long entry at 25000, original SL at 24972 (fixed 28 pts).
- While profit < 60 pts (price < 25060) → original SL unchanged.
- Price reaches 25060 → SL snaps to 25040 (locks in 40 pts profit).
- Price moves to 25061 → SL moves to 25041. Trails 1-for-1 thereafter — always 20 pts below current price.
- Price cannot pull the SL back down (trailing only ever tightens).

Trailing only ever tightens the stop (moves it in the favourable direction).

**ATR** is registered **conditionally** — `self.I(atr, …)` is only called when
`stop_loss_method == "atr"` or trailing uses ATR. This avoids the indicator
warm-up consuming the first `atr_period` bars of the session when ATR isn't used.

---

## 8. Trade gating

A new trade can open only when **all** hold (`can_open_trade`):

- inside the trading session window (`trading_start_time` ≤ now < `trading_end_time`)
- not halted for the day (`stop_after_first_profit` not yet triggered)
- `trades_today < max_trades_per_day`
- no position currently open

**One position at a time** is enforced by three independent guards in `next()`:
the pending-order check (step 6), the position check (step 7), and the
`not self.position` clause inside `can_open_trade`.

`stop_after_first_profit`: once any trade closes with positive PnL, the day is
halted. Forced square-off trades at end of day do **not** count toward the next
day's check, because `_reset_day_if_needed` advances `_closed_trades_seen` on the
day rollover.

---

## 9. Strategy parameters

Exposed as class variables (so `bt.optimize()` can grid-search them):

| Parameter               | Default | Meaning                                            |
|-------------------------|---------|----------------------------------------------------|
| `signal_expiry_candles` | 20      | Bars a signal stays valid before being discarded   |
| `entry_buffer_points`   | 0.0     | Extra points beyond the level to confirm a breakout |
| `allow_long`            | True    | Enable long entries                                |
| `allow_short`           | True    | Enable short entries                               |

Inherited tunables: `stop_loss_method`, `stop_loss_value`, `take_profit_method`,
`take_profit_value`, `trailing_enabled`, `trailing_method`, `trailing_value`,
`trailing_activation_points`, `trailing_lock_points`, `atr_period`,
`atr_multiplier`, `max_trades_per_day`, `stop_after_first_profit`,
`trading_start_time`, `trading_end_time`, `square_off_time`, `position_size`.

See `configs/traffic_light.yaml` for the YAML schema that maps to these.

---

## 10. Backtest results (Jun 2025 – Jun 2026, NIFTY 5-min)

### Baseline config
SL `fixed_or_signal_candle_tighter` 28 pts, TP `none`, trailing `activation_lock`
(`activation_points: 60`, `lock_points: 40`), `signal_expiry_candles: 20`,
`entry_buffer_points: 0`, session 09:15–14:30, square-off 15:15,
`max_trades_per_day: 3`, `stop_after_first_profit: true`.

| Metric            | Value       |
|-------------------|-------------|
| Trades            | 533         |
| Win Rate          | 25.3%       |
| Equity Final      | $100,265    |
| Return            | +0.27%      |
| Max Drawdown      | −0.87%      |
| Profit Factor     | 1.027       |
| Sharpe Ratio      | 0.26        |

**Observation:** 75% of trades hit the fixed SL before price reaches the +60 pt
activation threshold. The trailing stop works correctly but rarely fires because
most trades fail early.

### Optimised config (best of 48-combination grid search)

Parameters swept: `entry_buffer_points` [0, 5, 10], `signal_expiry_candles` [5, 20],
`stop_loss_value` [20, 35], `trailing_activation_points` [40, 60],
`trailing_lock_points` [20, 30]. Objective: maximise `Equity Final [$]`,
minimum 30 trades enforced.

**Best parameters found:**

| Parameter                    | Baseline | Optimised  |
|------------------------------|----------|------------|
| `entry_buffer_points`        | 0        | **5**      |
| `signal_expiry_candles`      | 20       | **5**      |
| `stop_loss_value`            | 28       | **20**     |
| `trailing_activation_points` | 60       | **40**     |
| `trailing_lock_points`       | 40       | **30**     |
| trail gap (act − lock)       | 20 pts   | **10 pts** |

**Results comparison:**

| Metric            | Baseline   | Optimised    | Change        |
|-------------------|------------|--------------|---------------|
| Trades            | 533        | 502          | −31           |
| **Win Rate**      | 25.3%      | **35.9%**    | **+10.5 pts** |
| Equity Final      | $100,265   | **$101,992** | +$1,727       |
| Return            | +0.27%     | **+1.99%**   | +7.5×         |
| Max Drawdown      | −0.87%     | **−0.35%**   | −60%          |
| Profit Factor     | 1.027      | **1.296**    | +26%          |
| Sharpe Ratio      | 0.26       | **2.56**     | +10×          |

**Key insight:** `signal_expiry_candles: 5` + `entry_buffer_points: 5` do most of
the work — they eliminate stale and weak breakouts that account for the majority
of SL hits. The lower activation threshold (40 pts) means more trades reach the
trailing lock before being stopped out.

Best config saved at `reports/optimization/traffic_light_best_config.yaml`.
To run: `python main.py reports/optimization/traffic_light_best_config.yaml`

---

## 11. Worked day — 3 Jul 2025

Config: SL `fixed_points` 28, TP `fixed_points` 60, `max_trades_per_day` 3,
session 09:15–14:30, square-off 15:15.

| # | Dir   | Entry              | Exit               | Result    | PnL    |
|---|-------|--------------------|--------------------|-----------|--------|
| 1 | Short | 09:30 @ 25434.85   | 09:35 @ 25462.85   | SL (gapped)| −28.00 |
| 2 | Short | 10:20 @ 25533.40   | 10:25 @ 25561.40   | SL hit    | −28.00 |
| 3 | Short | 10:40 @ 25550.35   | 11:45 @ 25490.35   | TP hit    | +60.00 |

Net +4.00 points. All three verified candle-by-candle against the raw CSV:

- **T1** — 09:15 GREEN → 09:20 RED flip; signal_low 25459.35 broken by 09:25; stop
  order fills on the 09:30 gap-open; SL recomputed from actual fill (§6).
- **T2** — after T1's exit bar is skipped, re-evaluation finds 09:55 GREEN →
  10:00 RED; signal_low 25533.40 broken; fills 10:20; SL hit 10:25.
- **T3** — after T2's exit bar is skipped, 10:20 GREEN → 10:25 RED;
  signal_low 25550.35 broken; fills 10:40; TP hit 11:45. Day caps at 3 trades.

---

## 12. Test suite

The test suite lives in `tests/` and is run with:

```bash
python -m pytest tests/ -v
```

**59 tests, ~5 seconds to run.**

---

### 12.1 Unit tests — `tests/test_calculations.py`

Pure calculation tests. No CSV files, no `bt.run()`, no Backtesting.py internals.
Each test instantiates a minimal concrete stub of `BaseIntradayStrategy` and calls
the calculation method directly.

| Test class | What it covers |
|---|---|
| `TestStopLossFixed` | `fixed_points` SL for long and short, fractional values |
| `TestStopLossSignalCandle` | `signal_candle_opposite` — long uses signal_low, short uses signal_high |
| `TestStopLossTighter` | `fixed_or_signal_candle_tighter` — correct min/max for both directions; verified against real Trade 1 and Trade 2 values |
| `TestTakeProfitFixed` | `fixed_points` TP long/short; `none` returns None |
| `TestTakeProfitRiskReward` | `risk_reward` 2R long/short; None when no SL |
| `TestTSLActivationLockLong` | below activation → None; at activation → locks SL; above → trails 1-for-1; custom gap; Trade 2 bar 19 real value |
| `TestTSLActivationLockShort` | mirror of above for shorts; Trade 3 bar 81 real value |
| `TestTSLRatchet` | SL never moves backward — for longs (candidate < current SL → blocked) and shorts (price bounce → None returned, SL frozen) |
| `TestCandleColour` | green/red/doji classification logic |
| `TestEntryLevel` | `signal_high + buffer` and `signal_low - buffer`; Trade 1 and Trade 3 real values |

**How the stub works:**

`BaseIntradayStrategy` is abstract (requires `next()`). Tests use a thin concrete
subclass `_ConcreteStrategy(BaseIntradayStrategy)` with a no-op `next()`.
Instances are created via `object.__new__()` to bypass Backtesting.py's
`__init__`, then attributes are set directly. For TSL tests that read
`self.data.Close[-1]`, the `data` property (read-only on `Strategy`) is
overridden at the class level to return a stub object.

---

### 12.2 Regression tests — `tests/test_regression.py`

End-to-end tests that run a full `bt.run()` against the real NIFTY CSV and assert
the output matches manually verified values. If any code change silently breaks
execution logic, these fail immediately.

**First 5 trades are locked** (verified candle-by-candle against raw CSV on 2026-06-17 — see §11):

| Trade | Date | Dir | Entry | Exit | PnL | Exit reason |
|---|---|---|---|---|---|---|
| 1 | 13 Jun | LONG | 24646.90 | 24626.90 | −20.00 | SL hit |
| 2 | 13 Jun | LONG | 24636.10 | 24671.90 | +35.80 | TSL activated bar 19, hit bar 20 |
| 3 | 16 Jun | SHORT | 24775.20 | 24723.25 | +51.95 | TSL activated bar 81, gap exit bar 82 |
| 4 | 17 Jun | SHORT | 24857.10 | 24877.10 | −20.00 | SL hit bar 162 |
| 5 | 17 Jun | LONG | 24886.10 | 24870.95 | −15.15 | SL (signal candle tighter) bar 169 |

Each trade asserts: entry price, exit price, PnL (±0.01 pts), and direction.

**Overall stats are also locked** — any change to strategy logic that affects the
full 502-trade run will be caught:

| Stat | Expected |
|---|---|
| Total trades | 502 |
| Net PnL | ~1991.75 pts |
| Win rate | ~35.86% |

---

### 12.3 What the tests catch vs. what they don't

**Caught automatically:**

- Wrong `min`/`max` in `fixed_or_signal_candle_tighter` (long vs short)
- TSL activating at the wrong threshold
- TSL gap formula (`activation - lock`) calculated incorrectly
- Signal expiry off by one bar (regression test)
- Gap-fill bracket adjustment not firing (regression test)
- Re-signal happening on exit bar instead of next bar (regression test)
- Any parameter change that alters trade count, net PnL, or win rate

**Not caught:**

- Whether the strategy is profitable on unseen data
- Edge cases in Backtesting.py's own order matching engine
- Whether the signal pattern makes trading sense

---

## 13. Change log (recent fixes)

- **Stop-order entry** — replaced market-on-close entry with a stop order at the
  breakout level so the fill price equals the signal level, not the breakout bar's
  close.
- **Conditional ATR init** — ATR indicator only registered when actually used,
  recovering the first `atr_period` bars of day one.
- **Re-signal timing** — `next()` now returns on the exit bar so the exit candle is
  evaluated as a signal candidate on the following bar, eliminating phantom signals
  from stale candle pairs.
- **Gap-fill bracket adjustment** — SL/TP recomputed from the actual fill price when
  a stop order gaps through its level, preserving the configured hard-stop distance.
- **`activation_lock` trailing stop** — new trailing method that stays inactive
  until profit reaches `activation_points`, then locks the SL at `entry +
  lock_points` and trails point-for-point (maintaining a gap of
  `activation_points − lock_points`). Configured via `trailing_stop.activation_points`
  and `trailing_stop.lock_points` in the YAML.
- **`fixed_or_signal_candle_tighter` SL method** — uses whichever of fixed-points
  or signal-candle-opposite SL is closer to entry (tighter risk).
- **Optimisation run (Jun 2026)** — 48-combination grid search over
  `entry_buffer_points`, `signal_expiry_candles`, `stop_loss_value`,
  `trailing_activation_points`, `trailing_lock_points`. Best result: win rate
  25% → 36%, return 0.27% → 1.99%, Sharpe 0.26 → 2.56. Key driver: fresher
  signals (`expiry: 5`) + small breakout buffer (`buffer: 5`) filter out the
  weak entries responsible for most SL hits. See §10 for full comparison.
- **Test suite added (Jun 2026)** — 59 pytest tests in `tests/`. Unit tests
  cover all SL/TP/TSL calculation methods using a lightweight stub (no CSV, no
  `bt.run()`). Regression tests lock the first 5 manually verified trades
  (entry/exit price, PnL, direction) and the overall run stats (502 trades,
  ~1992 pts, 35.86% win rate). See §12 for full details.
