"""
Regression tests — end-to-end backtest against real CSV data.

Each test runs a full bt.run() with the optimised config and asserts that the
first 5 trades match the manually verified values exactly.  Any code change
that silently breaks entry price, exit price, or PnL will be caught here.

Verified manually on 2026-06-17:
  Trade 1  13 Jun LONG   entry=24646.90  exit=24626.90  pnl=-20.00   SL hit
  Trade 2  13 Jun LONG   entry=24636.10  exit=24679.60  pnl=+43.50   TSL hit (act=50, lock=40)
  Trade 3  16 Jun SHORT  entry=24775.20  exit=24723.25  pnl=+51.95   TSL hit
  Trade 4  17 Jun SHORT  entry=24857.10  exit=24877.10  pnl=-20.00   SL hit
  Trade 5  17 Jun LONG   entry=24886.10  exit=24870.95  pnl=-15.15   SL hit (signal-candle tighter)

Config: act=50, lock=40, gap=10, end_time=13:00, buf=5, sl=20 fpsc, expiry=5

The config is frozen in tests/fixtures/regression_config.yaml and is never
touched by application code, so optimisation runs or config edits cannot
silently invalidate these tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import pandas as pd

from core.config import load_config
from core.backtest_runner import run_backtest

# Resolve relative to this file so the tests work from any working directory.
REGRESSION_CONFIG = Path(__file__).parent / "fixtures" / "regression_config.yaml"

# ---------------------------------------------------------------------------
# Expected values — sourced from manual trace (see docs/traffic_light_strategy.md §11)
# ---------------------------------------------------------------------------

EXPECTED_TRADES = [
    # (direction, entry_price, exit_price, pnl_pts)
    ( 1,  24646.90, 24626.90,  -20.00),   # T1  13 Jun LONG  SL hit
    ( 1,  24636.10, 24679.60,  +43.50),   # T2  13 Jun LONG  TSL hit (act=50, lock=40)
    (-1,  24775.20, 24723.25,  +51.95),   # T3  16 Jun SHORT TSL hit
    (-1,  24857.10, 24877.10,  -20.00),   # T4  17 Jun SHORT SL hit
    ( 1,  24886.10, 24870.95,  -15.15),   # T5  17 Jun LONG  SL hit (signal-candle tighter)
]


# ---------------------------------------------------------------------------
# Fixture: run once, reuse for all trade assertions
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def trades_df():
    config = load_config(REGRESSION_CONFIG)
    _, stats = run_backtest(config)
    trades = stats["_trades"].copy()
    # PnL in points = EntryPrice delta * Size (size=1)
    trades["pnl_pts"] = (trades["ExitPrice"] - trades["EntryPrice"]) * trades["Size"]
    return trades.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Individual trade assertions
# ---------------------------------------------------------------------------

class TestFirst5Trades:

    def test_trade_count_at_least_5(self, trades_df):
        assert len(trades_df) >= 5, "Expected at least 5 trades in backtest output"

    @pytest.mark.parametrize("idx,direction,entry,exit_price,pnl", [
        (0,  1, 24646.90, 24626.90,  -20.00),
        (1,  1, 24636.10, 24679.60,  +43.50),
        (2, -1, 24775.20, 24723.25,  +51.95),
        (3, -1, 24857.10, 24877.10,  -20.00),
        (4,  1, 24886.10, 24870.95,  -15.15),
    ])
    def test_entry_price(self, trades_df, idx, direction, entry, exit_price, pnl):
        assert trades_df.loc[idx, "EntryPrice"] == pytest.approx(entry, abs=0.01), \
            f"Trade {idx+1} entry price mismatch"

    @pytest.mark.parametrize("idx,direction,entry,exit_price,pnl", [
        (0,  1, 24646.90, 24626.90,  -20.00),
        (1,  1, 24636.10, 24679.60,  +43.50),
        (2, -1, 24775.20, 24723.25,  +51.95),
        (3, -1, 24857.10, 24877.10,  -20.00),
        (4,  1, 24886.10, 24870.95,  -15.15),
    ])
    def test_exit_price(self, trades_df, idx, direction, entry, exit_price, pnl):
        assert trades_df.loc[idx, "ExitPrice"] == pytest.approx(exit_price, abs=0.01), \
            f"Trade {idx+1} exit price mismatch"

    @pytest.mark.parametrize("idx,direction,entry,exit_price,pnl", [
        (0,  1, 24646.90, 24626.90,  -20.00),
        (1,  1, 24636.10, 24679.60,  +43.50),
        (2, -1, 24775.20, 24723.25,  +51.95),
        (3, -1, 24857.10, 24877.10,  -20.00),
        (4,  1, 24886.10, 24870.95,  -15.15),
    ])
    def test_pnl(self, trades_df, idx, direction, entry, exit_price, pnl):
        assert trades_df.loc[idx, "pnl_pts"] == pytest.approx(pnl, abs=0.01), \
            f"Trade {idx+1} PnL mismatch"

    @pytest.mark.parametrize("idx,direction,entry,exit_price,pnl", [
        (0,  1, 24646.90, 24626.90,  -20.00),
        (1,  1, 24636.10, 24679.60,  +43.50),
        (2, -1, 24775.20, 24723.25,  +51.95),
        (3, -1, 24857.10, 24877.10,  -20.00),
        (4,  1, 24886.10, 24870.95,  -15.15),
    ])
    def test_direction(self, trades_df, idx, direction, entry, exit_price, pnl):
        assert trades_df.loc[idx, "Size"] == direction, \
            f"Trade {idx+1} direction mismatch (expected {direction})"


# ---------------------------------------------------------------------------
# Overall stats regression — catches silent changes to total PnL / trade count
# ---------------------------------------------------------------------------

class TestOverallStats:

    def test_total_trades(self, trades_df):
        # act=50/lock=40/end=13:00 produces 517 trades
        assert len(trades_df) == 517, \
            f"Total trade count changed: expected 517, got {len(trades_df)}"

    def test_net_pnl_pts(self, trades_df):
        net = trades_df["pnl_pts"].sum()
        assert net == pytest.approx(2420.90, abs=0.10), \
            f"Net PnL changed: expected ~2420.90 pts, got {net:.2f}"

    def test_win_rate(self, trades_df):
        win_rate = (trades_df["pnl_pts"] > 0).mean() * 100
        assert win_rate == pytest.approx(31.53, abs=0.5), \
            f"Win rate changed: expected ~31.53%, got {win_rate:.2f}%"
