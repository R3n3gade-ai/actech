import sys
import pandas as pd
from simulation.replay_harness import run_backtest

start, end, cap = sys.argv[1], sys.argv[2], float(sys.argv[3])
r = run_backtest(start, end, cap)
h = r.history
print(f"\n===== BACKTEST {start} -> {end}  (capital ${cap:,.0f}) =====")
print(f"Days: {len(h)}")
print(f"Final NAV: ${h['NAV'].iloc[-1]:,.0f}")
print(f"Total return: {(h['NAV'].iloc[-1]/cap-1)*100:+.2f}%")
print(f"Max DD: {h['Drawdown'].min()*100:.2f}%")
dr = h['NAV'].pct_change().dropna()
sharpe = (dr.mean()/dr.std())*(252**0.5) if dr.std() > 0 else 0.0
print(f"Sharpe: {sharpe:.2f}")
print(f"Trades: {len(r.trade_ledger)}")
print()
print("Regime distribution:")
print(h['Regime'].value_counts())
changes = (h['Regime'] != h['Regime'].shift()).sum() - 1
print(f"\nRegime changes: {changes}")
print(f"Regime score range: {h['Regime_Score'].min():.3f} to {h['Regime_Score'].max():.3f}")
print()
print("FEM_Status distribution:")
print(h['FEM_Status'].value_counts())
print()
print("SSL_Worst_Scenario frequency:")
print(h['SSL_Worst_Scenario'].value_counts())
print()
# Annual breakdown
h2 = h.copy()
h2['Year'] = pd.to_datetime(h2.index).year
print("Annual returns:")
for yr, grp in h2.groupby('Year'):
    nav_start = grp['NAV'].iloc[0]
    nav_end = grp['NAV'].iloc[-1]
    ret = (nav_end/nav_start - 1) * 100
    dd = grp['Drawdown'].min() * 100
    print(f"  {yr}: {ret:+7.2f}%  Max DD: {dd:6.2f}%  Regimes: {grp['Regime'].nunique()}")
