import pandas as pd
from simulation.replay_harness import run_backtest

r = run_backtest('2025-09-15', '2025-10-31', 500_000_000.0)
h = r.history
print('\n=== Portfolio state Oct 1 - Oct 20, 2025 ===')
window = h.loc['2025-10-01':'2025-10-20',
               ['Regime', 'Regime_Score', 'Equity_Pct', 'Crypto_Pct',
                'Defensive_Pct', 'Cash_Pct', 'Drawdown', 'BTC_Price', 'QQQ_Price']]
print(window.to_string())

print('\n=== Trades Oct 1 - Oct 12, 2025 ===')
trades = [t for t in r.trade_ledger
          if t['Date'] >= '2025-10-01' and t['Date'] <= '2025-10-12']
print(f'Count: {len(trades)}')
for t in trades:
    tk = t.get('Ticker', '-')
    val = t.get('Value', 0)
    trig = str(t.get('Trigger', ''))[:60]
    mod = t.get('Module', '')
    print(f"  {t['Date']} {t['Action']:14s} {tk:6s} val={val:>14,.0f}  [{mod}] {trig}")

print('\n=== BTC/QQQ Oct 9 vs Oct 10 ===')
if '2025-10-09' in h.index.astype(str).tolist():
    pass
oct9 = h.loc['2025-10-09']
oct10 = h.loc['2025-10-10'] if '2025-10-10' in h.index.astype(str).tolist() else None
print(f"Oct 9  BTC={oct9['BTC_Price']:,.0f}  QQQ={oct9['QQQ_Price']:.2f}  NAV={oct9['NAV']:,.0f}  Crypto%={oct9['Crypto_Pct']:.1%}")
if oct10 is not None:
    print(f"Oct 10 BTC={oct10['BTC_Price']:,.0f}  QQQ={oct10['QQQ_Price']:.2f}  NAV={oct10['NAV']:,.0f}  Crypto%={oct10['Crypto_Pct']:.1%}")
    btc_drop = (oct10['BTC_Price']/oct9['BTC_Price']-1)*100
    nav_drop = (oct10['NAV']/oct9['NAV']-1)*100
    print(f"  -> BTC move: {btc_drop:+.2f}%   Portfolio NAV move: {nav_drop:+.2f}%")
