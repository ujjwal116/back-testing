from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd


SUMMARY_KEYS = [
    "Start",
    "End",
    "Duration",
    "Exposure Time [%]",
    "Return [%]",
    "Return (Ann.) [%]",
    "# Trades",
    "Win Rate [%]",
    "Best Trade [%]",
    "Worst Trade [%]",
    "Profit Factor",
    "SQN",
]


def _add_pnl_points(trades: pd.DataFrame) -> pd.DataFrame:
    """Add a PnL [pts] column = price-difference per unit, direction-adjusted."""
    if trades.empty:
        return trades
    trades = trades.copy()
    if "EntryPrice" in trades.columns and "ExitPrice" in trades.columns and "Size" in trades.columns:
        sign = trades["Size"].apply(lambda s: 1 if float(s) > 0 else -1)
        trades["PnL [pts]"] = (
            (trades["ExitPrice"].astype(float) - trades["EntryPrice"].astype(float)) * sign
        ).round(2)
    return trades


def _method_abbr(method: str) -> str:
    return {
        "fixed_points":                 "fp",
        "percentage":                   "pct",
        "atr":                          "atr",
        "signal_candle_opposite":       "sc",
        "risk_reward":                  "rr",
        "fixed_or_signal_candle_tighter": "fpsc",
        "none":                         "none",
        "previous_candle_high_low":     "pclh",
        "activation_lock":              "act",
    }.get(method, method[:4] if method else "")


def _tf_label(tf: str) -> str:
    """Map raw timeframe string from filename to human-readable label."""
    return {
        "1": "1min", "2": "2min", "3": "3min", "5": "5min",
        "10": "10min", "15": "15min", "25": "25min", "30": "30min",
        "60": "1h", "75": "75min", "120": "2h", "240": "4h",
        "D": "daily", "W": "weekly",
    }.get(tf, f"{tf}min")


def _symbol_and_timeframe(config: dict[str, Any]) -> tuple[str, str]:
    """
    Split the data filename into (symbol, timeframe_label).
      NIFTY_5_2025-06-13_2026-06-13.csv  →  ("NIFTY", "5min")
    Falls back to ("SYMBOL_TF", "unknown") if the pattern is not found.
    """
    import re
    file_path = config.get("data", {}).get("file", "")
    stem = Path(file_path).stem       # e.g. NIFTY_5_2025-06-13_2026-06-13
    # SYMBOL_TF_YYYY… — capture everything before _TF_YYYY
    m = re.match(r'^(.+?)_([^_]+)_\d{4}', stem)
    if m:
        return m.group(1), _tf_label(m.group(2))
    # Fallback: strip date suffix only
    m2 = re.match(r'^(.+?)_\d{4}', stem)
    if m2:
        return m2.group(1), "unknown"
    return stem, "unknown"


