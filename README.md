# Intraday Backtesting App

Minimal Python application for backtesting intraday trading strategies using existing Fyers CSV data and Backtesting.py.

The app is intended to use CSV files already present in the `data/` folder. It does not fetch market data, connect to Fyers, use a database, or implement a custom backtesting engine.

## Current Data

This workspace currently contains:

```text
data/
└── NIFTY_5_2025-06-13_2026-06-13.csv
```

Use this file path in the config unless you add another CSV.

## Folder Structure

```text
D:\back-testing\
│
├── data\
│   └── NIFTY_5_2025-06-13_2026-06-13.csv
│
├── configs\
│   ├── traffic_light.yaml
│   └── traffic_light_optimization.yaml
│
├── docs\
│   └── traffic_light_strategy.md
│
├── strategies\
│   ├── __init__.py
│   ├── base.py
│   └── traffic_light.py
│
├── core\
│   ├── data_loader.py
│   ├── strategy_loader.py
│   ├── backtest_runner.py
│   ├── optimizer.py
│   └── reporting.py
│
├── reports\
│   ├── summaries\
│   ├── trades\
│   ├── monthly\
│   ├── plots\
│   └── optimization\
│
├── main.py
├── optimize.py
├── requirements.txt
└── README.md
```

## Install Dependencies

Install dependencies with:

```powershell
python -m pip install -r requirements.txt
```

Expected core libraries:

```text
backtesting
pandas
numpy
PyYAML
```

## Configure A Backtest

Create `configs/traffic_light.yaml` and point it to an existing CSV file:

```yaml
data:
  file: data/NIFTY_5_2025-06-13_2026-06-13.csv
  datetime_column: datetime
  source_timezone: UTC
  market_timezone: Asia/Kolkata
  start_date: "2025-07-01"
  end_date: "2025-07-31"
  columns:
    open: open
    high: high
    low: low
    close: close
    volume: volume

backtest:
  cash: 100000
  commission: 0.0003
  trade_on_close: false
  exclusive_orders: true
  finalize_trades: true

strategy:
  name: traffic_light

trading:
  session_start: "09:20"
  session_end: "15:15"
  max_trades_per_day: 3

risk:
  position_size:
    method: fixed_size
    value: 1

  stop_loss:
    method: signal_candle_opposite
    value: null
    atr_multiplier: 1.5

  take_profit:
    method: risk_reward
    value: 2.0

  trailing_stop:
    enabled: false
    method: fixed_points
    value: 50
    activation_points: 80   # activation_lock only: profit threshold to activate
    lock_points: 60          # activation_lock only: profit locked in on activation
    atr_multiplier: 2.0

indicators:
  atr:
    period: 14

reporting:
  output_dir: reports
  save_trades: true
  save_monthly_summary: true
  save_plot: true
```

Adjust `datetime_column` and column names to match the actual CSV headers. The included sample uses UTC-like timestamps, so the default config converts them to India market time before applying trading-session filters.

## Run A Backtest

```powershell
python main.py configs/traffic_light.yaml
```

The app should:

1. Load the existing CSV from `data/`.
2. Normalize columns for Backtesting.py.
3. Dynamically load the configured strategy.
4. Run the backtest.
5. Save summary, trade list, monthly report, and optional plot.

## Traffic Light Candle Strategy

> Full reference: [`docs/traffic_light_strategy.md`](docs/traffic_light_strategy.md)

Signal setup — two consecutive opposite-colour candles; the **second** is the
signal candle:

```text
Red  followed by Green
or
Green followed by Red
```

Entry rules:

```text
Stop order at signal-candle high (+buffer) → long  when price breaks above it.
Stop order at signal-candle low  (-buffer) → short when price breaks below it.
Fill is anchored to the breakout level, not the breakout bar's close.
First breakout wins; opposite breakout on the same bar is ignored.
Signal expires after signal_expiry_candles bars (default 20).
One position at a time; one trade per signal.
After a trade closes, its exit candle is re-evaluated as the next signal candle.
If price gaps through the level, SL/TP are recomputed from the actual fill.
```

Supported stop loss methods:

```text
signal_candle_opposite
fixed_points
percentage
atr
```

Supported target methods:

```text
none
fixed_points
percentage
risk_reward
```

Supported trailing stop methods:

```text
fixed_points
atr
previous_candle_high_low
activation_lock    (activates at activation_points profit, locks at lock_points, trails 1:1)
```

## Configure Optimization

Create `configs/traffic_light_optimization.yaml`:

```yaml
base_config: configs/traffic_light.yaml

optimize:
  maximize: "Equity Final [$]"

  parameters:
    stop_loss_value: [40, 60]
    take_profit_value: [1.5, 2.0]
    atr_multiplier: [1.5]
    session_start: ["09:20"]
    session_end: ["15:15"]
    max_trades_per_day: [2, 3]

  constraints:
    min_trades: 20
```

Run optimization:

```powershell
python optimize.py configs/traffic_light_optimization.yaml
```

Expected optimization outputs:

```text
reports/optimization/
├── traffic_light_best_config.yaml
├── traffic_light_best_result.csv
└── traffic_light_optimization_results.csv
```

Rerun the best configuration:

```powershell
python main.py reports/optimization/traffic_light_best_config.yaml
```

## Reports

A normal backtest should generate:

```text
reports/
├── summaries/
│   └── traffic_light_summary.csv
├── trades/
│   └── traffic_light_trades.csv
├── monthly/
│   └── traffic_light_monthly.csv
└── plots/
    └── traffic_light_plot.html
```

Summary metrics should include:

```text
Net profit
Return %
Win rate
Profit factor
Max drawdown
Number of trades
Average trade
Best trade
Worst trade
Exposure time
Equity final
```

The trade list should include:

```text
Entry time
Exit time
Direction
Entry price
Exit price
Quantity
PnL
Return %
Trade duration
Exit reason, when available
```

Monthly summary should include:

```text
Month
Total trades
Winning trades
Losing trades
Gross profit
Gross loss
Net profit
Win rate
Profit factor
```

## Adding More Strategies

Add one new file per strategy:

```text
strategies/opening_range_breakout.py
strategies/inside_bar.py
strategies/vwap_reversal.py
```

Each strategy should subclass the shared base strategy and expose configurable class variables so Backtesting.py optimization can test different values.

The core runner should not be changed when a new strategy is added. Only the config should change:

```yaml
strategy:
  name: opening_range_breakout
```

## Important Notes

- Use only existing CSV files in `data/`.
- Do not add data-fetching code.
- Do not build a custom backtesting engine.
- Keep strategy-specific logic inside `strategies/`.
- Keep data loading, optimization, and reporting generic inside `core/`.
