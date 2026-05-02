import pandas as pd

h = pd.read_csv('SAMPLES/history_2025-01-02_2025-12-31.csv', index_col=0, parse_dates=True)
t = pd.read_csv('SAMPLES/trade_ledger_2025-01-02_2025-12-31.csv', index_col=0, parse_dates=True)

start_nav = h['NAV'].iloc[0]
end_nav = h['NAV'].iloc[-1]
total_ret = (end_nav / start_nav - 1) * 100
max_dd = h['Drawdown'].min() * 100
trades = len(t)

daily_ret = h['NAV'].pct_change().dropna()
import numpy as np
sharpe = (daily_ret.mean() / daily_ret.std()) * np.sqrt(252)

print("=== ARMS 2025 FULL YEAR BACKTEST ===")
print("Capital:       ${:,.0f}".format(start_nav))
print("End NAV:       ${:,.0f}".format(end_nav))
print("Total Return:  {:+.2f}%".format(total_ret))
print("Sharpe Ratio:  {:.2f}".format(sharpe))
print("Max Drawdown:  {:.2f}%".format(max_dd))
print("Trades:        {}".format(trades))

print("")
print("--- Monthly Returns ---")
monthly = h['NAV'].resample('ME').last()
monthly_ret = monthly.pct_change().dropna() * 100
for d, r in monthly_ret.items():
    bar = "+" * int(abs(r) / 0.5) if r >= 0 else "-" * int(abs(r) / 0.5)
    sign = "+" if r >= 0 else ""
    print("  {:>8}  {}{:.2f}%  {}".format(d.strftime("%b %Y"), sign, r, bar))

print("")
print("--- Quarterly Returns ---")
q = h['NAV'].resample('QE').last()
qret = q.pct_change().dropna() * 100
for d, r in qret.items():
    qnum = (d.month - 1) // 3 + 1
    print("  Q{} {}  {:+.2f}%".format(qnum, d.year, r))

print("")
print("--- Days by Regime ---")
for r, cnt in h['Regime'].value_counts().items():
    print("  {:20s}  {} days".format(r, cnt))

print("")
print("--- Risk Stats ---")
dd_pct = h['Drawdown'] * 100
in_dd5 = (dd_pct < -5).sum()
print("  Days >5% drawdown:   {}".format(in_dd5))
print("  Worst drawdown:      {:.2f}%".format(dd_pct.min()))
avg_vix = h['VIX'].mean()
print("  Avg VIX:             {:.1f}".format(avg_vix))
slof_days = (h['SLOF_Active'] == 'True').sum()
print("  SLOF active days:    {}".format(slof_days))
ptrh_max = h['PTRH_Notional'].astype(float).max()
print("  PTRH max notional:   ${:,.0f}".format(ptrh_max))

print("")
print("--- Allocation (avg %) ---")
for col in ['Equity_Pct', 'Crypto_Pct', 'Defensive_Pct', 'Cash_Pct']:
    avg = h[col].astype(float).mean() * 100
    print("  {:20s}  {:.1f}%".format(col, avg))
