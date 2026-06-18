# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Minimal Python intraday backtesting application for Indian equity markets (NIFTY). Uses [Backtesting.py](https://kernc.github.io/backtesting.py/) as the engine. Config-driven: strategies are selected and parameterised entirely via YAML — no changes to `core/` are needed to add a new strategy.

**Hard constraints:**
- Do not build a custom backtesting engine.
- Only use CSV files in `data/` as backtest input (fetched via `fetch_data.py` or placed manually).
- Keep strategy-specific logic in `strategies/`; keep data loading, optimization, and reporting generic in `core/`.
- Data-fetching code lives exclusively in `core/fyers_token.py`, `core/fyers_fetcher.py`, and `fetch_data.py` — do not scatter API calls elsewhere.

## Commands

```bash
# Install dependencies
python -m pip install -r requirements.txt

# Fetch historical data from Fyers (auto-opens browser if token is expired)
python fetch_data.py --symbol NIFTY --tf 5 --from 2022-01-01 --to 2025-06-12

# Run a backtest
python main.py configs/traffic_light.yaml

# Run parameter optimization
python optimize.py configs/traffic_light_optimization.yaml

# Rerun the best config from a previous optimization
python main.py reports/optimization/traffic_light_best_config.yaml
```

There is no lint, test, or build pipeline.

## Architecture

### Data flow — fetch (`fetch_data.py`)

1. `core/fyers_token.py: get_valid_token()` — loads `config/fyers_creds.json`, decodes the JWT exp claim, auto-opens the browser for OAuth if expired, saves the new token back to the file
2. `core/fyers_fetcher.py: resolve_symbol / resolve_timeframe` — maps short names (NIFTY → `NSE:NIFTY50-INDEX`) and validates the timeframe arg
3. `core/fyers_fetcher.py: fetch_and_save()` — builds a `FyersModel`, chunks intraday ranges into 90-day windows (Fyers limit), calls `fyers.history()`, normalises timestamps to UTC, deduplicates, and writes `data/<SYMBOL>_<TF>_<from>_<to>.csv`
4. `fetch_data.py` catches `FyersAuthError` (token rejected mid-fetch), calls `regenerate_token()` once, and retries automatically

Credentials file: `config/fyers_creds.json` (gitignored). Copy from `config/fyers_creds.json.example` and fill in `client_id` and `secret_key`. `access_token` is written automatically after the first login.

### Data flow — backtest (`main.py`)

1. `core/config.py: load_config(yaml_path)` → plain Python dict
2. `core/data_loader.py: load_csv_data(config)` — reads CSV, converts UTC → IST (`Asia/Kolkata`), normalises column names to Backtesting.py format (`Open/High/Low/Close/Volume`), applies `start_date`/`end_date` filter
3. `core/strategy_loader.py: load_strategy(name)` — dynamically imports `strategies.<name>` module, finds `<PascalCase>Strategy` class (e.g. `traffic_light` → `TrafficLightStrategy`)
4. `core/backtest_runner.py` — constructs `Backtest(data, strategy, ...)`, calls `build_strategy_params(config)` to flatten nested YAML into flat kwargs, runs `bt.run(**strategy_params)`
5. `core/reporting.py: save_reports(config, stats, bt)` — writes timestamped summary CSV, trades CSV, monthly CSV, and HTML plot under `reports/`

### Data flow — optimization (`optimize.py`)

1. Loads optimization YAML (`base_config` pointer + parameter grid + constraints)
2. Merges base config with grid lists; calls `bt.optimize()` with a custom objective that enforces `min_trades`
3. Writes `reports/optimization/`: best config YAML (reconstructed via `config_with_params`), best result CSV, full grid CSV

### Key abstractions

**`strategies/base.py: BaseIntradayStrategy`** — all strategies inherit from this. Owns:
- Trading session gating (`is_inside_session`, `can_open_trade`)
- Daily trade counter + `stop_after_first_profit` logic
- Force square-off at `square_off_time`
- ATR indicator
- All risk calculations: stop loss (`signal_candle_opposite`, `fixed_points`, `percentage`, `atr`), take profit (`fixed_points`, `percentage`, `risk_reward`), trailing stop (`fixed_points`, `atr`, `previous_candle_high_low`)
- `_reset_day_if_needed` advances `_closed_trades_seen` on day rollover so forced square-off trades don't pollute the next day's `stop_after_first_profit` check

**`strategies/traffic_light.py: TrafficLightStrategy`** — detects red→green or green→red candle pairs and enters on breakout. Key behaviours:
- `_detect_signal`: scans `data[-3]`/`data[-2]` for opposite-colour pairs; both candles must be within the current session day. Signals expire after `signal_expiry_candles` bars (default 20).
- `_try_entry`: long if `bar.high > signal_high + entry_buffer_points`, short if `bar.low < signal_low - entry_buffer_points`; ambiguous (both breached same bar) → skip.
- `_enter`: SL/TP anchored to the **fill price** (`Close[-1]`, since `trade_on_close=True`). `signal_candle_opposite` SL uses the signal level directly and has a guard that skips orders where fill is already past SL.
- **Re-signal**: after any trade closes, the signal was already cleared by `_enter`. `_detect_signal` re-evaluates the current bar's completed candles (which includes the exit bar), so the spec's "SL candle becomes signal candle" behaviour emerges naturally without special-case code.
- Direction filtering via `allow_long` / `allow_short`.

**`core/config.py`** — `build_strategy_params` / `config_with_params` form a two-way bridge between the nested YAML structure and the flat class-variable dict that Backtesting.py's `bt.run()` / `bt.optimize()` expects.

## Adding a New Strategy

1. Create `strategies/<snake_name>.py` with class `<PascalCase>Strategy(BaseIntradayStrategy)`
2. Override `init()` (call `super().init()`) and `next()`
3. Expose tunable parameters as class variables (for optimization)
4. Set `strategy.name: <snake_name>` in the YAML config — `core/` needs no changes

## Config Schema Reference

```yaml
data:
  file:              # path to CSV in data/
  datetime_column:   # CSV column name for timestamps
  source_timezone:   # e.g. UTC
  market_timezone:   # e.g. Asia/Kolkata
  start_date / end_date

backtest:
  cash, commission, trade_on_close, exclusive_orders, finalize_trades

strategy:
  name:              # snake_case name matching strategies/<name>.py

trading:
  trading_start_time / trading_end_time   # HH:MM, IST
  square_off_time                         # optional force-close time (default = trading_end_time)
  max_trades_per_day
  stop_after_first_profit                 # bool

risk:
  position_size:  { method: fixed_size, value }
  stop_loss:      { method, value, atr_multiplier }
  take_profit:    { method, value }
  trailing_stop:  { enabled, method, value, atr_multiplier }

indicators:
  atr: { period }

strategy_params:   # pass-through dict → strategy class variables
  # TrafficLightStrategy-specific:
  signal_expiry_candles, entry_buffer_points, allow_long, allow_short

reporting:
  output_dir, save_trades, save_monthly_summary, save_plot
```

Optimization YAML adds:
```yaml
base_config: configs/traffic_light.yaml
optimize:
  maximize: "Equity Final [$]"
  parameters:      # each key maps to a list of values to grid-search
  constraints:
    min_trades: 20
```
