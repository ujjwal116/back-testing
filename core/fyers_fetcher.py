"""
Fyers historical data fetcher for back-testing.

Fetches OHLCV candles from the Fyers API in 90-day chunks (intraday limit),
normalises them to the CSV format expected by core/data_loader.py, and saves
to data/<SYMBOL>_<TF>_<from>_<to>.csv.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

INTER_CHUNK_DELAY_SECONDS = 1  # stay well inside Fyers rate limits

try:
    from fyers_apiv3 import fyersModel
except ImportError:
    fyersModel = None

AUTH_ERROR_CODES = {-16, -17}
INTRADAY_CHUNK_DAYS = 90


class FyersAuthError(Exception):
    """Raised when Fyers returns an authentication / token-expiry error."""

# Fyers symbol map: short name → Fyers symbol string
SYMBOL_MAP: dict[str, str] = {
    "NIFTY":     "NSE:NIFTY50-INDEX",
    "BANKNIFTY": "NSE:NIFTYBANK-INDEX",
    "SENSEX":    "BSE:SENSEX-INDEX",
    "FINNIFTY":  "NSE:FINNIFTY-INDEX",
}

TIMEFRAME_MAP: dict[str, str] = {
    "1": "1", "3": "3", "5": "5", "10": "10",
    "15": "15", "25": "25", "30": "30",
    "60": "60", "120": "120", "240": "240",
    "D": "D",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_date(value: str, label: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise SystemExit(f"Invalid {label} date '{value}'. Use YYYY-MM-DD.")


def _date_chunks(range_from: str, range_to: str, chunk_days: int = INTRADAY_CHUNK_DAYS):
    start = _parse_date(range_from, "from")
    end   = _parse_date(range_to,   "to")
    if start > end:
        raise SystemExit("--from date must be before --to date.")
    current = start
    while current <= end:
        chunk_end = min(current + timedelta(days=chunk_days - 1), end)
        yield current.isoformat(), chunk_end.isoformat()
        current = chunk_end + timedelta(days=1)


def _fetch_one(fyers, symbol: str, timeframe: str, range_from: str, range_to: str) -> dict:
    return fyers.history({
        "symbol":      symbol,
        "resolution":  timeframe,
        "date_format": "1",
        "range_from":  range_from,
        "range_to":    range_to,
        "cont_flag":   "1",
    })


def _extract_candles(response: dict) -> list:
    if "candles" in response:
        return response["candles"]
    message = str(response.get("message", "Unknown Fyers error"))
    if response.get("code") in AUTH_ERROR_CODES or "auth" in message.lower():
        raise FyersAuthError(message)
    raise SystemExit(f"Fyers history error: {message}")


def _normalize(candles: list) -> pd.DataFrame:
    """Convert raw Fyers candle list to a clean DataFrame in UTC."""
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
    df = df[["datetime", "open", "high", "low", "close", "volume"]].copy()
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    return df


def _build_output_path(symbol_name: str, timeframe: str, range_from: str, range_to: str) -> Path:
    """e.g.  data/NIFTY_5_2022-01-01_2025-06-12.csv"""
    Path("data").mkdir(parents=True, exist_ok=True)
    tf_label = timeframe if str(timeframe).upper() == "D" else str(timeframe)
    fname = f"{symbol_name}_{tf_label}_{range_from}_{range_to}.csv"
    return Path("data") / fname


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_symbol(symbol_arg: str) -> tuple[str, str]:
    """
    Resolve a short name (e.g. 'NIFTY') or a raw Fyers symbol
    (e.g. 'NSE:NIFTY50-INDEX') to (display_name, fyers_symbol).
    """
    upper = symbol_arg.upper()
    if upper in SYMBOL_MAP:
        return upper, SYMBOL_MAP[upper]
    # Assume caller passed a full Fyers symbol string
    display = upper.split(":")[1].split("-")[0] if ":" in upper else upper
    return display, symbol_arg


def resolve_timeframe(tf_arg: str) -> str:
    tf = str(tf_arg).upper()
    if tf in TIMEFRAME_MAP:
        return TIMEFRAME_MAP[tf]
    raise SystemExit(
        f"Unsupported timeframe '{tf_arg}'.\n"
        f"Supported: {', '.join(TIMEFRAME_MAP.keys())}"
    )


def fetch_and_save(
    client_id: str,
    access_token: str,
    symbol_name: str,
    fyers_symbol: str,
    timeframe: str,
    range_from: str,
    range_to: str,
) -> Path:
    """
    Fetch candles from Fyers, save to data/, return the output path.
    Automatically chunks intraday requests into 90-day windows.
    Raises FyersAuthError if the token is rejected mid-fetch (caller should
    regenerate the token and retry).
    """
    if fyersModel is None:
        raise SystemExit(
            "fyers-apiv3 is not installed.\n"
            "Run: pip install fyers-apiv3"
        )

    fyers = fyersModel.FyersModel(
        client_id=client_id,
        token=access_token,
        is_async=False,
        log_path="",
    )

    is_intraday = str(timeframe).upper() != "D"

    if is_intraday:
        chunks = list(_date_chunks(range_from, range_to))
        print(f"Fetching {len(chunks)} chunk(s) of up to {INTRADAY_CHUNK_DAYS} days each...")
        all_candles: list = []
        for i, (chunk_from, chunk_to) in enumerate(chunks, 1):
            print(f"  [{i}/{len(chunks)}] {chunk_from} -> {chunk_to}")
            resp = _fetch_one(fyers, fyers_symbol, timeframe, chunk_from, chunk_to)
            all_candles.extend(_extract_candles(resp))
            if i < len(chunks):
                time.sleep(INTER_CHUNK_DELAY_SECONDS)
    else:
        print("Fetching daily data (single request)...")
        resp = _fetch_one(fyers, fyers_symbol, timeframe, range_from, range_to)
        all_candles = _extract_candles(resp)

    if not all_candles:
        raise SystemExit("No candle data returned from Fyers.")

    df = _normalize(all_candles)
    if df.empty:
        raise SystemExit("No candle data after normalisation.")

    output_path = _build_output_path(symbol_name, timeframe, range_from, range_to)
    df.to_csv(output_path, index=False)

    return output_path
