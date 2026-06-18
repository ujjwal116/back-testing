from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


BACKTEST_COLUMNS = {
    "open": "Open",
    "high": "High",
    "low": "Low",
    "close": "Close",
    "volume": "Volume",
}


def load_csv_data(config: dict[str, Any]) -> pd.DataFrame:
    data_config = config["data"]
    raw_path = Path(data_config["file"])

    # Resolve relative paths against the directory of the config file that was
    # loaded, so tests and scripts work correctly regardless of the process
    # working directory.  Absolute paths pass through unchanged.
    if not raw_path.is_absolute():
        config_dir = config.get("_config_dir")
        if config_dir is not None:
            csv_path = Path(config_dir) / raw_path
        else:
            csv_path = raw_path
    else:
        csv_path = raw_path

    if not csv_path.exists():
        raise FileNotFoundError(f"CSV file not found: {csv_path}")

    df = pd.read_csv(csv_path)
    datetime_column = data_config.get("datetime_column", "datetime")
    if datetime_column not in df.columns:
        raise ValueError(f"Datetime column '{datetime_column}' not found in {csv_path}")

    column_map = {}
    configured_columns = data_config.get("columns", {})
    for logical_name, output_name in BACKTEST_COLUMNS.items():
        source_name = configured_columns.get(logical_name, logical_name)
        if source_name not in df.columns:
            if logical_name == "volume":
                df[source_name] = 0
            else:
                raise ValueError(f"Required column '{source_name}' not found in {csv_path}")
        column_map[source_name] = output_name

    df[datetime_column] = pd.to_datetime(df[datetime_column], errors="coerce")
    source_timezone = data_config.get("source_timezone")
    market_timezone = data_config.get("market_timezone")
    if source_timezone and market_timezone:
        timestamps = df[datetime_column]
        if timestamps.dt.tz is None:
            timestamps = timestamps.dt.tz_localize(source_timezone)
        else:
            timestamps = timestamps.dt.tz_convert(source_timezone)
        df[datetime_column] = timestamps.dt.tz_convert(market_timezone).dt.tz_localize(None)
    df = df.dropna(subset=[datetime_column]).rename(columns=column_map)
    df = df.set_index(datetime_column).sort_index()

    start_date = data_config.get("start_date")
    end_date = data_config.get("end_date")
    if start_date:
        df = df[df.index >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df.index < pd.Timestamp(end_date) + pd.Timedelta(days=1)]

    required = ["Open", "High", "Low", "Close"]
    df = df.dropna(subset=required)
    df = df[["Open", "High", "Low", "Close", "Volume"]]
    return df
