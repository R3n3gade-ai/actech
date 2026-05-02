"""Verify Oct 10 advance warning per Addendum 7 §6: composite > 0.50 by Oct 6."""
import pandas as pd
import numpy as np
from simulation.replay_harness import run_backtest
from modules.deleveraging_risk import DeleveragingRisk

r = run_backtest('2025-09-15', '2025-10-31', 500_000_000.0)
h = r.history

print('\n=== Regime + Score Sep 25 - Oct 15, 2025 (Addendum 7 §6 validation) ===')
window = h.loc['2025-09-25':'2025-10-15',
               ['Regime', 'Regime_Score', 'Equity_Pct', 'Crypto_Pct',
                'Defensive_Pct', 'Cash_Pct', 'Drawdown']]
print(window.to_string())

# Manually compute deleveraging score with all available layers for each day
print('\n=== Standalone deleveraging_risk score (canonical 5-layer weighted sum) ===')
from simulation.data_loader import load_historical_data
data = load_historical_data('2025-09-15', '2025-10-31')
m = data.macro_signals
delev = DeleveragingRisk()
btc = data.crypto_prices['BTC']
qqq = data.benchmark_prices['QQQ']

results = []
for date in m.loc['2025-09-25':'2025-10-15'].index:
    skew = float(m.loc[date, 'CBOE_SKEW']) if 'CBOE_SKEW' in m.columns else None
    if skew is not None and np.isnan(skew):
        skew = None
    margin_3m = float(m.loc[date, 'MARGIN_DEBT_3M_GROWTH_PCT']) if 'MARGIN_DEBT_3M_GROWTH_PCT' in m.columns else 0.0
    margin_mom = float(m.loc[date, 'MARGIN_DEBT_MOM_CHANGE_PCT']) if 'MARGIN_DEBT_MOM_CHANGE_PCT' in m.columns else 0.0
    funding = float(m.loc[date, 'BTC_FUNDING_RATE_ANN']) if 'BTC_FUNDING_RATE_ANN' in m.columns else None
    if funding is not None and np.isnan(funding): funding = None
    funding_delta = float(m.loc[date, 'BTC_FUNDING_RATE_8H_DELTA']) if 'BTC_FUNDING_RATE_8H_DELTA' in m.columns else None
    if funding_delta is not None and np.isnan(funding_delta): funding_delta = None
    # OI fragility (Addendum 7 §3.2)
    oi_cur = float(m.loc[date, 'BTC_OI_CURRENT']) if 'BTC_OI_CURRENT' in m.columns else None
    if oi_cur is not None and np.isnan(oi_cur): oi_cur = None
    oi_high = float(m.loc[date, 'BTC_OI_30D_HIGH']) if 'BTC_OI_30D_HIGH' in m.columns else None
    if oi_high is not None and np.isnan(oi_high): oi_high = None
    oi_24h = float(m.loc[date, 'BTC_OI_24H_CHANGE_PCT']) if 'BTC_OI_24H_CHANGE_PCT' in m.columns else 0.0
    if np.isnan(oi_24h): oi_24h = 0.0
    # 5-day BTC/QQQ corr ending today + BTC 24h price change %
    end_idx = btc.index.get_indexer([date])[0]
    if end_idx >= 1:
        prev_btc = float(btc.iloc[end_idx - 1])
        cur_btc = float(btc.iloc[end_idx])
        price_24h = ((cur_btc - prev_btc) / prev_btc * 100.0) if prev_btc > 0 else 0.0
    else:
        price_24h = 0.0
    if end_idx >= 6:
        b = btc.iloc[end_idx-5:end_idx+1].pct_change().dropna()
        q = qqq.iloc[end_idx-5:end_idx+1].pct_change().dropna()
        common = b.index.intersection(q.index)
        corr = b.loc[common].corr(q.loc[common]) if len(common) >= 4 else None
        if corr is not None and np.isnan(corr): corr = None
    else:
        corr = None
    inputs = {
        'cboe_skew_index': skew,
        'margin_debt_3m_growth_pct': margin_3m,
        'margin_debt_mom_change_pct': margin_mom,
        'btc_qqq_corr_5d': corr,
        'btc_funding_rate_ann': funding,
        'btc_funding_rate_8h_delta': funding_delta,
        'btc_oi_current': oi_cur,
        'btc_oi_30d_high': oi_high,
        'btc_oi_24h_change_pct': oi_24h,
        'btc_price_24h_change_pct': price_24h,
    }
    score = delev.score(inputs)
    results.append({'Date': date, 'SKEW': skew, 'FundAnn': funding,
                    'OI_Cur': oi_cur, 'OI_24h': oi_24h,
                    'Margin3M': margin_3m, 'BTC_QQQ_5d': corr, 'DelevScore': score})
df = pd.DataFrame(results).set_index('Date')
print(df.to_string())
print()
print(f"Spec target (Addendum 7 §6): score > 0.50 by Oct 6, 2025")
print(f"Oct 6 actual score: {df.loc['2025-10-06', 'DelevScore']:.3f}")
print(f"Max in window:      {df['DelevScore'].max():.3f}")
