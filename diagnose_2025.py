"""Diagnostic: trace 2025 backtest engine behavior."""
import sys, pandas as pd
sys.path.insert(0, 'src')
from simulation.replay_harness import run_backtest as run_simulation_phase2

res = run_simulation_phase2('2025-01-02', '2025-12-31', 500_000_000)
h = res.history.copy()
h['ret'] = h['NAV'].pct_change() * 100

print('=== Weekly snapshots Jan-Apr 2025 ===')
wk = h.resample('W-FRI').last()
sub = wk[(wk.index >= '2025-01-01') & (wk.index <= '2025-04-30')]
print(sub[['NAV', 'Drawdown', 'PDS_Status', 'Regime', 'VIX', 'Equity_Pct',
           'Cash_Pct', 'Equity_Ceiling']].to_string())
print()

sp = h['SPY_Price']
qq = h['QQQ_Price']
spy_dd = (sp / sp.cummax() - 1) * 100
qq_dd = (qq / qq.cummax() - 1) * 100
me = pd.DataFrame({'SPY_DD': spy_dd, 'QQQ_DD': qq_dd,
                   'ARMS_DD': h['Drawdown'] * 100}).resample('ME').min()
print('=== Min drawdown by month (SPY vs QQQ vs ARMS) ===')
print(me.to_string())
print()

print('=== PDS_Status transitions ===')
prev = None
for d, s in h['PDS_Status'].items():
    if s != prev:
        nav = h.loc[d, 'NAV'] / 1e6
        dd = h.loc[d, 'Drawdown'] * 100
        regime = h.loc[d, 'Regime']
        eq = h.loc[d, 'Equity_Pct'] * 100
        print(f"  {d.date()}  {prev} -> {s}  | NAV={nav:.1f}M  DD={dd:+.2f}%  "
              f"Regime={regime}  Eq={eq:.1f}%")
        prev = s
print()

print('=== Regime transitions ===')
prev = None
for d, s in h['Regime'].items():
    if s != prev:
        nav = h.loc[d, 'NAV'] / 1e6
        dd = h.loc[d, 'Drawdown'] * 100
        eq = h.loc[d, 'Equity_Pct'] * 100
        vix = h.loc[d, 'VIX']
        print(f"  {d.date()}  {prev} -> {s}  | NAV={nav:.1f}M  DD={dd:+.2f}%  "
              f"VIX={vix:.1f}  Eq={eq:.1f}%")
        prev = s
print()

# Show daily detail for first 30 days
print('=== First 30 trading days ===')
print(h.head(30)[['NAV', 'Drawdown', 'PDS_Status', 'Regime', 'VIX',
                  'Equity_Pct', 'Crypto_Pct', 'Cash_Pct',
                  'Equity_Ceiling', 'ret']].to_string())
