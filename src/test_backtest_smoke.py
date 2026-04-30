"""Quick smoke test for both simulation phases."""
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)

print("=" * 70)
print("PHASE 1 SMOKE TEST: QQQ static book, 2020 COVID crash")
print("=" * 70)

from simulation.historical_engine_phase1 import run_simulation_phase1

res1 = run_simulation_phase1("2020-01-02", "2020-04-30", initial_capital=10_000_000)
h1 = res1.history

final_nav = h1["NAV"].iloc[-1]
ret = (final_nav / 10_000_000 - 1) * 100
maxdd = h1["Drawdown"].min() * 100

print(f"\nResults:")
print(f"  Trading days:  {len(h1)}")
print(f"  Final NAV:     ${final_nav:,.0f}")
print(f"  Return:        {ret:+.2f}%")
print(f"  Max Drawdown:  {maxdd:.2f}%")
print(f"  Trades:        {len(res1.trade_ledger)}")
print(f"\nRegime breakdown:")
for regime in h1["Regime"].unique():
    days = (h1["Regime"] == regime).sum()
    print(f"  {regime}: {days} days")

if res1.trade_ledger:
    print(f"\nSample trades:")
    for t in res1.trade_ledger[:5]:
        print(f"  {t['Date']} {t['Action']} {t['Ticker']} ${t['Value']:,.0f} [{t['Trigger']}]")

print("\n" + "=" * 70)
print("PHASE 2 SMOKE TEST: Full Arch AB, 2020 H1 (6 months)")
print("=" * 70)

from simulation.historical_engine_phase2 import run_simulation_phase2

res2 = run_simulation_phase2(
    "2020-01-02", "2020-06-30",
    initial_capital=10_000_000,
    use_individual_tickers=False,  # QQQ proxy for speed
)
h2 = res2.history

final_nav2 = h2["NAV"].iloc[-1]
ret2 = (final_nav2 / 10_000_000 - 1) * 100
maxdd2 = h2["Drawdown"].min() * 100

print(f"\nResults:")
print(f"  Trading days:  {len(h2)}")
print(f"  Final NAV:     ${final_nav2:,.0f}")
print(f"  Return:        {ret2:+.2f}%")
print(f"  Max Drawdown:  {maxdd2:.2f}%")
print(f"  Trades:        {len(res2.trade_ledger)}")
print(f"  PTRH Net Cost: ${h2['PTRH_Net_Cost'].iloc[-1]:,.0f}")
print(f"  PERM Income:   ${h2['PERM_Income'].iloc[-1]:,.0f}")

print(f"\nRegime breakdown:")
for regime in h2["Regime"].unique():
    days = (h2["Regime"] == regime).sum()
    print(f"  {regime}: {days} days")

print(f"\nSleeve averages:")
print(f"  Equity:    {h2['Equity_Pct'].mean()*100:.1f}%")
print(f"  Crypto:    {h2['Crypto_Pct'].mean()*100:.1f}%")
print(f"  Defensive: {h2['Defensive_Pct'].mean()*100:.1f}%")
print(f"  Cash:      {h2['Cash_Pct'].mean()*100:.1f}%")

if res2.trade_ledger:
    print(f"\nSample trades:")
    for t in res2.trade_ledger[:5]:
        action = t.get("Action", "")
        ticker = t.get("Ticker", "")
        val = t.get("Value", 0)
        trigger = t.get("Trigger", "")
        print(f"  {t['Date']} {action} {ticker} ${val:,.0f} [{trigger}]")

print("\n" + "=" * 70)
print("ALL SMOKE TESTS PASSED")
print("=" * 70)
