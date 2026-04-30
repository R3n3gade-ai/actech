"""
PDF Report Generator — Institutional Performance Report
=========================================================
Generates a professional PDF tearsheet with charts from backtest results.

Includes:
  - Cover page with headline metrics and benchmark comparison
  - Performance summary table (gross + net after 2% mgmt fee)
  - Year-by-year breakdown with SPY / QQQ / BTC benchmarks
  - Monthly detail tables with regime and allocation columns
  - Regime change log with context
  - Architecture & cost summary (PTRH, PERM, LAEP)
  - Charts: cumulative returns, monthly bar, drawdown, regime, allocation, VIX
  - Trade activity summary
  - Disclosures

Uses matplotlib for chart generation and fpdf2 for PDF composition.
"""

import logging
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────── Chart Generation ──

def _generate_charts(
    history: pd.DataFrame,
    initial_capital: float,
    output_dir: str,
) -> Dict[str, str]:
    """Generate chart images for the PDF report."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
    except ImportError:
        logger.warning("matplotlib not installed — skipping charts")
        return {}

    charts = {}
    fw, fh = 10, 4

    # ── Chart 1: Cumulative Performance vs Benchmarks ──
    fig, ax = plt.subplots(figsize=(fw, fh))
    arms_ret = (history["NAV"] / initial_capital - 1) * 100
    ax.plot(arms_ret.index, arms_ret.values, color="#3b82f6", linewidth=1.5, label="ARMS Strategy")

    if "SPY_Price" in history.columns:
        spy_ret = (history["SPY_Price"] / history["SPY_Price"].iloc[0] - 1) * 100
        ax.plot(spy_ret.index, spy_ret.values, color="#94a3b8", linewidth=1, alpha=0.7, label="SPY")
    if "QQQ_Price" in history.columns:
        qqq_ret = (history["QQQ_Price"] / history["QQQ_Price"].iloc[0] - 1) * 100
        ax.plot(qqq_ret.index, qqq_ret.values, color="#64748b", linewidth=1, alpha=0.7, label="QQQ", linestyle="--")
    if "BTC_Price" in history.columns:
        btc_ret = (history["BTC_Price"] / history["BTC_Price"].iloc[0] - 1) * 100
        ax.plot(btc_ret.index, btc_ret.values, color="#f59e0b", linewidth=1, alpha=0.5, label="BTC", linestyle=":")

    ax.set_title("Cumulative Returns — ARMS vs Benchmarks", fontsize=12, fontweight="bold")
    ax.set_ylabel("Return (%)")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.axhline(y=0, color="#475569", linewidth=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45, fontsize=8)
    plt.tight_layout()
    path = os.path.join(output_dir, "chart_cumulative_returns.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    charts["cumulative_returns"] = path

    # ── Chart 2: Drawdown ──
    fig, ax = plt.subplots(figsize=(fw, 3))
    dd = history["Drawdown"] * 100
    ax.fill_between(dd.index, dd.values, 0, color="#ef4444", alpha=0.3)
    ax.plot(dd.index, dd.values, color="#ef4444", linewidth=1)
    ax.set_title("Drawdown from Peak", fontsize=12, fontweight="bold")
    ax.set_ylabel("Drawdown (%)")
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45, fontsize=8)
    plt.tight_layout()
    path = os.path.join(output_dir, "chart_drawdown.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    charts["drawdown"] = path

    # ── Chart 3: Regime Timeline ──
    fig, ax = plt.subplots(figsize=(fw, 2.5))
    regime_colors = {
        "RISK_ON": "#10b981", "WATCH": "#f59e0b", "NEUTRAL": "#3b82f6",
        "DEFENSIVE": "#f97316", "CRASH": "#ef4444",
    }
    regime_nums = {
        "RISK_ON": 4, "WATCH": 3, "NEUTRAL": 2, "DEFENSIVE": 1, "CRASH": 0,
    }
    colors = [regime_colors.get(r, "#64748b") for r in history["Regime"]]
    for j in range(len(history) - 1):
        ax.axvspan(history.index[j], history.index[j + 1], ymin=0, ymax=1, color=colors[j], alpha=0.4)
    ax.set_title("Regime Timeline", fontsize=12, fontweight="bold")
    ax.set_yticks(list(regime_nums.values()))
    ax.set_yticklabels(list(regime_nums.keys()), fontsize=8)
    ax.set_ylim(-0.5, 4.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45, fontsize=8)
    plt.tight_layout()
    path = os.path.join(output_dir, "chart_regime_timeline.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    charts["regime_timeline"] = path

    # ── Chart 4: Allocation Over Time ──
    fig, ax = plt.subplots(figsize=(fw, fh))
    ax.stackplot(
        history.index,
        history["Equity_Pct"].values * 100,
        history.get("Crypto_Pct", pd.Series(0, index=history.index)).values * 100,
        history.get("Defensive_Pct", pd.Series(0, index=history.index)).values * 100,
        history["Cash_Pct"].values * 100,
        labels=["Equity", "Crypto", "Defensive", "Cash"],
        colors=["#3b82f6", "#f59e0b", "#10b981", "#94a3b8"],
        alpha=0.7,
    )
    ax.set_title("Portfolio Allocation Over Time", fontsize=12, fontweight="bold")
    ax.set_ylabel("Allocation (%)")
    ax.set_ylim(0, 110)
    ax.legend(loc="upper right", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45, fontsize=8)
    plt.tight_layout()
    path = os.path.join(output_dir, "chart_allocation.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    charts["allocation"] = path

    # ── Chart 5: VIX with LAEP Tiers ──
    fig, ax = plt.subplots(figsize=(fw, 3))
    ax.plot(history.index, history["VIX"].values, color="#8b5cf6", linewidth=1, label="VIX")
    ax.axhline(y=16, color="#10b981", linewidth=0.5, linestyle="--", alpha=0.5, label="Tier 1/2 (16)")
    ax.axhline(y=28, color="#f59e0b", linewidth=0.5, linestyle="--", alpha=0.5, label="Tier 3/4 (28)")
    ax.axhline(y=40, color="#ef4444", linewidth=0.5, linestyle="--", alpha=0.5, label="Crisis (40)")
    ax.set_title("VIX & LAEP Execution Tiers", fontsize=12, fontweight="bold")
    ax.set_ylabel("VIX Level")
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(True, alpha=0.3)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
    plt.xticks(rotation=45, fontsize=8)
    plt.tight_layout()
    path = os.path.join(output_dir, "chart_vix.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    charts["vix"] = path

    # ── Chart 6: Monthly Returns Bar Chart ──
    monthly_nav = history["NAV"].resample("ME").last()
    monthly_rets = monthly_nav.pct_change().dropna() * 100
    fig, ax = plt.subplots(figsize=(fw, 3.5))
    bar_colors = ["#10b981" if r >= 0 else "#ef4444" for r in monthly_rets.values]
    ax.bar(monthly_rets.index, monthly_rets.values, width=20, color=bar_colors, alpha=0.8)
    ax.set_title("Monthly Returns", fontsize=12, fontweight="bold")
    ax.set_ylabel("Return (%)")
    ax.grid(True, alpha=0.3, axis="y")
    ax.axhline(y=0, color="#475569", linewidth=0.5)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %y"))
    ax.xaxis.set_major_locator(mdates.MonthLocator(interval=2))
    plt.xticks(rotation=45, fontsize=7)
    plt.tight_layout()
    path = os.path.join(output_dir, "chart_monthly_returns.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    charts["monthly_returns"] = path

    return charts


# ────────────────────────────────────────────────── Regime Change Detection ──

def _detect_regime_changes(history: pd.DataFrame) -> List[dict]:
    """Detect regime transitions from daily history."""
    changes: List[dict] = []
    prev_regime = history["Regime"].iloc[0]
    for i in range(1, len(history)):
        cur = history["Regime"].iloc[i]
        if cur != prev_regime:
            changes.append({
                "date": history.index[i],
                "from": prev_regime,
                "to": cur,
                "vix": history["VIX"].iloc[i],
                "score": history["Regime_Score"].iloc[i] if "Regime_Score" in history.columns else 0,
            })
            prev_regime = cur
    return changes


# ─────────────────────────────────────────────────── Monthly Detail Builder ──

def _compute_monthly_detail(
    history: pd.DataFrame,
    initial_capital: float,
) -> pd.DataFrame:
    """Compute monthly returns with benchmark columns."""
    monthly = history.resample("ME").last()
    rows = []
    prev_nav = initial_capital
    prev_spy = history["SPY_Price"].iloc[0] if "SPY_Price" in history.columns else 1
    prev_qqq = history["QQQ_Price"].iloc[0] if "QQQ_Price" in history.columns else 1
    prev_btc = history["BTC_Price"].iloc[0] if "BTC_Price" in history.columns else 1

    for idx, m in monthly.iterrows():
        nav = m["NAV"]
        arms_g = (nav / prev_nav - 1) * 100
        arms_n = arms_g - (2.0 / 12)  # 2% annual fee applied monthly

        spy = m.get("SPY_Price", prev_spy)
        spy_ret = (spy / prev_spy - 1) * 100 if prev_spy > 0 else 0
        qqq = m.get("QQQ_Price", prev_qqq)
        qqq_ret = (qqq / prev_qqq - 1) * 100 if prev_qqq > 0 else 0
        btc = m.get("BTC_Price", prev_btc)
        btc_ret = (btc / prev_btc - 1) * 100 if prev_btc > 0 else 0

        rows.append({
            "Month": idx.strftime("%b %Y"),
            "Year": str(idx.year),
            "ARMS_G": arms_g,
            "ARMS_N": arms_n,
            "QQQ": qqq_ret,
            "BTC": btc_ret,
            "DEF": m.get("Defensive_Pct", 0) * 100,
            "SPY": spy_ret,
            "Regime": m.get("Regime", ""),
            "Eq_Pct": m.get("Equity_Pct", 0) * 100,
            "Cr_Pct": m.get("Crypto_Pct", 0) * 100,
            "Ca_Pct": m.get("Cash_Pct", 0) * 100,
        })

        prev_nav = nav
        if spy > 0: prev_spy = spy
        if qqq > 0: prev_qqq = qqq
        if btc > 0: prev_btc = btc

    return pd.DataFrame(rows)


# ──────────────────────────────────────────────────────── PDF Report Builder ──

def generate_pdf_report(
    history: pd.DataFrame,
    trade_ledger: List[dict],
    start_date: str,
    end_date: str,
    initial_capital: float = 500_000_000,
    output_dir: str = "SAMPLES",
) -> str:
    """Generate an institutional PDF report from backtest results."""
    os.makedirs(output_dir, exist_ok=True)
    chart_dir = os.path.join(output_dir, "charts")
    os.makedirs(chart_dir, exist_ok=True)

    charts = _generate_charts(history, initial_capital, chart_dir)

    # ── Core statistics ──
    final_nav = history["NAV"].iloc[-1]
    total_ret = (final_nav / initial_capital - 1) * 100
    daily_ret = history["NAV"].pct_change().dropna()
    n_days = len(history)
    n_years = n_days / 252
    ann_ret = ((final_nav / initial_capital) ** (1 / max(n_years, 0.01)) - 1) * 100
    ann_vol = daily_ret.std() * np.sqrt(252) * 100
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = history["Drawdown"].min() * 100
    net_total = total_ret - (2.0 * n_years)
    net_ann = ann_ret - 2.0

    # Benchmark totals
    spy_total = (history["SPY_Price"].iloc[-1] / history["SPY_Price"].iloc[0] - 1) * 100 if "SPY_Price" in history.columns else 0
    qqq_total = (history["QQQ_Price"].iloc[-1] / history["QQQ_Price"].iloc[0] - 1) * 100 if "QQQ_Price" in history.columns else 0
    btc_total = (history["BTC_Price"].iloc[-1] / history["BTC_Price"].iloc[0] - 1) * 100 if "BTC_Price" in history.columns else 0

    # PTRH / PERM / LAEP
    ptrh_cost = history["PTRH_Net_Cost"].iloc[-1] if "PTRH_Net_Cost" in history.columns else 0
    perm_income = history["PERM_Income"].iloc[-1] if "PERM_Income" in history.columns else 0
    total_slippage = history["Total_Slippage"].iloc[-1] if "Total_Slippage" in history.columns else 0

    # Regime & PDS breakdowns
    regime_counts = history["Regime"].value_counts()
    pds_counts = history["PDS_Status"].value_counts() if "PDS_Status" in history.columns else pd.Series(dtype=int)

    # Monthly detail
    monthly_detail = _compute_monthly_detail(history, initial_capital)

    # Regime changes
    regime_changes = _detect_regime_changes(history)

    # Year-by-year returns
    yearly = {}
    for year in sorted(history.index.year.unique()):
        yd = history[history.index.year == year]
        yearly[year] = (yd["NAV"].iloc[-1] / yd["NAV"].iloc[0] - 1) * 100

    try:
        from fpdf import FPDF
    except ImportError:
        logger.warning("fpdf2 not installed — generating HTML fallback")
        return _generate_html_report(
            history, trade_ledger, start_date, end_date,
            initial_capital, charts, output_dir,
        )

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 1 — COVER + HEADLINE METRICS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()

    pdf.set_font("Helvetica", "B", 22)
    pdf.cell(0, 14, "ACHELION CAPITAL", ln=True, align="C")
    pdf.set_font("Helvetica", "", 11)
    pdf.cell(0, 7, "ARMS Backtest Performance Report", ln=True, align="C")
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, f"Architecture AB  |  58/20/14/8  |  ARMS v4.0", ln=True, align="C")
    pdf.cell(0, 6, f"Period: {start_date} to {end_date}  |  {n_days} trading days  |  {len(regime_changes)} regime changes", ln=True, align="C")
    pdf.cell(0, 6, f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  CONFIDENTIAL", ln=True, align="C")
    pdf.ln(6)

    # ── Headline metric boxes ──
    col_w = 47
    metrics = [
        (f"{total_ret:+.1f}%", "ARMS Gross"),
        (f"{net_total:+.1f}%", "ARMS Net (2%)"),
        (f"{spy_total:+.1f}%", "S&P 500"),
        (f"{total_ret - spy_total:+.1f}pp", "vs S&P Gap"),
    ]
    for val, _ in metrics:
        pdf.set_font("Helvetica", "B", 16)
        pdf.cell(col_w, 12, val, border=1, align="C")
    pdf.ln()
    for _, label in metrics:
        pdf.set_font("Helvetica", "", 8)
        pdf.cell(col_w, 6, label, border=1, align="C")
    pdf.ln(6)

    # ── Performance Summary Table ──
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Performance Summary", ln=True)

    pdf.set_font("Helvetica", "B", 8)
    perf_hdr = ["Metric", "ARMS Gross", "ARMS Net", "S&P 500", "QQQ", "BTC"]
    perf_cw = [50, 28, 28, 28, 28, 28]
    for i, h in enumerate(perf_hdr):
        pdf.cell(perf_cw[i], 6, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 8)
    perf_rows = [
        ("Cumulative Return", f"{total_ret:+.1f}%", f"{net_total:+.1f}%", f"{spy_total:+.1f}%", f"{qqq_total:+.1f}%", f"{btc_total:+.1f}%"),
        ("Annualized Return", f"{ann_ret:+.1f}%", f"{net_ann:+.1f}%", "", "", ""),
        ("Annualized Volatility", f"{ann_vol:.1f}%", "", "", "", ""),
        ("Sharpe Ratio", f"{sharpe:.2f}", "", "", "", ""),
        ("Max Drawdown", f"{max_dd:.1f}%", "", "", "", ""),
    ]
    for row in perf_rows:
        for i, v in enumerate(row):
            pdf.cell(perf_cw[i], 6, v, border=1, align="C" if i > 0 else "L")
        pdf.ln()

    # Year-by-year rows
    for yr, ret in yearly.items():
        yd = history[history.index.year == yr]
        spy_yr = (yd["SPY_Price"].iloc[-1] / yd["SPY_Price"].iloc[0] - 1) * 100 if "SPY_Price" in yd.columns else 0
        qqq_yr = (yd["QQQ_Price"].iloc[-1] / yd["QQQ_Price"].iloc[0] - 1) * 100 if "QQQ_Price" in yd.columns else 0
        btc_yr = (yd["BTC_Price"].iloc[-1] / yd["BTC_Price"].iloc[0] - 1) * 100 if "BTC_Price" in yd.columns else 0
        net_yr = ret - 2.0
        pdf.cell(perf_cw[0], 6, f"{yr} Return", border=1)
        pdf.cell(perf_cw[1], 6, f"{ret:+.1f}%", border=1, align="C")
        pdf.cell(perf_cw[2], 6, f"{net_yr:+.1f}%", border=1, align="C")
        pdf.cell(perf_cw[3], 6, f"{spy_yr:+.1f}%", border=1, align="C")
        pdf.cell(perf_cw[4], 6, f"{qqq_yr:+.1f}%", border=1, align="C")
        pdf.cell(perf_cw[5], 6, f"{btc_yr:+.1f}%", border=1, align="C")
        pdf.ln()
    pdf.ln(3)

    # ── Architecture & Cost Summary ──
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Architecture & Costs", ln=True)
    pdf.set_font("Helvetica", "", 8)

    cost_rows = [
        ("Initial Capital", f"${initial_capital:,.0f}"),
        ("Final NAV", f"${final_nav:,.0f}"),
        ("Total Trades", str(len(trade_ledger))),
        ("PTRH Net Cost (Tail Hedge)", f"${ptrh_cost:,.0f}"),
        ("PERM Income (Covered Calls)", f"${perm_income:,.0f}"),
        ("LAEP Slippage (Execution Cost)", f"${total_slippage:,.0f}"),
        ("Avg Equity Allocation", f"{history['Equity_Pct'].mean()*100:.1f}% (target 58%)"),
        ("Avg Crypto Allocation", f"{history.get('Crypto_Pct', pd.Series(0)).mean()*100:.1f}% (target 20%)"),
        ("Avg Defensive Allocation", f"{history.get('Defensive_Pct', pd.Series(0)).mean()*100:.1f}% (target 14%)"),
        ("Avg Cash Allocation", f"{history['Cash_Pct'].mean()*100:.1f}% (target 8%)"),
    ]
    for label, val in cost_rows:
        pdf.cell(80, 6, label, border=1)
        pdf.cell(80, 6, val, border=1, ln=True)
    pdf.ln(3)

    # ── Regime & PDS Breakdown (side-by-side) ──
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(95, 8, "Regime Breakdown")
    pdf.cell(95, 8, "PDS Status Breakdown", ln=True)
    pdf.set_font("Helvetica", "", 8)

    regime_order = ["RISK_ON", "WATCH", "NEUTRAL", "DEFENSIVE", "CRASH"]
    pds_order = ["NORMAL", "WARNING", "DELEVERAGE_1", "DELEVERAGE_2"]
    for i in range(max(len(regime_order), len(pds_order))):
        if i < len(regime_order):
            r = regime_order[i]
            cnt = regime_counts.get(r, 0)
            pdf.cell(55, 5, r, border=1)
            pdf.cell(40, 5, f"{cnt} days ({cnt/n_days*100:.1f}%)", border=1)
        else:
            pdf.cell(95, 5, "")
        if i < len(pds_order):
            s = pds_order[i]
            cnt = pds_counts.get(s, 0)
            pdf.cell(55, 5, s, border=1)
            pdf.cell(40, 5, f"{cnt} days ({cnt/n_days*100:.1f}%)", border=1)
        pdf.ln()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE 2+ — CHARTS
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Performance Charts", ln=True, align="C")
    pdf.ln(2)

    chart_order = [
        "cumulative_returns", "monthly_returns", "drawdown",
        "regime_timeline", "allocation", "vix",
    ]
    for cn in chart_order:
        cp = charts.get(cn)
        if cp and os.path.exists(cp):
            if pdf.get_y() > 210:
                pdf.add_page()
            pdf.image(cp, x=10, w=190)
            pdf.ln(3)

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE — MONTHLY DETAIL TABLE
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Monthly Detail", ln=True, align="C")
    pdf.ln(2)

    mcw = [22, 18, 18, 16, 16, 14, 16, 22, 16, 16, 16]
    mhd = ["Month", "ARMS G", "ARMS N", "QQQ", "BTC", "DEF%", "SPY", "Regime", "Eq%", "Cr%", "Cash%"]

    def _print_month_header():
        pdf.set_font("Helvetica", "B", 7)
        for i, h in enumerate(mhd):
            pdf.cell(mcw[i], 5, h, border=1, align="C")
        pdf.ln()
        pdf.set_font("Helvetica", "", 7)

    _print_month_header()

    prev_year = None
    for _, row in monthly_detail.iterrows():
        cur_year = row["Year"]

        # Year subtotal separator
        if prev_year is not None and cur_year != prev_year:
            yr_rows = monthly_detail[monthly_detail["Year"] == prev_year]
            pdf.set_font("Helvetica", "B", 7)
            pdf.cell(mcw[0], 5, f"{prev_year} TOTAL", border=1)
            pdf.cell(mcw[1], 5, f"{yr_rows['ARMS_G'].sum():+.1f}%", border=1, align="C")
            pdf.cell(mcw[2], 5, f"{yr_rows['ARMS_N'].sum():+.1f}%", border=1, align="C")
            pdf.cell(mcw[3], 5, f"{yr_rows['QQQ'].sum():+.1f}%", border=1, align="C")
            pdf.cell(mcw[4], 5, f"{yr_rows['BTC'].sum():+.1f}%", border=1, align="C")
            pdf.cell(mcw[5], 5, "", border=1)
            pdf.cell(mcw[6], 5, f"{yr_rows['SPY'].sum():+.1f}%", border=1, align="C")
            for ci in range(4):
                pdf.cell(mcw[7 + ci], 5, "", border=1)
            pdf.ln()
            pdf.set_font("Helvetica", "", 7)

        prev_year = cur_year

        if pdf.get_y() > 270:
            pdf.add_page()
            _print_month_header()

        vals = [
            row["Month"],
            f"{row['ARMS_G']:+.1f}%",
            f"{row['ARMS_N']:+.1f}%",
            f"{row['QQQ']:+.1f}%",
            f"{row['BTC']:+.1f}%",
            f"{row['DEF']:.0f}%",
            f"{row['SPY']:+.1f}%",
            row["Regime"],
            f"{row['Eq_Pct']:.0f}%",
            f"{row['Cr_Pct']:.0f}%",
            f"{row['Ca_Pct']:.0f}%",
        ]
        for i, v in enumerate(vals):
            pdf.cell(mcw[i], 5, v, border=1, align="C" if i > 0 else "L")
        pdf.ln()

    # Final year subtotal
    if prev_year is not None:
        yr_rows = monthly_detail[monthly_detail["Year"] == prev_year]
        pdf.set_font("Helvetica", "B", 7)
        pdf.cell(mcw[0], 5, f"{prev_year} TOTAL", border=1)
        pdf.cell(mcw[1], 5, f"{yr_rows['ARMS_G'].sum():+.1f}%", border=1, align="C")
        pdf.cell(mcw[2], 5, f"{yr_rows['ARMS_N'].sum():+.1f}%", border=1, align="C")
        pdf.cell(mcw[3], 5, f"{yr_rows['QQQ'].sum():+.1f}%", border=1, align="C")
        pdf.cell(mcw[4], 5, f"{yr_rows['BTC'].sum():+.1f}%", border=1, align="C")
        pdf.cell(mcw[5], 5, "", border=1)
        pdf.cell(mcw[6], 5, f"{yr_rows['SPY'].sum():+.1f}%", border=1, align="C")
        for ci in range(4):
            pdf.cell(mcw[7 + ci], 5, "", border=1)
        pdf.ln()

    # ══════════════════════════════════════════════════════════════════════════
    # PAGE — REGIME CHANGE LOG
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 10, "Regime Change Log", ln=True, align="C")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 8)
    rcw = [26, 32, 32, 20, 20, 60]
    rch = ["Date", "From", "To", "VIX", "Score", "Context"]
    for i, h in enumerate(rch):
        pdf.cell(rcw[i], 6, h, border=1, align="C")
    pdf.ln()

    pdf.set_font("Helvetica", "", 7)
    for rc in regime_changes:
        if pdf.get_y() > 270:
            pdf.add_page()
            pdf.set_font("Helvetica", "B", 8)
            for i, h in enumerate(rch):
                pdf.cell(rcw[i], 6, h, border=1, align="C")
            pdf.ln()
            pdf.set_font("Helvetica", "", 7)

        ctx = ""
        if rc["to"] == "CRASH":
            ctx = f"VIX spike to {rc['vix']:.0f}"
        elif rc["to"] == "DEFENSIVE":
            ctx = "Composite crosses 0.50"
        elif rc["to"] == "NEUTRAL":
            ctx = "Composite crosses 0.35"
        elif rc["to"] == "WATCH":
            ctx = "Composite near 0.30"
        elif rc["to"] == "RISK_ON":
            ctx = "Composite below 0.25"

        vals = [
            rc["date"].strftime("%Y-%m-%d"),
            rc["from"], rc["to"],
            f"{rc['vix']:.1f}", f"{rc['score']:.3f}", ctx,
        ]
        for i, v in enumerate(vals):
            pdf.cell(rcw[i], 5, v, border=1, align="C" if i > 0 else "L")
        pdf.ln()

    pdf.ln(2)
    pdf.set_font("Helvetica", "", 9)
    freq = len(regime_changes) / max(1, n_years * 12)
    pdf.cell(0, 6, f"Total regime changes: {len(regime_changes)}  |  Avg frequency: {freq:.1f} per month", ln=True)

    # ── Trade Activity Summary ──
    pdf.ln(4)
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Trade Activity Summary", ln=True)
    pdf.set_font("Helvetica", "", 8)

    actions = Counter(t.get("Action", "UNKNOWN") for t in trade_ledger)
    modules = Counter(t.get("Module", "UNKNOWN") for t in trade_ledger)

    pdf.cell(0, 6, "By Action:", ln=True)
    for action in sorted(actions.keys()):
        pdf.cell(55, 5, f"  {action}", border=0)
        pdf.cell(30, 5, str(actions[action]), border=0, ln=True)
    pdf.ln(2)
    pdf.cell(0, 6, "By Module:", ln=True)
    for mod in sorted(modules.keys()):
        pdf.cell(55, 5, f"  {mod}", border=0)
        pdf.cell(30, 5, str(modules[mod]), border=0, ln=True)

    # ══════════════════════════════════════════════════════════════════════════
    # FINAL PAGE — DISCLOSURES
    # ══════════════════════════════════════════════════════════════════════════
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 11)
    pdf.cell(0, 8, "Important Disclosures", ln=True)
    pdf.set_font("Helvetica", "", 7)

    disclosures = (
        "BACKTESTED PERFORMANCE DISCLOSURE: Results are backtested and hypothetical. "
        "ARMS was not live during this period. All figures reflect retroactive application "
        "of ARMS v4.0 parameters to actual historical market data.\n\n"
        "BTC returns use BTC-USD spot price as proxy. QQQ and SPY returns are actual ETF "
        "total returns from public record. Defense sleeve figures (SGOV, SGOL, DBMF, STRC) "
        "are approximate within +/-0.5% for DBMF and STRC components.\n\n"
        "Management fee of 2.0% per annum applied monthly in Net figures. No performance "
        "fee applied. S&P 500 (SPY) is the primary benchmark.\n\n"
        "ARCHITECTURE AB: 58% equity / 20% crypto / 14% defensive / 8% cash. "
        "Equity sleeve uses individual position selection via MICS conviction scoring with "
        "KEVLAR risk limits. Crypto sleeve uses BTC-USD as proxy. "
        "Defensive sleeve: SGOV (3%), SGOL (2%), DBMF (5%), STRC (4%).\n\n"
        "Regime system: 5-state Macro Compass -> ARAS with hysteresis bands. "
        "Tail hedge: PTRH via regime table (10-15% OTM QQQ puts). "
        "Execution: LAEP 5-tier system with VIX-adjusted slippage modeling.\n\n"
        "Past backtested performance is not indicative of future results. "
        "CONFIDENTIAL - prepared for internal use."
    )
    pdf.multi_cell(0, 4, disclosures)

    # ── Save PDF ──
    filename = f"ARMS_Report_{start_date}_{end_date}.pdf"
    filepath = os.path.join(output_dir, filename)
    pdf.output(filepath)
    logger.info(f"PDF report generated: {filepath}")
    return filepath


# ────────────────────────────────────────────────────── HTML Fallback Report ──

def _generate_html_report(
    history: pd.DataFrame,
    trade_ledger: List[dict],
    start_date: str,
    end_date: str,
    initial_capital: float,
    charts: Dict[str, str],
    output_dir: str,
) -> str:
    """Fallback: generate HTML report when fpdf2 is not available."""
    final_nav = history["NAV"].iloc[-1]
    total_ret = (final_nav / initial_capital - 1) * 100
    daily_ret = history["NAV"].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = history["Drawdown"].min() * 100

    html = f"""<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<title>ARMS Backtest Report</title>
