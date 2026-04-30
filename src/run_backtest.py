"""
ARMS Backtest Runner — Phase 2 Full Architecture AB
=====================================================
Runs the complete 3-year backtest (2020-2022) with all production
modules wired, then generates tearsheet and PDF report.

Usage:
    cd src/
    py run_backtest.py                          # Full 3-year run
    py run_backtest.py --start 2022-01-01       # Custom date range
    py run_backtest.py --quick                  # Quick 6-month test
"""
import argparse
import logging
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(message)s",
    datefmt="%H:%M:%S",
)

from simulation.historical_engine_phase1 import run_simulation_phase1
from simulation.historical_engine_phase2 import run_simulation_phase2
from simulation.tearsheet import generate_tearsheet


def main():
    parser = argparse.ArgumentParser(description="ARMS Backtesting Engine")
    parser.add_argument("--phase", type=int, default=2, choices=[1, 2],
                        help="Phase 1 (static QQQ) or Phase 2 (full Architecture AB)")
    parser.add_argument("--start", default="2020-01-02", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2022-12-30", help="End date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=500_000_000,
                        help="Initial capital (default: $500M)")
    parser.add_argument("--quick", action="store_true",
                        help="Quick test: 2020-01-02 to 2020-06-30")
    parser.add_argument("--individual", action="store_true", default=True,
                        help="Use individual equity tickers (default: True)")
    parser.add_argument("--output", default="SAMPLES",
                        help="Output directory for tearsheet")
    parser.add_argument("--pdf", action="store_true",
                        help="Also generate PDF report")
    args = parser.parse_args()

    if args.quick:
        args.start = "2020-01-02"
        args.end = "2020-06-30"

    print("=" * 70)
    print(f"ARMS BACKTESTER — Phase {args.phase}")
    print(f"Period: {args.start} → {args.end}")
    print(f"Capital: ${args.capital:,.0f}")
    print("=" * 70)

    # Run simulation
    if args.phase == 1:
        res = run_simulation_phase1(args.start, args.end, args.capital)
    else:
        res = run_simulation_phase2(
            args.start, args.end, args.capital,
            use_individual_tickers=args.individual,
        )

    h = res.history

    # Print summary
    final_nav = h["NAV"].iloc[-1]
    initial = h["NAV"].iloc[0]
    total_ret = (final_nav / initial - 1) * 100
    max_dd = h["Drawdown"].min() * 100

    daily_ret = h["NAV"].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * (252 ** 0.5) if daily_ret.std() > 0 else 0

    print(f"\n{'─' * 50}")
    print(f"PERFORMANCE SUMMARY")
    print(f"{'─' * 50}")
    print(f"  Final NAV:        ${final_nav:,.0f}")
    print(f"  Cumulative Return: {total_ret:+.2f}%")
    print(f"  Sharpe Ratio:      {sharpe:.2f}")
    print(f"  Max Drawdown:      {max_dd:.2f}%")
    print(f"  Trade Count:       {len(res.trade_ledger)}")

    if args.phase == 2:
        print(f"\n  Avg Equity:    {h['Equity_Pct'].mean()*100:.1f}%")
        print(f"  Avg Crypto:    {h['Crypto_Pct'].mean()*100:.1f}%")
        print(f"  Avg Defensive: {h['Defensive_Pct'].mean()*100:.1f}%")
        print(f"  Avg Cash:      {h['Cash_Pct'].mean()*100:.1f}%")

        if "PTRH_Net_Cost" in h.columns:
            print(f"  PTRH Net Cost: ${h['PTRH_Net_Cost'].iloc[-1]:,.0f}")
        if "PERM_Income" in h.columns:
            print(f"  PERM Income:   ${h['PERM_Income'].iloc[-1]:,.0f}")
        if "Total_Slippage" in h.columns:
            print(f"  LAEP Slippage: ${h['Total_Slippage'].iloc[-1]:,.0f}")

        if "SLOF_Active" in h.columns:
            print(f"  SLOF Active:   {h['SLOF_Active'].sum()} days")

    # PDS breakdown
    if "PDS_Status" in h.columns:
        print(f"\n  PDS Status Breakdown:")
        for status in ["NORMAL", "WARNING", "DELEVERAGE_1", "DELEVERAGE_2"]:
            count = (h["PDS_Status"] == status).sum()
            if count > 0:
                print(f"    {status}: {count} days ({count/len(h)*100:.1f}%)")

    # Regime breakdown
    print(f"\n  Regime Breakdown:")
    for regime in h["Regime"].unique():
        days = (h["Regime"] == regime).sum()
        print(f"    {regime}: {days} days ({days/len(h)*100:.1f}%)")

    # Trade breakdown
    if res.trade_ledger:
        actions = Counter(t["Action"] for t in res.trade_ledger)
        print(f"\n  Trade Breakdown:")
        for action, count in sorted(actions.items()):
            print(f"    {action}: {count}")

    # Generate tearsheet
    output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", args.output)
    os.makedirs(output_dir, exist_ok=True)

    generate_tearsheet(
        h, args.start, args.end, args.capital,
        phase=args.phase,
        trade_ledger=res.trade_ledger,
        output_dir=output_dir,
    )
    print(f"\nTearsheet saved to {args.output}/")

    # Optional PDF
    if args.pdf:
        from simulation.pdf_report import generate_pdf_report
        pdf_path = generate_pdf_report(
            h, res.trade_ledger, args.start, args.end,
            initial_capital=args.capital, output_dir=output_dir,
        )
        print(f"PDF report: {pdf_path}")

    print(f"\n{'=' * 70}")
    print("BACKTEST COMPLETE")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