def _run_tag(config: dict[str, Any]) -> str:
    """
    Build a human-readable run tag.  Major components separated by '--':

        buf{N}--SL{val}-{method}--TP{val}-{method}[--TSL-{detail}]--sap{0|1}-mtd{N}--{start}-{end}

    Examples:
        buf0--SL28-fpsc--TP60-fp--sap1-mtd3--20250613-20260613
        buf0--SL28-fpsc--TP60-fp--TSL-act80-lk60--sap1-mtd3--20250613-20260613
        buf5--SL-sc--TP-rr2.0--TSL-20-fp--sap0-mtd2--20250101-20251231
    """
    sp  = config.get("strategy_params", {})
    trd = config.get("trading", {})
    rsk = config.get("risk", {})
    dat = config.get("data", {})

    buf = int(float(sp.get("entry_buffer_points", 0)))

    # ── Stop loss ────────────────────────────────────────────────────────────
    sl     = rsk.get("stop_loss", {})
    sl_val = sl.get("value", "")
    sl_m   = _method_abbr(sl.get("method", ""))
    sl_tag = f"SL{sl_val}-{sl_m}" if sl_val != "" and sl_val is not None else f"SL-{sl_m}"

    # ── Take profit ───────────────────────────────────────────────────────────
    tp        = rsk.get("take_profit", {})
    tp_val    = tp.get("value", "")
    tp_method = tp.get("method", "none")
    if tp_method == "risk_reward":
        tp_tag = f"TP-rr{tp_val}"
    elif tp_method in (None, "none"):
        tp_tag = "TP-none"
    else:
        tp_m   = _method_abbr(tp_method)
        tp_tag = f"TP{tp_val}-{tp_m}" if tp_val != "" and tp_val is not None else f"TP-{tp_m}"

    # ── Trailing stop ─────────────────────────────────────────────────────────
    ts     = rsk.get("trailing_stop", {})
    ts_tag = ""
    if ts.get("enabled"):
        ts_method = ts.get("method", "none")
        if ts_method == "activation_lock":
            act    = ts.get("activation_points", 80)
            lk     = ts.get("lock_points", 60)
            ts_tag = f"--TSL-act{act}-lk{lk}"
        elif ts_method == "fixed_points":
            ts_tag = f"--TSL-{ts.get('value', '')}-fp"
        elif ts_method == "atr":
            mult   = ts.get("atr_multiplier", "")
            ts_tag = f"--TSL-atr{mult}"
        elif ts_method == "previous_candle_high_low":
            ts_tag = "--TSL-pclh"
        else:
            ts_tag = f"--TSL-{_method_abbr(ts_method)}"

    sap  = int(bool(trd.get("stop_after_first_profit", False)))
    mtd  = trd.get("max_trades_per_day", 3)

    start    = str(dat.get("start_date", "")).replace("-", "")
    end      = str(dat.get("end_date",   "")).replace("-", "")
    date_tag = f"{start}-{end}" if start and end else datetime.now().strftime("%Y%m%d")

    return f"buf{buf}--{sl_tag}--{tp_tag}{ts_tag}--sap{sap}-mtd{mtd}--{date_tag}"


def save_reports(config: dict[str, Any], stats: pd.Series, bt=None, trades_override: pd.DataFrame | None = None, engine: str = "backtesting") -> dict[str, Path]:
    reporting     = config.get("reporting", {})
    base_dir      = Path(reporting.get("output_dir", "reports"))
    strategy_name = config.get("strategy", {}).get("name", "strategy")

    symbol, tf_label = _symbol_and_timeframe(config)

    tag     = _run_tag(config)

    # FSL / TSL subfolder based on whether trailing stop is active
    trailing   = config.get("risk", {}).get("trailing_stop", {})
    sl_mode    = "TSL" if trailing.get("enabled") else "FSL"

    # reports/<symbol>/<timeframe>/<strategy>/FSL|TSL/<engine>/<run_tag>/
    run_dir = base_dir / symbol / tf_label / strategy_name / sl_mode / engine / tag
    run_dir.mkdir(parents=True, exist_ok=True)

    paths: dict[str, Path] = {}

    # Use trades_override if provided (custom engine), else extract from stats
    if trades_override is not None:
        trades = trades_override.copy()
        if "PnL [pts]" not in trades.columns and "PnL" in trades.columns:
            trades["PnL [pts]"] = trades["PnL"]
    else:
        trades = _add_pnl_points(stats.get("_trades", pd.DataFrame()).copy())

    # Augment summary with points-based metrics derived from trades.
    summary = {key: stats[key] for key in SUMMARY_KEYS if key in stats.index}
    # For custom engine stats, also pull extra keys it provides directly
    extra_keys = ["Winning Trades", "Losing Trades", "Net Profit [pts]",
                  "Gross Profit [pts]", "Gross Loss [pts]", "Avg Trade [pts]",
                  "Best Trade [pts]", "Worst Trade [pts]", "Sharpe Ratio",
                  "Max. Drawdown [%]", "SQN", "Profit Factor"]
    for k in extra_keys:
        if k in stats.index and k not in summary:
            summary[k] = stats[k]

    summary_path = run_dir / "summary.csv"
    pd.Series(summary, name="value").to_csv(summary_path)
    paths["summary"] = summary_path

    if reporting.get("save_trades", True):
        trades_path = run_dir / "trades.csv"
        trades.to_csv(trades_path, index=False)
        paths["trades"] = trades_path

    monthly = pd.DataFrame()
    if reporting.get("save_monthly_summary", True):
        monthly_path = run_dir / "monthly.csv"
        monthly = build_monthly_summary(trades)
        monthly.to_csv(monthly_path, index=False)
        paths["monthly"] = monthly_path

    daily = pd.DataFrame()
    if reporting.get("save_trades", True):
        daily_path = run_dir / "daily.csv"
        daily = build_daily_summary(trades)
        daily.to_csv(daily_path, index=False)
        paths["daily"] = daily_path

    if reporting.get("save_plot", True) and bt is not None:
        plot_path = run_dir / "plot.html"
        try:
            bt.plot(filename=str(plot_path), open_browser=False)
            paths["plot"] = plot_path
        except Exception as exc:
            print(f"Plot was not saved: {exc}")

    html_path = run_dir / "report.html"
    build_html_report(
        path=html_path,
        strategy_name=strategy_name,
        summary=summary,
        trades=trades,
        monthly=monthly,
        daily=daily,
        plot_path=paths.get("plot"),
        config=config,
    )
    paths["html_report"] = html_path

    return paths