<style>
body{{font-family:system-ui;background:#0a0e1a;color:#e2e8f0;padding:40px;max-width:1000px;margin:0 auto}}
h1{{color:#06b6d4}}h2{{color:#3b82f6;border-bottom:1px solid #1e293b;padding-bottom:8px}}
table{{border-collapse:collapse;width:100%;margin:16px 0}}
td,th{{border:1px solid #1e293b;padding:8px 12px;text-align:left}}
th{{background:#111827;font-size:12px;text-transform:uppercase}}
img{{max-width:100%;border-radius:8px;margin:12px 0}}
.stat{{display:inline-block;background:#111827;border:1px solid #1e293b;padding:16px 24px;border-radius:8px;margin:6px;text-align:center}}
.stat-value{{font-size:24px;font-weight:700;color:#06b6d4}}
.stat-label{{font-size:11px;color:#94a3b8;text-transform:uppercase}}
</style>
</head><body>
<h1>ACHELION CAPITAL — ARMS Backtest Report</h1>
<p>Period: {start_date} to {end_date} | Initial: ${initial_capital:,.0f}</p>

<div>
<div class="stat"><div class="stat-value">{total_ret:+.2f}%</div><div class="stat-label">Total Return</div></div>
<div class="stat"><div class="stat-value">{sharpe:.2f}</div><div class="stat-label">Sharpe Ratio</div></div>
<div class="stat"><div class="stat-value">{max_dd:.2f}%</div><div class="stat-label">Max Drawdown</div></div>
<div class="stat"><div class="stat-value">${final_nav:,.0f}</div><div class="stat-label">Final NAV</div></div>
<div class="stat"><div class="stat-value">{len(trade_ledger)}</div><div class="stat-label">Total Trades</div></div>
</div>
"""

    for chart_name, chart_path in charts.items():
        rel_path = os.path.relpath(chart_path, output_dir)
        html += f'\n<h2>{chart_name.replace("_", " ").title()}</h2>\n'
        html += f'<img src="{rel_path}" alt="{chart_name}">\n'

    html += "\n</body></html>"

    filename = f"ARMS_Report_{start_date}_{end_date}.html"
    filepath = os.path.join(output_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html)
    logger.info(f"HTML report generated: {filepath}")
    return filepath
