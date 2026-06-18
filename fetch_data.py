"""
fetch_data.py — Download historical OHLCV candles from Fyers and save to data/.

Usage:
    python fetch_data.py --symbol NIFTY --tf 5 --from 2022-01-01 --to 2025-06-12

The output CSV is compatible with core/data_loader.py (UTC timestamps, lowercase
column names: datetime, open, high, low, close, volume).

If the stored token is expired, the browser opens automatically for re-auth.
If the token expires mid-fetch, it is regenerated and the fetch retried once.

Supported symbols  : NIFTY, BANKNIFTY, SENSEX, FINNIFTY  (or any raw Fyers symbol)
Supported timeframes: 1 3 5 10 15 25 30 60 120 240 D
"""
from __future__ import annotations

import argparse
import sys

from core.fyers_fetcher import FyersAuthError, fetch_and_save, resolve_symbol, resolve_timeframe
from core.fyers_token import get_valid_token, load_creds, regenerate_token


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch Fyers historical candles and save to data/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python fetch_data.py --symbol NIFTY --tf 5 "
            "--from 2022-01-01 --to 2025-06-12\n"
            "  python fetch_data.py --symbol BANKNIFTY --tf 15 "
            "--from 2024-01-01 --to 2024-12-31\n"
            "  python fetch_data.py --symbol NSE:NIFTY50-INDEX --tf D "
            "--from 2020-01-01 --to 2025-06-12\n"
        ),
    )
    parser.add_argument(
        "--symbol", "-s",
        required=True,
        help="Short name (NIFTY, BANKNIFTY, SENSEX, FINNIFTY) or full Fyers symbol",
    )
    parser.add_argument(
        "--tf", "-t",
        required=True,
        metavar="TIMEFRAME",
        help="Candle timeframe: 1 3 5 10 15 25 30 60 120 240 D",
    )
    parser.add_argument(
        "--from", "-f",
        required=True,
        dest="range_from",
        metavar="YYYY-MM-DD",
        help="Start date (inclusive)",
    )
    parser.add_argument(
        "--to", "-T",
        required=True,
        dest="range_to",
        metavar="YYYY-MM-DD",
        help="End date (inclusive)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    symbol_name, fyers_symbol = resolve_symbol(args.symbol)
    timeframe = resolve_timeframe(args.tf)

    print(f"Symbol    : {symbol_name} ({fyers_symbol})")
    print(f"Timeframe : {timeframe}")
    print(f"Range     : {args.range_from} -> {args.range_to}")
    print()

    # --- Step 1: get a valid token (auto-regen if expired before we start) ---
    client_id, access_token = get_valid_token()

    # --- Step 2: fetch (with one automatic retry on mid-fetch auth failure) ---
    for attempt in (1, 2):
        try:
            output_path = fetch_and_save(
                client_id=client_id,
                access_token=access_token,
                symbol_name=symbol_name,
                fyers_symbol=fyers_symbol,
                timeframe=timeframe,
                range_from=args.range_from,
                range_to=args.range_to,
            )
            break  # success
        except FyersAuthError as exc:
            if attempt == 2:
                print(f"\nAuthentication failed again after token refresh: {exc}", file=sys.stderr)
                sys.exit(1)
            print(f"\nToken rejected mid-fetch ({exc}). Regenerating token and retrying...")
            creds = load_creds()
            access_token = regenerate_token(creds)

    # --- Step 3: confirm ---
    import pandas as pd  # noqa: PLC0415 — deferred; pandas already loaded by fetcher
    df = pd.read_csv(output_path, nrows=5)
    print(f"\nSaved {sum(1 for _ in open(output_path)) - 1} rows -> {output_path.resolve()}")
    print("\nSample (first 5 rows):")
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
