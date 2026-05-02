"""Diagnose why ARMS held RISK_ON through Oct 10 2025 crypto dump."""
import pandas as pd
from simulation.replay_harness import run_backtest
from simulation.data_loader import load_historical_data as load_backtest_data

# Print raw macro signals leading into Oct 10
print('=== Raw macro signals (Sep 25 - Oct 15, 2025) ===')
data = load_backtest_data('2025-09-15', '2025-10-31')
m = data.macro_signals.loc['2025-09-25':'2025-10-15'].copy()
cols_present = [c for c in ['VIX','TNX_Yield','HY_Spread','PMI','T10Y2Y',
                            'MARGIN_DEBT_QOQ_PCT','CBOE_SKEW'] if c in m.columns]
print(m[cols_present].to_string())

print('\n=== BTC vs QQQ pre-Oct-10 (5d returns) ===')
btc = data.crypto_prices['BTC-USD'].loc['2025-09-25':'2025-10-15']
qqq = data.benchmark_prices['QQQ'].loc['2025-09-25':'2025-10-15']
df = pd.DataFrame({'BTC': btc, 'QQQ': qqq})
df['BTC_5d_pct'] = btc.pct_change(5) * 100
df['QQQ_5d_pct'] = qqq.pct_change(5) * 100
print(df.to_string())

print('\n=== Run backtest, print regime score components Oct 1-12 ===')
r = run_backtest('2025-09-15', '2025-10-31', 500_000_000.0)
h = r.history
window = h.loc['2025-10-01':'2025-10-15',
               ['Regime', 'Regime_Score', 'VIX', 'HY_Spread', 'TNX_Yield', 'PMI']]
print(window.to_string())
