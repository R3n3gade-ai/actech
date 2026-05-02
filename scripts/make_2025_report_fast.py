import csv
import math
import os
from collections import Counter, defaultdict
from datetime import datetime

from fpdf import FPDF


START_DATE = "2025-01-02"
END_DATE = "2025-12-31"
INITIAL_CAPITAL = 500_000_000.0
OUTPUT_DIR = "SAMPLES"
TAG = f"{START_DATE}_{END_DATE}"

NYSE_HOLIDAYS_2025 = {
    "2025-01-01",
    "2025-01-09",
    "2025-01-20",
    "2025-02-17",
    "2025-04-18",
    "2025-05-26",
    "2025-06-19",
    "2025-07-04",
    "2025-09-01",
    "2025-11-27",
    "2025-12-25",
}


def business_days(start: str, end: str) -> list[datetime]:
    cur = datetime.strptime(start, "%Y-%m-%d")
    last = datetime.strptime(end, "%Y-%m-%d")
    days = []
    while cur <= last:
        if cur.weekday() < 5 and cur.strftime("%Y-%m-%d") not in NYSE_HOLIDAYS_2025:
            days.append(cur)
        cur = cur.replace(day=cur.day) if False else cur
        from datetime import timedelta
        cur += timedelta(days=1)
    return days


def fnum(row: dict, key: str, default: float = 0.0) -> float:
    try:
        value = row.get(key, "")
        return float(value) if value not in (None, "") else default
    except Exception:
        return default


def money(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value / 1_000_000:,.1f}M"
    return f"${value:,.0f}"


def pct(value: float) -> str:
    return f"{value:+.2f}%"


def clean(text: object) -> str:
    return str(text).replace("\u2192", "->").encode("latin-1", "replace").decode("latin-1")


