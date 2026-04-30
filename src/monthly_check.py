import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from simulation.historical_engine_phase2 import run_simulation_phase2

res = run_simulation_phase2('2024-01-02','2025-08-29', 500_000_000, use_individual_tickers=True)
h = res.history
monthly = h.resample('ME').last()

print(f"{'Month':>7} | {'NAV':>10} | {'MonRet':>7} | {'CumRet':>7} | {'DD':>7} | {'Equity':>6} | {'Crypto':>6} | {'Def':>5} | {'Cash':>5} | {'Regime':>10} | {'PDS':>12}")
print("-" * 120)

prev_nav = 500_000_000
for idx, m in monthly.iterrows():
    nav = m['NAV']
    mon_ret = (nav / prev_nav - 1) * 100
    cum_ret = (nav / 500_000_000 - 1) * 100
    dd = m['Drawdown'] * 100
    eq = m['Equity_Pct'] * 100
    cr = m['Crypto_Pct'] * 100
    df = m['Defensive_Pct'] * 100
    ca = m['Cash_Pct'] * 100
    regime = m['Regime']
    pds = m['PDS_Status']
    print(f"{idx.strftime('%Y-%m'):>7} | {nav/1e6:>9.1f}M | {mon_ret:>+6.1f}% | {cum_ret:>+6.1f}% | {dd:>+6.1f}% | {eq:>5.1f}% | {cr:>5.1f}% | {df:>4.1f}% | {ca:>4.1f}% | {regime:>10} | {pds:>12}")
    prev_nav = nav

# Year by year
for year in [2024, 2025]:
    year_data = h[h.index.year == year]
    if len(year_data) == 0:
        continue
    y_start = year_data['NAV'].iloc[0]
    y_end = year_data['NAV'].iloc[-1]
    y_ret = (y_end / y_start - 1) * 100
    print(f"\n  {year}: {y_ret:+.1f}%  (start {y_start/1e6:.1f}M -> end {y_end/1e6:.1f}M)")
