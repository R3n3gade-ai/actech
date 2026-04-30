"""
Tearsheet Generator — Markdown Performance Report
===================================================
Generates institutional-grade performance tearsheet from backtest results.

Output includes:
  - Summary statistics (return, Sharpe, max DD, Sortino)
  - Monthly return table
  - Regime transition timeline
  - Sleeve allocation breakdown
  - Trade action summary
  - PTRH hedge analysis
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


def generate_tearsheet(
    history: pd.DataFrame,
    start_date: str,
    end_date: str,
    initial_capital: float,
    phase: int = 2,
    trade_ledger: Optional[List[dict]] = None,
    output_dir: str = "SAMPLES",
) -> str:
    """
    Generate a Markdown tearsheet from backtest results.

    Parameters
    ----------
    history : pd.DataFrame — Backtest history (from SimulationResult.history)
    start_date / end_date : str — Date range
    initial_capital : float — Starting NAV
    phase : int — Simulation phase (1 or 2)
    trade_ledger : list — Trade log entries
    output_dir : str — Output directory for tearsheet file

    Returns
    -------
    str — Path to generated tearsheet file
    """
    trade_ledger = trade_ledger or []
    os.makedirs(output_dir, exist_ok=True)

    final_nav = history["NAV"].iloc[-1]
    total_ret = (final_nav / initial_capital - 1) * 100
    daily_ret = history["NAV"].pct_change().dropna()

    # ── Core Statistics ──
    n_years = len(history) / 252
    ann_ret = ((final_nav / initial_capital) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    ann_vol = daily_ret.std() * np.sqrt(252) * 100
    sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252) if daily_ret.std() > 0 else 0
    max_dd = history["Drawdown"].min() * 100

    # Sortino (downside deviation)
    downside = daily_ret[daily_ret < 0]
    downside_dev = downside.std() * np.sqrt(252) if len(downside) > 0 else 0.001
    sortino = (ann_ret / 100) / downside_dev if downside_dev > 0 else 0

    # Calmar (ann return / max drawdown)
    calmar = abs(ann_ret / max_dd) if max_dd != 0 else 0

    # Win rate
    win_days = (daily_ret > 0).sum()
    total_days = len(daily_ret)
    win_rate = win_days / total_days * 100 if total_days > 0 else 0

    # Benchmark comparison
    spy_ret = 0
    qqq_ret = 0
    if "SPY_Price" in history.columns:
        spy_ret = (history["SPY_Price"].iloc[-1] / history["SPY_Price"].iloc[0] - 1) * 100
    if "QQQ_Price" in history.columns:
        qqq_ret = (history["QQQ_Price"].iloc[-1] / history["QQQ_Price"].iloc[0] - 1) * 100

    # ── Monthly Returns Table ──
    monthly_nav = history["NAV"].resample("ME").last()
    monthly_ret = monthly_nav.pct_change().dropna() * 100

    # ── Regime Breakdown ──
    regime_counts = history["Regime"].value_counts()
    regime_pcts = (regime_counts / len(history) * 100).round(1)

    # ── PDS Activation ──
    pds_counts = history["PDS_Status"].value_counts()

    # ── Sleeve Averages ──
    avg_equity = history["Equity_Pct"].mean() * 100
    avg_crypto = history.get("Crypto_Pct", pd.Series(dtype=float)).mean() * 100
    avg_defensive = history.get("Defensive_Pct", pd.Series(dtype=float)).mean() * 100
    avg_cash = history["Cash_Pct"].mean() * 100

    # ── Trade Summary ──
    actions = Counter(t.get("Action", "UNKNOWN") for t in trade_ledger)

    # ── Regime Transitions ──
    transitions = []
    prev_regime = None
    for date, row in history.iterrows():
        if row["Regime"] != prev_regime:
            transitions.append({
                "date": date,
                "from": prev_regime or "START",
                "to": row["Regime"],
                "score": row["Regime_Score"],
                "vix": row["VIX"],
            })
            prev_regime = row["Regime"]

    # ── PTRH Analysis ──
    ptrh_final_cost = 0
    ptrh_final_value = 0
    if "PTRH_Net_Cost" in history.columns:
        ptrh_final_cost = history["PTRH_Net_Cost"].iloc[-1]
        ptrh_final_value = history["PTRH_Value"].iloc[-1]

    # ══════════════════════════════════════════════════════
    # BUILD MARKDOWN
    # ══════════════════════════════════════════════════════
    lines = []
    lines.append(f"# ARMS Backtesting Tearsheet — Phase {phase}")
    lines.append(f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"**Period:** {start_date} → {end_date} ({len(history)} trading days)")
    lines.append(f"**Initial Capital:** ${initial_capital:,.0f}")
    lines.append("")

    # Summary
    lines.append("## Performance Summary")
    lines.append("")
    lines.append("| Metric | ARMS | SPY | QQQ |")
    lines.append("|--------|------|-----|-----|")
    lines.append(f"| Cumulative Return | **{total_ret:+.2f}%** | {spy_ret:+.2f}% | {qqq_ret:+.2f}% |")
    lines.append(f"| Annualized Return | **{ann_ret:+.2f}%** | — | — |")
    lines.append(f"| Max Drawdown | **{max_dd:.2f}%** | — | — |")
    lines.append(f"| Annualized Volatility | {ann_vol:.2f}% | — | — |")
    lines.append(f"| Sharpe Ratio | **{sharpe:.2f}** | — | — |")
    lines.append(f"| Sortino Ratio | {sortino:.2f} | — | — |")
    lines.append(f"| Calmar Ratio | {calmar:.2f} | — | — |")
    lines.append(f"| Win Rate (Daily) | {win_rate:.1f}% | — | — |")
    lines.append(f"| Final NAV | ${final_nav:,.0f} | — | — |")
    lines.append("")

    # Sleeve Allocation
    lines.append("## Average Sleeve Allocation")
    lines.append("")
    lines.append("| Sleeve | Avg Weight | Target (AB) |")
    lines.append("|--------|-----------|-------------|")
    lines.append(f"| Equity | {avg_equity:.1f}% | 58% |")
    if phase >= 2:
        lines.append(f"| Crypto | {avg_crypto:.1f}% | 20% |")
        lines.append(f"| Defensive | {avg_defensive:.1f}% | 14% |")
    lines.append(f"| Cash | {avg_cash:.1f}% | 8% |")
    lines.append("")

    # Regime Breakdown
    lines.append("## Regime Distribution")
    lines.append("")
    lines.append("| Regime | Days | % of Period |")
    lines.append("|--------|------|-------------|")
    for regime in ["RISK_ON", "WATCH", "NEUTRAL", "DEFENSIVE", "CRASH"]:
        days = regime_counts.get(regime, 0)
        pct = regime_pcts.get(regime, 0)
        lines.append(f"| {regime} | {days} | {pct}% |")
    lines.append("")

    # PDS Status
    lines.append("## PDS Activation")
    lines.append("")
    lines.append("| Status | Days | % of Period |")
    lines.append("|--------|------|-------------|")
    for status in ["NORMAL", "WARNING", "DELEVERAGE_1", "DELEVERAGE_2"]:
        days = pds_counts.get(status, 0)
        pct = round(days / len(history) * 100, 1)
        lines.append(f"| {status} | {days} | {pct}% |")
    lines.append("")

    # PTRH Hedge Analysis
    if phase >= 2:
        lines.append("## PTRH Tail Hedge Analysis")
        lines.append("")
        lines.append(f"- **Net Hedge Cost:** ${ptrh_final_cost:,.0f}")
        lines.append(f"- **Final Hedge Value:** ${ptrh_final_value:,.0f}")
        lines.append(f"- **Hedge Cost as % of NAV:** {ptrh_final_cost / initial_capital * 100:.2f}%")
        lines.append("")

    # SLOF Analysis
    if phase >= 2 and "SLOF_Active" in history.columns:
        slof_days = history["SLOF_Active"].sum()
        lines.append("## SLOF Leverage")
        lines.append("")
        lines.append(f"- **Active Days:** {slof_days} ({slof_days / len(history) * 100:.1f}%)")
        lines.append(f"- **Avg Leverage (when active):** {history.loc[history['SLOF_Active'], 'SLOF_Leverage'].mean():.2f}x" if slof_days > 0 else "- **Avg Leverage:** N/A")
        lines.append("")

    # Monthly Returns
    lines.append("## Monthly Returns")
    lines.append("")

    # Group by year
    years = sorted(set(dt.year for dt in monthly_ret.index))
    lines.append("| Year | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep | Oct | Nov | Dec | **Annual** |")
    lines.append("|------|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|-----|------------|")

    for year in years:
        row_data = [str(year)]
        year_rets = []
        for month in range(1, 13):
            matching = [r for dt, r in monthly_ret.items()
                        if dt.year == year and dt.month == month]
            if matching:
                val = matching[0]
                year_rets.append(val)
                color = "+" if val >= 0 else ""
                row_data.append(f"{color}{val:.1f}%")
            else:
                row_data.append("—")
        ann = sum(year_rets) if year_rets else 0
        row_data.append(f"**{ann:+.1f}%**")
        lines.append("| " + " | ".join(row_data) + " |")
    lines.append("")

    # Regime Transitions
    lines.append("## Regime Transition Timeline")
    lines.append("")
    lines.append("| Date | From | To | Score | VIX |")
    lines.append("|------|------|----|-------|-----|")
    for t in transitions[:50]:  # Cap at 50
        date_str = t["date"].strftime("%Y-%m-%d") if hasattr(t["date"], "strftime") else str(t["date"])
        lines.append(f"| {date_str} | {t['from']} | {t['to']} | {t['score']:.3f} | {t['vix']:.1f} |")
    lines.append("")

    # Trade Summary
    lines.append("## Trade Summary")
    lines.append("")
    lines.append(f"**Total Trades:** {len(trade_ledger)}")
    lines.append("")
    lines.append("| Action | Count |")
    lines.append("|--------|-------|")
    for action, count in sorted(actions.items()):
        lines.append(f"| {action} | {count} |")
    lines.append("")

    # LAEP Slippage
    if "Total_Slippage" in history.columns:
        total_slip = history["Total_Slippage"].iloc[-1]
        lines.append(f"**Total LAEP Slippage:** ${total_slip:,.0f} ({total_slip / initial_capital * 100:.3f}% of initial capital)")
        lines.append("")

    # ── Write to file ──
    filename = f"ARMS_Tearsheet_Phase{phase}_{start_date}_{end_date}.md"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"Tearsheet generated: {filepath}")
    return filepath


def generate_trade_ledger_csv(
    trade_ledger: List[dict],
    output_dir: str = "SAMPLES",
    filename: Optional[str] = None,
) -> str:
    """Export trade ledger to CSV."""
    os.makedirs(output_dir, exist_ok=True)

    if not filename:
        filename = f"ARMS_Trade_Ledger_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

    filepath = os.path.join(output_dir, filename)
    df = pd.DataFrame(trade_ledger)
    df.to_csv(filepath, index=False)

    logger.info(f"Trade ledger exported: {filepath} ({len(trade_ledger)} trades)")
    return filepath