def build_monthly_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "ExitTime" not in trades.columns:
        return pd.DataFrame(
            columns=[
                "Month",
                "Total Trades",
                "Winning Trades",
                "Losing Trades",
                "Gross Profit [pts]",
                "Gross Loss [pts]",
                "Net Profit [pts]",
                "Win Rate [%]",
                "Profit Factor",
            ]
        )

    trades = trades.copy()
    trades["ExitTime"] = pd.to_datetime(trades["ExitTime"], errors="coerce")
    trades = trades.dropna(subset=["ExitTime"])
    trades["Month"] = trades["ExitTime"].dt.to_period("M").astype(str)
    pnl_column = "PnL [pts]" if "PnL [pts]" in trades.columns else (
        "PnL" if "PnL" in trades.columns else "ReturnPct"
    )

    rows = []
    for month, group in trades.groupby("Month"):
        pnl = group[pnl_column]
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        gross_profit = wins.sum()
        gross_loss = losses.sum()
        profit_factor = gross_profit / abs(gross_loss) if gross_loss < 0 else float("inf")
        rows.append(
            {
                "Month": month,
                "Total Trades": len(group),
                "Winning Trades": len(wins),
                "Losing Trades": len(losses),
                "Gross Profit [pts]": round(gross_profit, 2),
                "Gross Loss [pts]": round(gross_loss, 2),
                "Net Profit [pts]": round(pnl.sum(), 2),
                "Win Rate [%]": round(len(wins) / len(group) * 100, 2) if len(group) else 0,
                "Profit Factor": round(profit_factor, 4),
            }
        )
    return pd.DataFrame(rows)


def build_daily_summary(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty or "ExitTime" not in trades.columns:
        return pd.DataFrame(
            columns=[
                "Date",
                "Total Trades",
                "Winning Trades",
                "Losing Trades",
                "Gross Profit [pts]",
                "Gross Loss [pts]",
                "Net Profit [pts]",
                "Win Rate [%]",
            ]
        )

    trades = trades.copy()
    trades["ExitTime"] = pd.to_datetime(trades["ExitTime"], errors="coerce")
    trades = trades.dropna(subset=["ExitTime"])
    trades["Date"] = trades["ExitTime"].dt.date.astype(str)
    pnl_column = "PnL [pts]" if "PnL [pts]" in trades.columns else (
        "PnL" if "PnL" in trades.columns else "ReturnPct"
    )

    rows = []
    for date, group in trades.groupby("Date"):
        pnl = group[pnl_column]
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]
        rows.append(
            {
                "Date": date,
                "Total Trades": len(group),
                "Winning Trades": len(wins),
                "Losing Trades": len(losses),
                "Gross Profit [pts]": round(wins.sum(), 2),
                "Gross Loss [pts]": round(losses.sum(), 2),
                "Net Profit [pts]": round(pnl.sum(), 2),
                "Win Rate [%]": round(len(wins) / len(group) * 100, 2) if len(group) else 0,
            }
        )
    return pd.DataFrame(rows)

_HTML_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
       background: #f4f6f9; color: #1a1a2e; font-size: 14px; }