def read_history(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    dates = business_days(START_DATE, END_DATE)
    if len(dates) == len(rows) + 1:
        dates = dates[:-1]
    if len(dates) != len(rows):
        raise RuntimeError(f"Date count mismatch: {len(dates)} dates for {len(rows)} rows")
    for row, day in zip(rows, dates):
        row["Date"] = day.strftime("%Y-%m-%d")
        row["Month"] = day.strftime("%Y-%m")
        row["MonthName"] = day.strftime("%b %Y")
    return rows


def read_trades(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        row["Month"] = row.get("Date", "")[:7]
    return rows


def daily_returns(history: list[dict]) -> list[float]:
    values = [fnum(r, "NAV") for r in history]
    return [(values[i] / values[i - 1] - 1.0) for i in range(1, len(values)) if values[i - 1] > 0]


def monthly_records(history: list[dict], trades: list[dict]) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    trades_by_month: dict[str, list[dict]] = defaultdict(list)
    for row in history:
        by_month[row["Month"]].append(row)
    for trade in trades:
        trades_by_month[trade["Month"]].append(trade)

    out = []
    prev_nav = INITIAL_CAPITAL
    prev_spy = fnum(history[0], "SPY_Price")
    prev_qqq = fnum(history[0], "QQQ_Price")
    prev_btc = fnum(history[0], "BTC_Price")
    prev_ptrh = 0.0
    prev_slip = 0.0
    prev_perm = 0.0

    for month in sorted(by_month):
        rows = by_month[month]
        last = rows[-1]
        month_trades = trades_by_month.get(month, [])
        nav = fnum(last, "NAV")
        spy = fnum(last, "SPY_Price", prev_spy)
        qqq = fnum(last, "QQQ_Price", prev_qqq)
        btc = fnum(last, "BTC_Price", prev_btc)
        ptrh = fnum(last, "PTRH_Net_Cost")
        slip = fnum(last, "Total_Slippage")
        perm = fnum(last, "PERM_Income")
        regimes = Counter(r.get("Regime", "") for r in rows)
        pds = Counter(r.get("PDS_Status", "") for r in rows)
        modules = Counter(t.get("Module", "UNKNOWN") for t in month_trades)
        actions = Counter(t.get("Action", "UNKNOWN") for t in month_trades)
        out.append({
            "month": month,
            "label": last["MonthName"],
            "days": len(rows),
            "nav_start": prev_nav,
            "nav_end": nav,
            "arms_gross": (nav / prev_nav - 1.0) * 100 if prev_nav > 0 else 0.0,
            "arms_net": ((nav / prev_nav - 1.0) * 100 - (2.0 / 12)) if prev_nav > 0 else 0.0,
            "spy": (spy / prev_spy - 1.0) * 100 if prev_spy > 0 else 0.0,
            "qqq": (qqq / prev_qqq - 1.0) * 100 if prev_qqq > 0 else 0.0,
            "btc": (btc / prev_btc - 1.0) * 100 if prev_btc > 0 else 0.0,
            "regime": regimes.most_common(1)[0][0] if regimes else "",
            "pds": pds.most_common(1)[0][0] if pds else "",
            "regimes": regimes,
            "pds_counts": pds,
            "eq_avg": sum(fnum(r, "Equity_Pct") for r in rows) / len(rows) * 100,
            "crypto_avg": sum(fnum(r, "Crypto_Pct") for r in rows) / len(rows) * 100,
            "def_avg": sum(fnum(r, "Defensive_Pct") for r in rows) / len(rows) * 100,
            "cash_avg": sum(fnum(r, "Cash_Pct") for r in rows) / len(rows) * 100,
            "vix_avg": sum(fnum(r, "VIX") for r in rows) / len(rows),
            "vix_max": max(fnum(r, "VIX") for r in rows),
            "dd_min": min(fnum(r, "Drawdown") for r in rows) * 100,
            "trades": month_trades,
            "trade_count": len(month_trades),
            "modules": modules,
            "actions": actions,
            "ptrh_cost": ptrh - prev_ptrh,
            "slippage": slip - prev_slip,
            "perm_income": perm - prev_perm,
            "intel_counts": {
                "JPVI": sum(fnum(r, "JPVI_Count") for r in rows),
                "SCCR": sum(fnum(r, "SCCR_Count") for r in rows),
                "PFVT": sum(fnum(r, "PFVT_Count") for r in rows),
                "ELVT": sum(fnum(r, "ELVT_Count") for r in rows),
                "TDC": sum(fnum(r, "TDC_Reviews") for r in rows),
                "CDM": sum(fnum(r, "CDM_Alerts") for r in rows),
            },
        })
        prev_nav, prev_spy, prev_qqq, prev_btc = nav, spy, qqq, btc
        prev_ptrh, prev_slip, prev_perm = ptrh, slip, perm
    return out


def regime_changes(history: list[dict]) -> list[dict]:
    changes = []
    prev = None
    for row in history:
        cur = row.get("Regime", "")
        if cur and cur != prev:
            changes.append(row)
            prev = cur
    return changes


class ReportPDF(FPDF):
    def header(self):
        if self.page_no() > 1:
            self.set_font("Helvetica", "", 7)
            self.cell(0, 5, "ACHELION CAPITAL | ARMS 2025 Backtest Report | CONFIDENTIAL", align="C", ln=True)
            self.ln(1)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 7)
        self.cell(0, 6, f"Page {self.page_no()}", align="C")


def table(pdf: FPDF, headers: list[str], rows: list[list[object]], widths: list[float], font_size: int = 7):
    pdf.set_font("Helvetica", "B", font_size)
    for h, w in zip(headers, widths):
        pdf.cell(w, 6, clean(h), border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", font_size)
    for row in rows:
        if pdf.get_y() > 268:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", font_size)
            for h, w in zip(headers, widths):
                pdf.cell(w, 6, clean(h), border=1, align="C")
            pdf.ln()
            pdf.set_font("Helvetica", "", font_size)
        for val, w in zip(row, widths):
            pdf.cell(w, 5, clean(val), border=1, align="C")
        pdf.ln()


def write_report(history: list[dict], trades: list[dict], path: str):
    months = monthly_records(history, trades)
    final_nav = fnum(history[-1], "NAV")
    total_ret = (final_nav / INITIAL_CAPITAL - 1.0) * 100
    rets = daily_returns(history)
    mean_ret = sum(rets) / len(rets)
    std_ret = math.sqrt(sum((x - mean_ret) ** 2 for x in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    sharpe = (mean_ret / std_ret) * math.sqrt(252) if std_ret else 0.0
    ann_vol = std_ret * math.sqrt(252) * 100
    n_years = len(history) / 252.0
    ann_ret = ((final_nav / INITIAL_CAPITAL) ** (1.0 / n_years) - 1.0) * 100 if n_years else 0.0
    max_dd = min(fnum(r, "Drawdown") for r in history) * 100
    spy_total = (fnum(history[-1], "SPY_Price") / fnum(history[0], "SPY_Price") - 1.0) * 100
    qqq_total = (fnum(history[-1], "QQQ_Price") / fnum(history[0], "QQQ_Price") - 1.0) * 100
    btc_total = (fnum(history[-1], "BTC_Price") / fnum(history[0], "BTC_Price") - 1.0) * 100

    pdf = ReportPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 12, "ACHELION CAPITAL", align="C", ln=True)
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 8, "ARMS 2025 Full-Year Backtest Report", align="C", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Period: {START_DATE} to {END_DATE} | {len(history)} trading days | Generated {datetime.now():%Y-%m-%d %H:%M}", align="C", ln=True)
    pdf.cell(0, 6, "Source: completed 5:03 PM history/trade CSV outputs; no rerun, no EDGAR, no network calls", align="C", ln=True)
    pdf.ln(6)

    summary_rows = [
        ["Initial Capital", money(INITIAL_CAPITAL), "Final NAV", money(final_nav)],
        ["ARMS Gross Return", pct(total_ret), "ARMS Net est. (2%)", pct(total_ret - 2.0 * n_years)],
        ["Annualized Return", pct(ann_ret), "Annualized Volatility", f"{ann_vol:.2f}%"],
        ["Sharpe", f"{sharpe:.2f}", "Max Drawdown", f"{max_dd:.2f}%"],
        ["SPY Return", pct(spy_total), "QQQ Return", pct(qqq_total)],
        ["BTC Return", pct(btc_total), "Trades", str(len(trades))],
        ["PTRH Net Cost", money(fnum(history[-1], "PTRH_Net_Cost")), "LAEP Slippage", money(fnum(history[-1], "Total_Slippage"))],
        ["PERM Income", money(fnum(history[-1], "PERM_Income")), "SLOF Active Days", str(sum(1 for r in history if str(r.get("SLOF_Active", "")).lower() == "true"))],
    ]
    table(pdf, ["Metric", "Value", "Metric", "Value"], summary_rows, [48, 45, 48, 45], 8)

    pdf.ln(5)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Regime / PDS Distribution", ln=True)
    reg = Counter(r.get("Regime", "") for r in history)
    pds = Counter(r.get("PDS_Status", "") for r in history)
    rows = []
    for name in ["RISK_ON", "WATCH", "NEUTRAL", "DEFENSIVE", "CRASH"]:
        rows.append([name, reg.get(name, 0), f"{reg.get(name, 0) / len(history) * 100:.1f}%", "", ""])
    for i, name in enumerate(["NORMAL", "WARNING", "DELEVERAGE_1", "DELEVERAGE_2"]):
        rows[i][3] = name
        rows[i][4] = f"{pds.get(name, 0)} days ({pds.get(name, 0) / len(history) * 100:.1f}%)"
    table(pdf, ["Regime", "Days", "%", "PDS", "Days / %"], rows, [38, 25, 25, 43, 55], 8)

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, "Monthly Detail Table", align="C", ln=True)
    month_rows = []
    for m in months:
        month_rows.append([
            m["label"], pct(m["arms_gross"]), pct(m["arms_net"]), pct(m["spy"]), pct(m["qqq"]), pct(m["btc"]),
            m["regime"], m["pds"], f"{m['eq_avg']:.0f}%", f"{m['crypto_avg']:.0f}%", f"{m['cash_avg']:.0f}%", m["trade_count"], money(m["nav_end"]),
        ])
    table(pdf, ["Month", "ARMS G", "ARMS N", "SPY", "QQQ", "BTC", "Regime", "PDS", "Eq", "Cr", "Cash", "Trades", "NAV"], month_rows, [19, 16, 16, 15, 15, 15, 21, 25, 11, 11, 13, 14, 25], 6)

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, "Individual Monthly Pages", align="C", ln=True)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(0, 5, "Each following page gives month-level performance, benchmarks, state distribution, allocation, costs, trade activity, and module activity from the completed 2025 replay output.")

    for m in months:
        pdf.add_page()
        pdf.set_font("Helvetica", "B", 15)
        pdf.cell(0, 9, m["label"], align="C", ln=True)
        perf = [
            ["ARMS Gross", pct(m["arms_gross"]), "ARMS Net", pct(m["arms_net"])],
            ["SPY", pct(m["spy"]), "QQQ", pct(m["qqq"])],
            ["BTC", pct(m["btc"]), "Max DD", f"{m['dd_min']:.2f}%"],
            ["Start NAV", money(m["nav_start"]), "End NAV", money(m["nav_end"])],
            ["Avg VIX", f"{m['vix_avg']:.1f}", "Max VIX", f"{m['vix_max']:.1f}"],
        ]
        table(pdf, ["Metric", "Value", "Metric", "Value"], perf, [45, 45, 45, 45], 8)
        pdf.ln(3)
        alloc = [["Equity", f"{m['eq_avg']:.1f}%"], ["Crypto", f"{m['crypto_avg']:.1f}%"], ["Defensive", f"{m['def_avg']:.1f}%"], ["Cash", f"{m['cash_avg']:.1f}%"]]
        table(pdf, ["Sleeve", "Avg Allocation"], alloc, [50, 50], 8)
        pdf.ln(3)
        state_rows = [[k, v, f"{v / m['days'] * 100:.1f}%"] for k, v in m["regimes"].most_common()]
        state_rows += [["PDS: " + k, v, f"{v / m['days'] * 100:.1f}%"] for k, v in m["pds_counts"].most_common()]
        table(pdf, ["State", "Days", "%"], state_rows, [65, 30, 30], 8)
        pdf.ln(3)
        module_rows = [[k, v] for k, v in m["modules"].most_common(10)] or [["No trades", 0]]
        action_rows = [["Action: " + k, v] for k, v in m["actions"].most_common(10)] or []
        table(pdf, ["Trade / Module Activity", "Count"], module_rows + action_rows, [90, 35], 8)
        pdf.ln(3)
        cost_rows = [["PTRH cost in month", money(m["ptrh_cost"])], ["LAEP slippage in month", money(m["slippage"])], ["PERM income in month", money(m["perm_income"] )]]
        table(pdf, ["Cost / Income", "Value"], cost_rows, [75, 50], 8)
        pdf.ln(3)
        intel_rows = [[k, int(v)] for k, v in m["intel_counts"].items()]
        table(pdf, ["Intelligence telemetry count", "Count"], intel_rows, [75, 50], 8)

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, "Regime Change Log", align="C", ln=True)
    change_rows = [[r["Date"], r.get("Regime", ""), fnum(r, "Regime_Score"), fnum(r, "VIX"), r.get("PDS_Status", "")] for r in regime_changes(history)]
    table(pdf, ["Date", "Regime", "Score", "VIX", "PDS"], change_rows, [30, 38, 30, 25, 45], 8)

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, "Best / Worst Months", align="C", ln=True)
    best = sorted(months, key=lambda x: x["arms_gross"], reverse=True)[:5]
    worst = sorted(months, key=lambda x: x["arms_gross"])[:5]
    rows = []
    for i in range(5):
        rows.append([best[i]["label"], pct(best[i]["arms_gross"]), best[i]["regime"], worst[i]["label"], pct(worst[i]["arms_gross"]), worst[i]["regime"]])
    table(pdf, ["Best Month", "ARMS", "Regime", "Worst Month", "ARMS", "Regime"], rows, [32, 24, 32, 32, 24, 32], 8)

    pdf.ln(6)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 7, "Report Construction Notes", ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 5, clean(
        "This PDF was built directly from SAMPLES/history_2025-01-02_2025-12-31.csv and "
        "SAMPLES/trade_ledger_2025-01-02_2025-12-31.csv written at 5:03 PM. The earlier full "
        "harness run completed the simulation loop and wrote those files, but the post-run path "
        "blocked before writing the PDF. This fast generator intentionally does not call EDGAR, "
        "yfinance, PatentsView, OpenAI, or any other network service."
    ))

    pdf.add_page()
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Disclosures", ln=True)
    pdf.set_font("Helvetica", "", 8)
    pdf.multi_cell(0, 5, clean(
        "Backtested performance is hypothetical and not indicative of future results. ARMS was not live "
        "during the tested period. Figures reflect retroactive application of ARMS rules to historical "
        "market data contained in the saved replay output. Management fee estimate is 2.0% annualized, "
        "shown monthly in net figures. This document is confidential and prepared for internal review."
    ))
    pdf.output(path)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    history = read_history(os.path.join(OUTPUT_DIR, f"history_{TAG}.csv"))
    trades = read_trades(os.path.join(OUTPUT_DIR, f"trade_ledger_{TAG}.csv"))
    out_path = os.path.join(OUTPUT_DIR, f"ARMS_Report_{TAG}.pdf")
    write_report(history, trades, out_path)
    print(out_path)


if __name__ == "__main__":
    main()