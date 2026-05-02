"""Build an updated ARMS portfolio report from saved replay CSVs.

This intentionally avoids pandas and network calls so the report can be
regenerated even when the heavier reporting path is slow or blocked.
"""
from __future__ import annotations

import argparse
import csv
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean
from typing import Any

from fpdf import FPDF


INITIAL_CAPITAL_DEFAULT = 500_000_000.0
EXCLUDED_TRADE_TICKERS = {"BTC", "SGOV", "SGOL", "DBMF", "STRC", "TLT", "SLOF"}


MONTH_NAMES = {
    "01": "Jan", "02": "Feb", "03": "Mar", "04": "Apr", "05": "May", "06": "Jun",
    "07": "Jul", "08": "Aug", "09": "Sep", "10": "Oct", "11": "Nov", "12": "Dec",
}


class ReportPDF(FPDF):
    def header(self) -> None:
        if self.page_no() > 1:
            self.set_font("Helvetica", "", 7)
            self.cell(0, 5, clean("ACHELION CAPITAL | ARMS Portfolio Report | CONFIDENTIAL"), align="C", ln=True)
            self.ln(1)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "", 7)
        self.cell(0, 6, clean(f"Page {self.page_no()}"), align="C")


def clean(value: object) -> str:
    text = str(value)
    replacements = {
        "\u2192": "->",
        "\u2014": "-",
        "\u2013": "-",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2265": ">=",
        "\u2264": "<=",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def fnum(row: dict[str, Any], key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key, "")
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def money(value: float) -> str:
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1_000_000_000:
        return f"{sign}${value / 1_000_000_000:,.2f}B"
    if value >= 1_000_000:
        return f"{sign}${value / 1_000_000:,.1f}M"
    return f"{sign}${value:,.0f}"


def pct(value: float) -> str:
    return f"{value:+.2f}%"


def read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["Date"] = row.get("Date", "")[:10]
        row["Month"] = row["Date"][:7]
    return rows


def daily_returns(history: list[dict[str, Any]]) -> list[float]:
    navs = [fnum(row, "NAV") for row in history]
    returns: list[float] = []
    for index in range(1, len(navs)):
        if navs[index - 1] > 0:
            returns.append(navs[index] / navs[index - 1] - 1.0)
    return returns


def max_drawdown(history: list[dict[str, Any]]) -> float:
    return min(fnum(row, "Drawdown") for row in history) * 100.0


def period_return(history: list[dict[str, Any]], column: str) -> float:
    first = fnum(history[0], column)
    last = fnum(history[-1], column)
    if first <= 0:
        return 0.0
    return (last / first - 1.0) * 100.0


def nav_return(history: list[dict[str, Any]], initial_capital: float) -> float:
    return (fnum(history[-1], "NAV") / initial_capital - 1.0) * 100.0


def annualized_return(total_return_pct: float, trading_days: int) -> float:
    years = trading_days / 252.0
    if years <= 0:
        return 0.0
    return ((1.0 + total_return_pct / 100.0) ** (1.0 / years) - 1.0) * 100.0


def sharpe_and_vol(returns: list[float]) -> tuple[float, float]:
    if len(returns) < 2:
        return 0.0, 0.0
    avg = mean(returns)
    variance = sum((item - avg) ** 2 for item in returns) / (len(returns) - 1)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0, 0.0
    return (avg / std) * math.sqrt(252.0), std * math.sqrt(252.0) * 100.0


def grouped_by_month(history: list[dict[str, Any]], trades: list[dict[str, Any]], initial_capital: float) -> list[dict[str, Any]]:
    history_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    trades_by_month: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        history_by_month[row["Month"]].append(row)
    for row in trades:
        trades_by_month[row["Month"]].append(row)

    previous_nav = initial_capital
    previous_spy = fnum(history[0], "SPY_Price")
    previous_qqq = fnum(history[0], "QQQ_Price")
    previous_btc = fnum(history[0], "BTC_Price")
    previous_ptrh = 0.0
    previous_slip = 0.0
    previous_perm = 0.0
    months: list[dict[str, Any]] = []

    for month in sorted(history_by_month):
        rows = history_by_month[month]
        month_trades = trades_by_month.get(month, [])
        last = rows[-1]
        nav = fnum(last, "NAV")
        spy = fnum(last, "SPY_Price")
        qqq = fnum(last, "QQQ_Price")
        btc = fnum(last, "BTC_Price")
        ptrh = fnum(last, "PTRH_Net_Cost")
        slip = fnum(last, "Total_Slippage")
        perm = fnum(last, "PERM_Income")
        modules = Counter(row.get("Module", "UNKNOWN") for row in month_trades)
        actions = Counter(row.get("Action", "UNKNOWN") for row in month_trades)
        months.append({
            "month": month,
            "label": f"{MONTH_NAMES.get(month[5:7], month[5:7])} {month[:4]}",
            "nav_start": previous_nav,
            "nav_end": nav,
            "arms": (nav / previous_nav - 1.0) * 100.0 if previous_nav > 0 else 0.0,
            "spy": (spy / previous_spy - 1.0) * 100.0 if previous_spy > 0 else 0.0,
            "qqq": (qqq / previous_qqq - 1.0) * 100.0 if previous_qqq > 0 else 0.0,
            "btc": (btc / previous_btc - 1.0) * 100.0 if previous_btc > 0 else 0.0,
            "avg_eq": mean(fnum(row, "Equity_Pct") for row in rows) * 100.0,
            "avg_crypto": mean(fnum(row, "Crypto_Pct") for row in rows) * 100.0,
            "avg_def": mean(fnum(row, "Defensive_Pct") for row in rows) * 100.0,
            "avg_cash": mean(fnum(row, "Cash_Pct") for row in rows) * 100.0,
            "min_dd": min(fnum(row, "Drawdown") for row in rows) * 100.0,
            "trades": len(month_trades),
            "turnover": sum(fnum(row, "Value") for row in month_trades),
            "pds": Counter(row.get("PDS_Status", "") for row in rows),
            "regime": Counter(row.get("Regime", "") for row in rows),
            "modules": modules,
            "actions": actions,
            "ptrh_cost": ptrh - previous_ptrh,
            "slippage": slip - previous_slip,
            "perm_income": perm - previous_perm,
        })
        previous_nav, previous_spy, previous_qqq, previous_btc = nav, spy, qqq, btc
        previous_ptrh, previous_slip, previous_perm = ptrh, slip, perm
    return months


def pds_transitions(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    transitions: list[dict[str, Any]] = []
    previous = None
    for row in history:
        status = row.get("PDS_Status", "")
        if previous is not None and status != previous:
            transitions.append({
                "date": row["Date"],
                "from": previous,
                "to": status,
                "drawdown": fnum(row, "Drawdown") * 100.0,
                "nav": fnum(row, "NAV"),
                "equity": fnum(row, "Equity_Pct") * 100.0,
            })
        previous = status
    return transitions


def ticker_stats(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in trades:
        ticker = row.get("Ticker", "") or "UNKNOWN"
        by_ticker[ticker].append(row)
    stats = []
    for ticker, rows in by_ticker.items():
        stats.append({
            "ticker": ticker,
            "trades": len(rows),
            "buy_count": sum(1 for row in rows if row.get("Action") == "BUY"),
            "sell_count": sum(1 for row in rows if row.get("Action") == "SELL"),
            "value": sum(fnum(row, "Value") for row in rows),
            "slippage": sum(fnum(row, "Slippage") for row in rows),
            "modules": Counter(row.get("Module", "") for row in rows),
        })
    return sorted(stats, key=lambda item: item["value"], reverse=True)


def estimate_round_trip_cost(trades: list[dict[str, Any]], days: int = 14) -> tuple[int, float]:
    date_format = "%Y-%m-%d"
    equity_trades = [row for row in trades if row.get("Ticker", "") not in EXCLUDED_TRADE_TICKERS]
    sells = [row for row in equity_trades if row.get("Action") == "SELL"]
    buys = [row for row in equity_trades if row.get("Action") == "BUY"]
    count = 0
    cost = 0.0
    for sell in sells:
        try:
            sell_date = datetime.strptime(sell["Date"], date_format).date()
        except Exception:
            continue
        candidates = []
        for buy in buys:
            if buy.get("Ticker") != sell.get("Ticker"):
                continue
            try:
                buy_date = datetime.strptime(buy["Date"], date_format).date()
            except Exception:
                continue
            delta = (buy_date - sell_date).days
            if 0 < delta <= days:
                candidates.append(buy)
        if not candidates:
            continue
        buy = candidates[0]
        shares = min(fnum(sell, "Shares"), fnum(buy, "Shares"))
        extra = max(0.0, (fnum(buy, "Price") - fnum(sell, "Price")) * shares)
        if extra > 0:
            count += 1
            cost += extra
    return count, cost


def draw_table(pdf: FPDF, headers: list[str], rows: list[list[object]], widths: list[float], font_size: int = 7, row_h: float = 5.0) -> None:
    pdf.set_font("Helvetica", "B", font_size)
    pdf.set_fill_color(235, 238, 242)
    for header, width in zip(headers, widths):
        pdf.cell(width, row_h + 1, clean(header), border=1, align="C", fill=True)
    pdf.ln()
    pdf.set_font("Helvetica", "", font_size)
    for row in rows:
        if pdf.get_y() > 270:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", font_size)
            for header, width in zip(headers, widths):
                pdf.cell(width, row_h + 1, clean(header), border=1, align="C", fill=True)
            pdf.ln()
            pdf.set_font("Helvetica", "", font_size)
        for value, width in zip(row, widths):
            pdf.cell(width, row_h, clean(value), border=1, align="C")
        pdf.ln()


def section_title(pdf: FPDF, title: str) -> None:
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, clean(title), ln=True)
    pdf.set_draw_color(85, 95, 110)
    pdf.line(pdf.get_x(), pdf.get_y(), 200, pdf.get_y())
    pdf.ln(3)


def paragraph(pdf: FPDF, text: str, size: int = 8, height: float = 4.7) -> None:
    pdf.set_font("Helvetica", "", size)
    pdf.set_x(pdf.l_margin)
    width = pdf.w - pdf.l_margin - pdf.r_margin
    pdf.multi_cell(width, height, clean(text))


def normalize_series(values: list[float], base: float = 100.0) -> list[float]:
    if not values or values[0] == 0:
        return []
    first = values[0]
    return [(value / first) * base for value in values]


def draw_line_chart(pdf: FPDF, title: str, series: list[tuple[str, list[float], tuple[int, int, int]]], x: float, y: float, w: float, h: float) -> None:
    all_values = [value for _, values, _ in series for value in values if value is not None]
    if not all_values:
        return
    min_v = min(all_values)
    max_v = max(all_values)
    if max_v == min_v:
        max_v += 1.0
        min_v -= 1.0
    pad = (max_v - min_v) * 0.08
    min_v -= pad
    max_v += pad

    pdf.set_font("Helvetica", "B", 9)
    pdf.text(x, y - 2, clean(title))
    pdf.set_draw_color(190, 195, 205)
    pdf.rect(x, y, w, h)
    for grid in range(1, 4):
        gy = y + h * grid / 4.0
        pdf.line(x, gy, x + w, gy)

    for label, values, color in series:
        if len(values) < 2:
            continue
        pdf.set_draw_color(*color)
        points: list[tuple[float, float]] = []
        for index, value in enumerate(values):
            px = x + (index / (len(values) - 1)) * w
            py = y + h - ((value - min_v) / (max_v - min_v)) * h
            points.append((px, py))
        for index in range(1, len(points)):
            pdf.line(points[index - 1][0], points[index - 1][1], points[index][0], points[index][1])

    legend_x = x + 2
    legend_y = y + h + 5
    pdf.set_font("Helvetica", "", 7)
    for label, _, color in series:
        pdf.set_fill_color(*color)
        pdf.rect(legend_x, legend_y - 3, 3, 3, style="F")
        pdf.text(legend_x + 4, legend_y, clean(label))
        legend_x += 28


def draw_bar_chart(pdf: FPDF, title: str, labels: list[str], values: list[float], x: float, y: float, w: float, h: float) -> None:
    if not values:
        return
    max_abs = max(abs(value) for value in values) or 1.0
    zero_y = y + h / 2.0
    pdf.set_font("Helvetica", "B", 9)
    pdf.text(x, y - 2, clean(title))
    pdf.set_draw_color(190, 195, 205)
    pdf.rect(x, y, w, h)
    pdf.line(x, zero_y, x + w, zero_y)
    bar_gap = 1.0
    bar_w = max(1.5, (w - bar_gap * (len(values) + 1)) / len(values))
    for index, value in enumerate(values):
        bx = x + bar_gap + index * (bar_w + bar_gap)
        bh = abs(value) / max_abs * (h / 2.0 - 2)
        if value >= 0:
            by = zero_y - bh
            pdf.set_fill_color(82, 145, 93)
        else:
            by = zero_y
            pdf.set_fill_color(194, 83, 68)
        pdf.rect(bx, by, bar_w, bh, style="F")
    pdf.set_font("Helvetica", "", 6)
    for index, label in enumerate(labels):
        if index % 2 == 0:
            bx = x + bar_gap + index * (bar_w + bar_gap)
            pdf.text(bx, y + h + 4, clean(label[5:]))


def build_report(start: str, end: str, capital: float, sample_dir: Path, output_path: Path) -> Path:
    tag = f"{start}_{end}"
    history = read_csv(sample_dir / f"history_{tag}.csv")
    trades = read_csv(sample_dir / f"trade_ledger_{tag}.csv")
    months = grouped_by_month(history, trades, capital)
    transitions = pds_transitions(history)
    ticker_rows = ticker_stats(trades)
    rt_count, rt_cost = estimate_round_trip_cost(trades)

    returns = daily_returns(history)
    sharpe, ann_vol = sharpe_and_vol(returns)
    total_return = nav_return(history, capital)
    ann_return = annualized_return(total_return, len(history))
    final_nav = fnum(history[-1], "NAV")
    spy_total = period_return(history, "SPY_Price")
    qqq_total = period_return(history, "QQQ_Price")
    btc_total = period_return(history, "BTC_Price")
    max_dd = max_drawdown(history)
    ptrh_cost = fnum(history[-1], "PTRH_Net_Cost")
    total_slippage = fnum(history[-1], "Total_Slippage")
    perm_income = fnum(history[-1], "PERM_Income")

    pdf = ReportPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 12, "ACHELION CAPITAL", align="C", ln=True)
    pdf.set_font("Helvetica", "B", 15)
    pdf.cell(0, 8, "ARMS Portfolio Report - Updated PDS-Hardened Replay", align="C", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, clean(f"Period: {start} to {end} | Capital: {money(capital)} | Generated: {datetime.now():%Y-%m-%d %H:%M}"), align="C", ln=True)
    pdf.cell(0, 6, clean("Source: saved post-fix replay CSVs; no network calls; no full rerun during report generation"), align="C", ln=True)
    pdf.ln(8)

    section_title(pdf, "Executive Snapshot")
    draw_table(pdf, ["Metric", "Value", "Metric", "Value"], [
        ["Final NAV", money(final_nav), "Total Return", pct(total_return)],
        ["Annualized Return", pct(ann_return), "Sharpe", f"{sharpe:.2f}"],
        ["Max Drawdown", pct(max_dd), "Annualized Vol", f"{ann_vol:.2f}%"],
        ["SPY Return", pct(spy_total), "QQQ Return", pct(qqq_total)],
        ["BTC Return", pct(btc_total), "Total Trades", str(len(trades))],
        ["PTRH Net Cost", money(ptrh_cost), "LAEP Slippage", money(total_slippage)],
        ["PERM Income", money(perm_income), "PDS Transitions", str(len(transitions))],
        ["14d Sell/Buy-Higher Count", str(rt_count), "Est. Round-Trip Cost", money(rt_cost)],
    ], [45, 45, 45, 45], 8)
    pdf.ln(5)
    paragraph(pdf, "PDS hardening status: the replay harness now treats PDS WARNING as advisory-only for trade-trigger purposes, matching the briefing language that WARNING has no mandatory action. DELEVERAGE_1 and DELEVERAGE_2 remain mandatory ceiling changes. The saved output still records WARNING state transitions for audit visibility, but WARNING alone no longer qualifies as a PDS rebalance trigger.")

    pdf.add_page()
    section_title(pdf, "Top-Level Charts")
    nav_series = normalize_series([fnum(row, "NAV") for row in history])
    spy_series = normalize_series([fnum(row, "SPY_Price") for row in history])
    qqq_series = normalize_series([fnum(row, "QQQ_Price") for row in history])
    btc_series = normalize_series([fnum(row, "BTC_Price") for row in history])
    draw_line_chart(pdf, "Growth of $100", [
        ("ARMS", nav_series, (42, 91, 150)),
        ("SPY", spy_series, (80, 150, 90)),
        ("QQQ", qqq_series, (180, 115, 40)),
        ("BTC", btc_series, (150, 80, 150)),
    ], 15, 35, 180, 70)
    dd_series = [fnum(row, "Drawdown") * 100.0 for row in history]
    draw_line_chart(pdf, "ARMS Drawdown", [("Drawdown", dd_series, (195, 70, 60))], 15, 130, 180, 55)
    draw_bar_chart(pdf, "Monthly ARMS Returns", [m["month"] for m in months], [m["arms"] for m in months], 15, 210, 180, 45)

    pdf.add_page()
    section_title(pdf, "Ticker And Trade Statistics")
    ticker_table = []
    for item in ticker_rows[:30]:
        modules = ", ".join(f"{name}:{count}" for name, count in item["modules"].most_common(3))
        ticker_table.append([
            item["ticker"], item["trades"], item["buy_count"], item["sell_count"],
            money(item["value"]), money(item["slippage"]), modules,
        ])
    draw_table(pdf, ["Ticker", "Trades", "Buys", "Sells", "Trade Value", "Slippage", "Top Modules"], ticker_table, [20, 18, 16, 16, 28, 25, 67], 7)

    pdf.add_page()
    section_title(pdf, "Monthly Return Table")
    month_rows = []
    for item in months:
        month_rows.append([
            item["label"], pct(item["arms"]), pct(item["spy"]), pct(item["qqq"]), pct(item["btc"]),
            f"{item['avg_eq']:.0f}%", f"{item['avg_crypto']:.0f}%", f"{item['avg_cash']:.0f}%",
            pct(item["min_dd"]), item["trades"], money(item["turnover"]), money(item["nav_end"]),
        ])
    draw_table(pdf, ["Month", "ARMS", "SPY", "QQQ", "BTC", "Eq", "Crypto", "Cash", "Min DD", "Trades", "Turnover", "NAV"], month_rows, [20, 16, 16, 16, 16, 11, 14, 13, 17, 15, 24, 25], 6)

    for page_start in range(0, len(months), 3):
        pdf.add_page()
        section_title(pdf, f"Monthly Detail {months[page_start]['label']} - {months[min(page_start + 2, len(months) - 1)]['label']}")
        for item in months[page_start:page_start + 3]:
            pdf.set_font("Helvetica", "B", 10)
            pdf.cell(0, 6, clean(item["label"]), ln=True)
            top_modules = ", ".join(f"{name}:{count}" for name, count in item["modules"].most_common(5)) or "No trades"
            pds = ", ".join(f"{name}:{count}" for name, count in item["pds"].most_common())
            regimes = ", ".join(f"{name}:{count}" for name, count in item["regime"].most_common())
            draw_table(pdf, ["ARMS", "SPY", "QQQ", "BTC", "Eq", "Crypto", "Cash", "Trades", "Turnover"], [[
                pct(item["arms"]), pct(item["spy"]), pct(item["qqq"]), pct(item["btc"]),
                f"{item['avg_eq']:.1f}%", f"{item['avg_crypto']:.1f}%", f"{item['avg_cash']:.1f}%",
                item["trades"], money(item["turnover"]),
            ]], [19, 19, 19, 19, 16, 17, 17, 18, 26], 7)
            paragraph(pdf, f"PDS: {pds}. Regime: {regimes}. Modules: {top_modules}.", 7, 4.0)
            paragraph(pdf, f"Costs/income: PTRH {money(item['ptrh_cost'])}; slippage {money(item['slippage'])}; PERM income {money(item['perm_income'])}.", 7, 4.0)
            pdf.ln(2)

    pdf.add_page()
    section_title(pdf, "PDS Audit And Feedback Summary")
    paragraph(pdf, "The corrected interpretation is now: NORMAL and WARNING are non-mandatory PDS states for rebalance triggering; DELEVERAGE_1 and DELEVERAGE_2 are the only mandatory PDS trade states. This matches the briefing documents: WARNING is a warning/advisory state, D1 is a 60% ceiling, D2 is a 30% ceiling.")
    paragraph(pdf, "Remaining PDS churn visible in the ledger comes from true D1/D2 threshold crossings and the staged ARES re-entry process. Adding a new PDS exit buffer or arbitrary hysteresis would be a parameter change not explicitly supported by the reviewed briefing language, so it was not invented in this report build.")
    draw_table(pdf, ["Audit Item", "Result"], [
        ["PDS transitions recorded", str(len(transitions))],
        ["Sell-then-buy-higher round trips <=14d", str(rt_count)],
        ["Approx round-trip cost estimate", money(rt_cost)],
        ["Largest churn month", max(months, key=lambda item: item["turnover"])["label"] + " / " + money(max(months, key=lambda item: item["turnover"])["turnover"])],
        ["Most trades in a month", max(months, key=lambda item: item["trades"])["label"] + f" / {max(months, key=lambda item: item['trades'])['trades']} trades"],
    ], [80, 100], 8)
    pdf.ln(4)
    worst_vs_qqq = sorted(months, key=lambda item: item["arms"] - item["qqq"])[:8]
    draw_table(pdf, ["Month", "ARMS", "QQQ", "Gap", "Avg Eq", "Avg Crypto", "Trades"], [
        [item["label"], pct(item["arms"]), pct(item["qqq"]), pct(item["arms"] - item["qqq"]), f"{item['avg_eq']:.0f}%", f"{item['avg_crypto']:.0f}%", item["trades"]]
        for item in worst_vs_qqq
    ], [28, 23, 23, 23, 23, 27, 22], 8)

    pdf.add_page()
    section_title(pdf, "Disclosure")
    paragraph(pdf, "Backtested performance is hypothetical and not indicative of future results. ARMS was not live during the tested period. Figures reflect retroactive application of ARMS rules to historical market data contained in the saved replay output. This updated report was generated from the post-fix CSV artifacts and did not make external API calls or rerun market-data ingestion.")
    paragraph(pdf, "Confidential. Prepared for internal Achelion review. Do not distribute outside authorized GP/PM/development review channels.")

    pdf.output(str(output_path))
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate updated ARMS portfolio report from saved CSVs.")
    parser.add_argument("--start", default="2024-01-01")
    parser.add_argument("--end", default="2025-12-31")
    parser.add_argument("--capital", type=float, default=INITIAL_CAPITAL_DEFAULT)
    parser.add_argument("--output-dir", default="SAMPLES")
    parser.add_argument("--output-name", default=None)
    args = parser.parse_args()

    sample_dir = Path(args.output_dir)
    output_name = args.output_name or f"ARMS_Portfolio_Report_{args.start}_{args.end}_UPDATED.pdf"
    output_path = sample_dir / output_name
    path = build_report(args.start, args.end, args.capital, sample_dir, output_path)
    print(f"Generated: {path}")
    print(f"Size: {path.stat().st_size:,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
