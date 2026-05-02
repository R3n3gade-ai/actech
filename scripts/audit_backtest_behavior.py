"""Audit saved ARMS backtest behavior for underperformance and whipsaw.

Reads saved SAMPLES/history_* and trade_ledger_* CSV outputs. Does not rerun
market data or modify any state. Uses only the standard library so it remains
usable when scientific packages are slow to import.
"""
from __future__ import annotations

import csv
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

TARGET_MONTHS = [
    "2024-04",
    "2024-06",
    "2024-07",
    "2024-08",
    "2025-02",
    "2025-04",
    "2025-05",
    "2025-11",
]

NUMERIC_HISTORY_COLUMNS = {
    "NAV",
    "SPY_Price",
    "QQQ_Price",
    "BTC_Price",
    "Equity_Pct",
    "Crypto_Pct",
    "Cash_Pct",
    "Drawdown",
}
NUMERIC_TRADE_COLUMNS = {"Value", "Price", "Shares", "Slippage"}


def parse_float(value: str | None) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def pct(value: float) -> str:
    return f"{value:+.2%}"


def money_m(value: float) -> str:
    return f"${value / 1_000_000:,.1f}M"


def parse_csv(path: Path, numeric_columns: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for raw in reader:
            row: dict[str, Any] = dict(raw)
            row["Date"] = date.fromisoformat(str(raw["Date"]))
            row["Month"] = row["Date"].strftime("%Y-%m")
            for column in numeric_columns:
                if column in row:
                    row[column] = parse_float(raw.get(column))
            rows.append(row)
    return sorted(rows, key=lambda item: item["Date"])


def load_outputs(tag: str = "2024-01-01_2025-12-31") -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sample_dir = Path("SAMPLES")
    history = parse_csv(sample_dir / f"history_{tag}.csv", NUMERIC_HISTORY_COLUMNS)
    trades = parse_csv(sample_dir / f"trade_ledger_{tag}.csv", NUMERIC_TRADE_COLUMNS)
    return history, trades


def average(rows: list[dict[str, Any]], column: str) -> float:
    return sum(float(row[column]) for row in rows) / len(rows)


def monthly_summary(history: list[dict[str, Any]], trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    history_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        history_by_month[str(row["Month"])].append(row)
    for row in trades:
        trades_by_month[str(row["Month"])].append(row)

    rows: list[dict[str, Any]] = []
    previous_nav = 500_000_000.0
    previous_spy = float(history[0]["SPY_Price"])
    previous_qqq = float(history[0]["QQQ_Price"])
    previous_btc = float(history[0]["BTC_Price"])

    for month in sorted(history_by_month):
        month_history = history_by_month[month]
        month_trades = trades_by_month[month]
        last = month_history[-1]
        nav_return = float(last["NAV"]) / previous_nav - 1.0
        spy_return = float(last["SPY_Price"]) / previous_spy - 1.0
        qqq_return = float(last["QQQ_Price"]) / previous_qqq - 1.0
        btc_return = float(last["BTC_Price"]) / previous_btc - 1.0
        rows.append(
            {
                "Month": month,
                "ARMS": nav_return,
                "SPY": spy_return,
                "QQQ": qqq_return,
                "BTC": btc_return,
                "ARMS_vs_QQQ": nav_return - qqq_return,
                "ARMS_vs_SPY": nav_return - spy_return,
                "Trades": len(month_trades),
                "Turnover": sum(float(row.get("Value", 0.0)) for row in month_trades),
                "AvgEq": average(month_history, "Equity_Pct"),
                "AvgCrypto": average(month_history, "Crypto_Pct"),
                "AvgCash": average(month_history, "Cash_Pct"),
                "MinDD": min(float(row["Drawdown"]) for row in month_history),
                "PDS_states": dict(Counter(str(row["PDS_Status"]) for row in month_history)),
                "Regimes": dict(Counter(str(row["Regime"]) for row in month_history)),
                "Modules": dict(Counter(str(row["Module"]) for row in month_trades)),
            }
        )
        previous_nav = float(last["NAV"])
        previous_spy = float(last["SPY_Price"])
        previous_qqq = float(last["QQQ_Price"])
        previous_btc = float(last["BTC_Price"])
    return rows


def pds_transitions(history: list[dict[str, Any]]) -> list[tuple[date, str, str, float, float, float]]:
    transitions = []
    previous = None
    for row in history:
        status = str(row["PDS_Status"])
        if previous is not None and status != previous:
            transitions.append(
                (
                    row["Date"],
                    previous,
                    status,
                    float(row["Drawdown"]),
                    float(row["NAV"]),
                    float(row["Equity_Pct"]),
                )
            )
        previous = status
    return transitions


def estimate_round_trip_cost(trades: list[dict[str, Any]], days: int = 14) -> tuple[int, float]:
    excluded = {"BTC", "SGOV", "SGOL", "DBMF", "STRC", "TLT", "SLOF"}
    equity_trades = [row for row in trades if str(row["Ticker"]) not in excluded]
    sells = [row for row in equity_trades if row["Action"] == "SELL"]
    buys = [row for row in equity_trades if row["Action"] == "BUY"]
    total_cost = 0.0
    count = 0
    for sell in sells:
        sell_date = sell["Date"]
        matched_buys = [
            buy
            for buy in buys
            if buy["Ticker"] == sell["Ticker"]
            and sell_date < buy["Date"] <= sell_date + timedelta(days=days)
        ]
        if not matched_buys:
            continue
        buy = matched_buys[0]
        shares = min(float(sell["Shares"]), float(buy["Shares"]))
        cost = max(0.0, (float(buy["Price"]) - float(sell["Price"])) * shares)
        if cost > 0:
            total_cost += cost
            count += 1
    return count, total_cost


def print_month_details(summary: list[dict[str, Any]]) -> None:
    by_month = {row["Month"]: row for row in summary}
    print("TARGET MONTHS")
    for month in TARGET_MONTHS:
        row = by_month[month]
        print(
            f"\n{month}: ARMS {pct(row['ARMS'])} | SPY {pct(row['SPY'])} | "
            f"QQQ {pct(row['QQQ'])} | BTC {pct(row['BTC'])} | "
            f"vs QQQ {pct(row['ARMS_vs_QQQ'])} | vs SPY {pct(row['ARMS_vs_SPY'])}"
        )
        print(
            f"  avg eq {row['AvgEq']:.1%}, crypto {row['AvgCrypto']:.1%}, "
            f"cash {row['AvgCash']:.1%}, minDD {row['MinDD']:.2%}, "
            f"trades {int(row['Trades'])}, turnover {money_m(row['Turnover'])}"
        )
        print(f"  PDS {row['PDS_states']} | Regime {row['Regimes']}")
        print(f"  modules {row['Modules']}")


def main() -> int:
    history, trades = load_outputs()
    summary = monthly_summary(history, trades)
    print_month_details(summary)

    print("\nWORST ARMS VS QQQ MONTHS")
    for row in sorted(summary, key=lambda item: item["ARMS_vs_QQQ"])[:10]:
        print(
            f"{row['Month']}: ARMS {pct(row['ARMS'])}, QQQ {pct(row['QQQ'])}, "
            f"gap {pct(row['ARMS_vs_QQQ'])}, eq {row['AvgEq']:.0%}, "
            f"crypto {row['AvgCrypto']:.0%}, trades {int(row['Trades'])}, "
            f"PDS {row['PDS_states']}, Regime {row['Regimes']}"
        )

    transitions = pds_transitions(history)
    print("\nPDS TRANSITIONS")
    for transition_date, old, new, drawdown, nav, equity_pct in transitions:
        print(
            f"{transition_date}: {old}->{new} DD={drawdown:.2%} "
            f"NAV={money_m(nav)} eq={equity_pct:.1%}"
        )
    print(f"total transitions {len(transitions)}")

    print("\nPDS FLIP CLUSTERS <=10 CALENDAR DAYS")
    for index in range(1, len(transitions)):
        date0, old0, new0, dd0, _, eq0 = transitions[index - 1]
        date1, old1, new1, dd1, _, eq1 = transitions[index]
        days_between = (date1 - date0).days
        if days_between <= 10:
            print(
                f"{date0} {old0}->{new0} then {date1} {old1}->{new1} "
                f"({days_between}d) DD {dd0:.2%}->{dd1:.2%} "
                f"eq {eq0:.1%}->{eq1:.1%}"
            )

    count, cost = estimate_round_trip_cost(trades)
    print("\nROUND TRIP ESTIMATE <=14D")
    print(f"sell->buy higher count={count}, approx cost={money_m(cost)}")

    print("\nTRADE COUNTS BY MODULE")
    for module, count in Counter(str(row["Module"]) for row in trades).most_common():
        print(f"{module} {count}")

    print("\nTRADE COUNTS BY ACTION")
    for action, count in Counter(str(row["Action"]) for row in trades).most_common():
        print(f"{action} {count}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
