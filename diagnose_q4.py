"""Q4 2025 forensic decomposition."""
import sys
sys.path.insert(0, "src")
import pandas as pd
from simulation.replay_harness import run_backtest

res = run_backtest(start_date="2025-01-02", end_date="2025-12-31",
                   initial_capital=500_000_000)
h = pd.DataFrame(res.history)
h.index = pd.to_datetime(h.index)
trades = pd.DataFrame(res.trade_ledger)
trades["Date"] = pd.to_datetime(trades["Date"])

q4 = h.loc["2025-10-31":"2025-12-31"]
print("=== Q4 (Oct 31 -> Dec 31) ===")
print(f"SPY  : {q4['SPY_Price'].iloc[-1]/q4['SPY_Price'].iloc[0]-1:+.2%}")
print(f"QQQ  : {q4['QQQ_Price'].iloc[-1]/q4['QQQ_Price'].iloc[0]-1:+.2%}")
print(f"ARMS : {q4['NAV'].iloc[-1]/q4['NAV'].iloc[0]-1:+.2%}")

print()
print("=== Q4 trade volume by module ===")
q4t = trades[trades["Date"] >= "2025-10-31"]
print(q4t.groupby("Module")[["Value", "Slippage"]].agg(["count", "sum"]).round(0).to_string())
print()
print(f"Q4 total slippage: ${q4t['Slippage'].sum():,.0f}")
print(f"Q4 total turnover: ${q4t['Value'].sum():,.0f}")
print()

print("=== Q4 PDS state transitions (status changes) ===")
last = None
for d, row in q4.iterrows():
    if row["PDS_Status"] != last:
        print(f"  {d.date()} {last} -> {row['PDS_Status']:15s} | NAV=${row['NAV']/1e6:.1f}M  DD={row['Drawdown']:.2%}  Eq={row['Equity_Pct']:.1%}")
        last = row["PDS_Status"]

print()
print("=== Q4 BEFORE/AFTER each PDS down-trigger ===")
for d, row in q4.iterrows():
    if row["PDS_Status"] in ("DELEVERAGE_1", "DELEVERAGE_2") and last != row["PDS_Status"]:
        pass

# Realized whipsaw cost: trades where we sold then bought back same ticker within 7 days
print()
print("=== Equity sleeve trim/redeploy round-trips Q4 ===")
eqtrades = q4t[~q4t["Ticker"].isin(["BTC", "SGOL", "DBMF", "SGOV", "STRC", "TLT"])].copy()
sells = eqtrades[eqtrades["Action"] == "SELL"].copy()
buys = eqtrades[eqtrades["Action"] == "BUY"].copy()
total_round_trip_loss = 0.0
for _, sell in sells.iterrows():
    matched_buys = buys[
        (buys["Ticker"] == sell["Ticker"]) &
        (buys["Date"] > sell["Date"]) &
        (buys["Date"] <= sell["Date"] + pd.Timedelta(days=14))
    ]
    if len(matched_buys) > 0:
        first_buy = matched_buys.iloc[0]
        # Cost = (buy_price - sell_price) * shares (approx, since shares may differ)
        if first_buy["Price"] > sell["Price"]:
            extra = (first_buy["Price"] - sell["Price"]) * min(sell["Shares"], first_buy["Shares"])
            total_round_trip_loss += extra
print(f"Approx round-trip whipsaw loss (sells immediately re-bought higher within 14d): ${total_round_trip_loss:,.0f}")