header { background: #1a1a2e; color: #fff; padding: 24px 32px; }
header h1 { font-size: 22px; font-weight: 600; }
header p  { margin-top: 4px; color: #a0aec0; font-size: 13px; }
main { padding: 28px 32px; max-width: 1400px; margin: 0 auto; }

/* collapsible sections */
details { margin-top: 24px; }
details summary {
  list-style: none;
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
  font-size: 15px;
  font-weight: 600;
  color: #2d3748;
  padding: 10px 14px;
  background: #edf2f7;
  border-radius: 8px;
  border: 1px solid #cbd5e0;
  transition: background 0.15s;
}
details summary::-webkit-details-marker { display: none; }
details summary::before {
  content: '▶';
  font-size: 10px;
  color: #718096;
  transition: transform 0.2s;
  flex-shrink: 0;
}
details[open] summary::before { transform: rotate(90deg); }
details summary:hover { background: #e2e8f0; }
.section-body { padding-top: 14px; }

/* summary cards */
.cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 12px; }
.card { background: #fff; border-radius: 8px; padding: 16px 18px;
        box-shadow: 0 1px 4px rgba(0,0,0,.10); border: 1px solid #e2e8f0; }
.card .label { font-size: 11px; color: #718096; text-transform: uppercase;
               letter-spacing: .5px; margin-bottom: 8px; }
.card .value { font-size: 22px; font-weight: 700; }
.card.pos .value { color: #276749; }
.card.neg .value { color: #9b2c2c; }
.card.neu .value { color: #2b6cb0; }

/* tables */
.table-wrap { overflow-x: auto; background: #fff; border-radius: 8px;
              box-shadow: 0 1px 3px rgba(0,0,0,.08); }
table { width: 100%; border-collapse: collapse; }
thead th { background: #edf2f7; padding: 12px 16px; text-align: left;
           font-size: 12px; font-weight: 700; color: #2d3748;
           text-transform: uppercase; letter-spacing: .5px;
           border-bottom: 3px solid #cbd5e0; white-space: nowrap; }
thead th.sortable { cursor: pointer; user-select: none; }
thead th.sortable:hover { background: #e2e8f0; }
thead th.sortable::after { content: ' ⇅'; color: #a0aec0; font-size: 10px; }
thead th.sort-asc::after  { content: ' ▲'; color: #2b6cb0; font-size: 10px; }
thead th.sort-desc::after { content: ' ▼'; color: #2b6cb0; font-size: 10px; }
tbody td { padding: 11px 16px; border-bottom: 1px solid #e2e8f0;
           white-space: nowrap; font-size: 13px; }
tbody tr:nth-child(even) td { background: #f7fafc; }
tbody tr:nth-child(odd) td { background: #ffffff; }
tbody tr:last-child td { border-bottom: none; }
tbody tr:hover td { background: #ebf8ff !important; }
.win  { color: #276749; font-weight: 600; }
.loss { color: #9b2c2c; font-weight: 600; }
.tag-long  { background: #ebf8ff; color: #2b6cb0; padding: 2px 7px;
             border-radius: 4px; font-size: 11px; font-weight: 600; }
.tag-short { background: #fff5f5; color: #9b2c2c; padding: 2px 7px;
             border-radius: 4px; font-size: 11px; font-weight: 600; }
/* day-level row colouring */
tr.day-profit td { background: #f0fff4 !important; }
tr.day-loss   td { background: #fff5f5 !important; }
tr.day-profit td:first-child { border-left: 4px solid #38a169; }
tr.day-loss   td:first-child { border-left: 4px solid #e53e3e; }
tr.day-profit:hover td { background: #c6f6d5 !important; }
tr.day-loss:hover   td { background: #fed7d7 !important; }
.iframe-wrap { background:#fff; border-radius:8px;
               box-shadow:0 1px 3px rgba(0,0,0,.08); overflow:hidden; }
iframe { width:100%; height:540px; border:none; display:block; }
footer { text-align:center; color:#a0aec0; font-size:12px;
         padding:24px 0 32px; }
"""


def _fmt(value: object, decimals: int = 2) -> str:
    if isinstance(value, float):
        return f"{value:,.{decimals}f}"
    if isinstance(value, (int,)):
        return f"{value:,}"
    return str(value)


def _card_class(label: str, raw) -> str:
    positive_keys = {"Return [%]", "Win Rate [%]", "Profit Factor", "Sharpe Ratio",
                     "Sortino Ratio", "Calmar Ratio", "SQN", "Expectancy [%]",
                     "Return (Ann.) [%]", "Buy & Hold Return [%]",
                     "Net Profit [pts]", "Gross Profit [pts]", "Avg Trade [pts]",
                     "Best Trade [pts]", "Winning Trades"}
    negative_keys = {"Max. Drawdown [%]", "Avg. Drawdown [%]", "Gross Loss [pts]",
                     "Worst Trade [pts]", "Losing Trades"}
    if label in negative_keys:
        return "neg" if isinstance(raw, (int, float)) and float(raw) < 0 else "neu"
    if label in positive_keys:
        try:
            v = float(raw)
            return "pos" if v > 0 else ("neg" if v < 0 else "neu")
        except (TypeError, ValueError):
            return "neu"
    return "neu"


def _summary_cards_html(summary: dict) -> str:
    cards = []
    for label, raw in summary.items():
        cls = _card_class(label, raw)
        val = _fmt(raw, 4 if "Ratio" in label or label == "SQN" else 2)
        cards.append(
            f'<div class="card {cls}">'
            f'<div class="label">{label}</div>'
            f'<div class="value">{val}</div>'
            f"</div>"
        )
    return f'<div class="cards">{"".join(cards)}</div>'


def _monthly_table_html(monthly: pd.DataFrame) -> str:
    if monthly.empty:
        return "<p style='color:#718096'>No monthly data available.</p>"

    num_cols = {"Gross Profit [pts]", "Gross Loss [pts]", "Net Profit [pts]",
                "Gross Profit", "Gross Loss", "Net Profit",
                "Win Rate [%]", "Profit Factor", "Total Trades", "Winning Trades", "Losing Trades"}
    net_cols = {"Net Profit [pts]", "Net Profit"}

    headers = "".join(f"<th>{c}</th>" for c in monthly.columns)
    rows = []
    for _, row in monthly.iterrows():
        cells = []
        for col in monthly.columns:
            val = row[col]
            if col in net_cols:
                cls = "win" if float(val) > 0 else ("loss" if float(val) < 0 else "")
                cells.append(f'<td class="{cls}">{_fmt(val)}</td>')
            elif col in num_cols and col not in {"Total Trades", "Winning Trades", "Losing Trades"}:
                cells.append(f"<td>{_fmt(val)}</td>")
            else:
                cells.append(f"<td>{val}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<div class="table-wrap">'
        f"<table><thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _trades_table_html(trades: pd.DataFrame) -> str:
    if trades.empty:
        return "<p style='color:#718096'>No trades executed.</p>"

    display_cols = [c for c in
                    ["EntryTime", "ExitTime", "Duration", "Size",
                     "EntryPrice", "ExitPrice", "SL", "TP", "PnL [pts]", "PnL", "ReturnPct"]
                    if c in trades.columns]
    df = trades[display_cols].copy()

    pnl_col = (
        "PnL [pts]" if "PnL [pts]" in df.columns else
        "PnL" if "PnL" in df.columns else
        "ReturnPct" if "ReturnPct" in df.columns else None
    )
    size_col = "Size" if "Size" in df.columns else None

    headers = "".join(f"<th>{c}</th>" for c in df.columns)
    rows = []
    for _, row in df.iterrows():
        direction = None
        if size_col:
            direction = "Long" if float(row[size_col]) > 0 else "Short"

        cells = []
        for col in df.columns:
            val = row[col]
            if col == size_col and direction:
                tag_cls = "tag-long" if direction == "Long" else "tag-short"
                cells.append(f'<td><span class="{tag_cls}">{direction}</span></td>')
            elif col == pnl_col:
                try:
                    fv = float(val)
                    cls = "win" if fv > 0 else ("loss" if fv < 0 else "")
                    cells.append(f'<td class="{cls}">{_fmt(fv)}</td>')
                except (TypeError, ValueError):
                    cells.append(f"<td>{val}</td>")
            elif col in ("EntryPrice", "ExitPrice", "SL", "TP"):
                cells.append(f"<td>{_fmt(val)}</td>")
            elif col == "ReturnPct" and col != pnl_col:
                try:
                    fv = float(val)
                    cls = "win" if fv > 0 else ("loss" if fv < 0 else "")
                    cells.append(f'<td class="{cls}">{_fmt(fv, 4)}%</td>')
                except (TypeError, ValueError):
                    cells.append(f"<td>{val}</td>")
            else:
                cells.append(f"<td>{val}</td>")
        rows.append(f"<tr>{''.join(cells)}</tr>")

    return (
        '<div class="table-wrap">'
        f"<table><thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _daily_table_html(daily: pd.DataFrame) -> str:
    if daily.empty:
        return "<p style='color:#718096'>No daily data available.</p>"

    num_cols = {"Gross Profit [pts]", "Gross Loss [pts]", "Net Profit [pts]", "Win Rate [%]"}

    headers = "".join(f"<th>{c}</th>" for c in daily.columns)
    rows = []
    for _, row in daily.iterrows():
        net = row.get("Net Profit [pts]", 0)
        try:
            net_val = float(net)
        except (TypeError, ValueError):
            net_val = 0
        row_cls = "day-profit" if net_val > 0 else ("day-loss" if net_val < 0 else "")

        cells = []
        for col in daily.columns:
            val = row[col]
            if col == "Net Profit [pts]":
                cls = "win" if net_val > 0 else ("loss" if net_val < 0 else "")
                cells.append(f'<td class="{cls}"><strong>{_fmt(val)}</strong></td>')
            elif col in num_cols:
                cells.append(f"<td>{_fmt(val)}</td>")
            else:
                cells.append(f"<td>{val}</td>")
        rows.append(f'<tr class="{row_cls}">{"".join(cells)}</tr>')

    return (
        '<div class="table-wrap">'
        f"<table><thead><tr>{headers}</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _params_section_html(config: dict) -> str:
    """Render strategy parameters as a grid of pill badges above the summary cards."""
    if not config:
        return ""

    sp  = config.get("strategy_params", {})
    trd = config.get("trading", {})
    rsk = config.get("risk", {})
    sl  = rsk.get("stop_loss", {})
    tp  = rsk.get("take_profit", {})
    ts  = rsk.get("trailing_stop", {})

    groups = [
        ("Entry", [
            ("Buffer",        f"{sp.get('entry_buffer_points', 0)} pts"),
            ("Signal expiry", f"{sp.get('signal_expiry_candles', 20)} candles"),
            ("Allow long",    str(sp.get("allow_long", True))),
            ("Allow short",   str(sp.get("allow_short", True))),
        ]),
        ("Session", [
            ("Start",              trd.get("trading_start_time", "—")),
            ("End",                trd.get("trading_end_time", "—")),
            ("Square-off",         trd.get("square_off_time", "—")),
            ("Max trades/day",     str(trd.get("max_trades_per_day", "—"))),
            ("Stop after profit",  str(trd.get("stop_after_first_profit", False))),
        ]),
        ("Stop Loss", [
            ("Method",  sl.get("method", "—")),
            ("Value",   f"{sl.get('value', '—')} pts"),
        ]),
        ("Take Profit", [
            ("Method",  tp.get("method", "none")),
            ("Value",   f"{tp.get('value', '—')} pts" if tp.get("value") else "—"),
        ]),
    ]

    if ts.get("enabled"):
        ts_method = ts.get("method", "—")
        ts_rows = [("Method", ts_method)]
        if ts_method in ("activation_lock", "activation_lock_step"):
            ts_rows += [
                ("Activation", f"{ts.get('activation_points', '—')} pts"),
                ("Lock",       f"{ts.get('lock_points', '—')} pts"),
            ]
            if ts_method == "activation_lock_step":
                ts_rows.append(("Step", f"{ts.get('step_points', '—')} pts"))
        elif ts_method == "fixed_points":
            ts_rows.append(("Trail", f"{ts.get('value', '—')} pts"))
        groups.append(("Trailing Stop", ts_rows))

    css = """
<style>
.params-grid { display: flex; flex-wrap: wrap; gap: 16px; margin-top: 4px; }
.param-group { background: #fff; border: 1px solid #e2e8f0; border-radius: 8px;
               padding: 14px 18px; min-width: 180px; flex: 1 1 180px; }
.param-group h4 { font-size: 11px; font-weight: 700; color: #718096;
                  text-transform: uppercase; letter-spacing: .6px; margin-bottom: 10px; }
.param-row { display: flex; justify-content: space-between; align-items: center;
             gap: 12px; padding: 3px 0; border-bottom: 1px solid #f0f4f8; }
.param-row:last-child { border-bottom: none; }
.param-key   { font-size: 12px; color: #4a5568; }
.param-value { font-size: 12px; font-weight: 600; color: #1a202c;
               background: #edf2f7; border-radius: 4px; padding: 1px 7px; }
</style>"""

    group_html = []
    for title, rows in groups:
        items = "".join(
            f'<div class="param-row">'
            f'<span class="param-key">{k}</span>'
            f'<span class="param-value">{v}</span>'
            f"</div>"
            for k, v in rows
        )
        group_html.append(
            f'<div class="param-group"><h4>{title}</h4>{items}</div>'
        )

    return css + f'<div class="params-grid">{"".join(group_html)}</div>'


def build_html_report(
    path: Path,
    strategy_name: str,
    summary: dict,
    trades: pd.DataFrame,
    monthly: pd.DataFrame,
    daily: pd.DataFrame = None,
    plot_path: Path | None = None,
    config: dict | None = None,
) -> None:
    if daily is None:
        daily = pd.DataFrame()
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    start = summary.get("Start", "")
    end = summary.get("End", "")
    period = f"{start} → {end}" if start and end else ""

    params_html = _params_section_html(config or {})

    equity_curve_section = ""
    if plot_path and Path(plot_path).exists():
        rel = Path(plot_path).name
        equity_curve_section = f"""
  <details open>
    <summary>Equity Curve</summary>
    <div class="section-body">
      <div class="iframe-wrap"><iframe src="{rel}"></iframe></div>
    </div>
  </details>"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{strategy_name} — Backtest Report</title>
<style>{_HTML_CSS}</style>
</head>
<body>
<header>
  <h1>{strategy_name.replace("_", " ").title()} — Backtest Report</h1>
  <p>{period} &nbsp;·&nbsp; Generated {generated_at}</p>
</header>
<main>
  <details open>
    <summary>Parameters</summary>
    <div class="section-body">{params_html}</div>
  </details>
  <details open>
    <summary>Summary</summary>
    <div class="section-body">{_summary_cards_html(summary)}</div>
  </details>
  {equity_curve_section}
  <details open>
    <summary>Monthly Performance</summary>
    <div class="section-body">{_monthly_table_html(monthly)}</div>
  </details>
  <details open>
    <summary>Daily Performance ({len(daily)} trading days)</summary>
    <div class="section-body">{_daily_table_html(daily)}</div>
  </details>
  <details open>
    <summary>Trade Log ({len(trades)} trades)</summary>
    <div class="section-body">{_trades_table_html(trades)}</div>
  </details>
</main>
<footer>Generated by back-testing · {generated_at}</footer>
<script>
(function() {{
  function parseCell(td) {{
    var v = td.getAttribute('data-val') || td.innerText.trim().replace(/,/g,'');
    var n = parseFloat(v);
    return isNaN(n) ? v.toLowerCase() : n;
  }}
  function sortTable(table, col, asc) {{
    var tbody = table.querySelector('tbody');
    var rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort(function(a, b) {{
      var va = parseCell(a.cells[col]);
      var vb = parseCell(b.cells[col]);
      if (va < vb) return asc ? -1 : 1;
      if (va > vb) return asc ?  1 : -1;
      return 0;
    }});
    rows.forEach(function(r) {{ tbody.appendChild(r); }});
  }}
  document.querySelectorAll('table').forEach(function(table) {{
    var headers = table.querySelectorAll('thead th');
    headers.forEach(function(th, i) {{
      th.classList.add('sortable');
      th._sortAsc = true;
      th.addEventListener('click', function() {{
        headers.forEach(function(h) {{
          h.classList.remove('sort-asc','sort-desc');
        }});
        sortTable(table, i, th._sortAsc);
        th.classList.add(th._sortAsc ? 'sort-asc' : 'sort-desc');
        th._sortAsc = !th._sortAsc;
      }});
    }});
  }});
}})();
</script>
</body>
</html>"""

    path.write_text(html, encoding="utf-8")
