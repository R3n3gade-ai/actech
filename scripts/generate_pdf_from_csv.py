import argparse
import importlib.util
import os
import sys
from datetime import datetime

import pandas as pd


NYSE_HOLIDAYS_2025 = {
    "2025-01-01",  # New Year's Day
    "2025-01-09",  # National Day of Mourning
    "2025-01-20",  # Martin Luther King Jr. Day
    "2025-02-17",  # Washington's Birthday
    "2025-04-18",  # Good Friday
    "2025-05-26",  # Memorial Day
    "2025-06-19",  # Juneteenth
    "2025-07-04",  # Independence Day
    "2025-09-01",  # Labor Day
    "2025-11-27",  # Thanksgiving Day
    "2025-12-25",  # Christmas Day
}


def nyse_trading_days(start_date: str, end_date: str) -> pd.DatetimeIndex:
    dates = pd.bdate_range(start=start_date, end=end_date)
    holidays = pd.to_datetime(sorted(NYSE_HOLIDAYS_2025))
    return dates.difference(holidays)


def load_history(path: str, start_date: str, end_date: str) -> pd.DataFrame:
    history = pd.read_csv(path)

    date_columns = [c for c in history.columns if c.lower() in {"date", "datetime"}]
    if date_columns:
        date_col = date_columns[0]
        history[date_col] = pd.to_datetime(history[date_col])
        history = history.set_index(date_col)
    else:
        trading_days = nyse_trading_days(start_date, end_date)
        if len(trading_days) == len(history) + 1:
            trading_days = trading_days[:-1]
        if len(trading_days) != len(history):
            raise RuntimeError(
                f"Cannot assign trading-day index: {len(trading_days)} dates for {len(history)} rows"
            )
        history.index = trading_days

    history.index.name = "Date"
    return history


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate ARMS PDF report from saved CSV outputs.")
    parser.add_argument("--start", default="2025-01-02")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--capital", type=float, default=500_000_000)
    parser.add_argument("--output-dir", default="SAMPLES")
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    tag = f"{args.start}_{args.end}"
    history_path = os.path.join(repo_root, args.output_dir, f"history_{tag}.csv")
    ledger_path = os.path.join(repo_root, args.output_dir, f"trade_ledger_{tag}.csv")

    history = load_history(history_path, args.start, args.end)
    trade_ledger = pd.read_csv(ledger_path).to_dict("records") if os.path.exists(ledger_path) else []

    # Load pdf_report.py directly to avoid simulation/__init__.py, which imports
    # replay_harness and the intelligence layer (SEC EDGAR, PFVT, etc.) at import time.
    pdf_report_path = os.path.join(repo_root, "src", "simulation", "pdf_report.py")
    spec = importlib.util.spec_from_file_location("arms_pdf_report", pdf_report_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    generate_pdf_report = module.generate_pdf_report

    pdf_path = generate_pdf_report(
        history=history,
        trade_ledger=trade_ledger,
        start_date=args.start,
        end_date=args.end,
        initial_capital=args.capital,
        output_dir=os.path.join(repo_root, args.output_dir),
    )

    final_nav = float(history["NAV"].iloc[-1])
    total_return = (final_nav / args.capital - 1) * 100
    print(f"Generated: {pdf_path}")
    print(f"Rows: {len(history)} | Final NAV: ${final_nav:,.0f} | Total return: {total_return:+.2f}%")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())