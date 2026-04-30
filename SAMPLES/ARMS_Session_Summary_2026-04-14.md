# ARMS System Check-Up Summary
## April 14, 2026

---

## What We Did

We ran a thorough check on the ARMS backtesting system to make sure it's doing what it's supposed to do - following the original design documents, not making stuff up on its own. Think of it like taking a car to the mechanic to make sure all the parts match what the manufacturer intended.

---

## What We Found

### The Big One: Bitcoin Was Being Held Back

The system was automatically selling Bitcoin every time it grew more than 3% above its target weight (20% of the portfolio). So if Bitcoin had a great week and climbed to 23% of the portfolio, the system would sell the extra and push it back down to 20%.

**The problem:** This was never part of the original design. It was added as a "safety measure" by a previous build session, but MJ's system explicitly runs with **no BTC dampening** - meaning Bitcoin is supposed to ride freely. When Bitcoin is on a hot streak, you want to let it run, not clip its wings every few days.

**What we did:** Removed it entirely. Bitcoin now floats naturally - it only gets sold in a genuine crisis (VIX above 45, which means markets are in panic mode).

**Did it matter?** Yes:
- Bitcoin's average portfolio weight went from **16.9% to 20.6%** - right where it should be
- The system was making about 40 unnecessary Bitcoin sell trades that are now eliminated
- For the Jan 2024 to Aug 2025 period, returns went up slightly (+105.2% vs +103.9%)

---

### We Also Tested a Smarter Version (But Didn't Keep It)

We tried a middle-ground approach: let Bitcoin run freely during calm markets, but take some profits when the system detects rising risk (regime shifts from "all clear" to "watch mode"). The idea was to lock in some gains before potential trouble.

**Results:** Nearly identical to just letting Bitcoin float. In this particular time period, there weren't enough risk events to make a meaningful difference. We kept the simpler approach (just let it ride) since that matches the original design.

---

### Data Pipeline Check: Credit Spreads

We traced how credit spread data (a measure of how nervous the bond market is) flows through the system from start to finish. The backtester uses a proxy based on two bond ETFs (HYG and LQD) and converts it to the same units the live system gets from the Federal Reserve database.

**Result:** The math checks out. Both the live system and the backtester deliver the same type of numbers to the scoring engine. No issues here.

---

### Stock Coverage Check: ARM Holdings

ARM went public in September 2023, so we verified it has full price data for our test period (January 2024 onward). It does - zero gaps, zero missing days. ARM is properly included in the portfolio and contributing to returns.

---

## The Numbers

### January 2024 - August 2025 (Comparison Period with MJ)

| What | Value |
|------|-------|
| Starting Capital | $500,000,000 |
| Ending Value | $1,025,829,184 |
| Total Return | **+105.2%** |
| Risk-Adjusted Return (Sharpe) | 1.65 |
| Worst Drawdown | -26.2% |
| Total Trades | 275 |
| Average Bitcoin Weight | 20.6% |
| Average Stock Weight | 57.3% |

### January 2024 - April 14, 2026 (Current)

| What | Value |
|------|-------|
| Starting Capital | $500,000,000 |
| Ending Value | $1,155,779,666 |
| Total Return | **+131.2%** |
| Risk-Adjusted Return (Sharpe) | 1.41 |
| Worst Drawdown | -26.2% |
| Total Trades | 410 |
| Average Bitcoin Weight | 20.3% |
| Average Stock Weight | 58.8% |

---

## Could Any of This Affect Accuracy?

### Removing the Bitcoin cap - LOW concern
This actually **improves** accuracy because the original system was never supposed to have it. We're now closer to how the system was designed to work. Bitcoin sits right at its 20% target instead of being artificially suppressed to 17%.

### Credit spread data - NO concern
The numbers flow correctly through the entire pipeline. Verified against both the live production path and the backtester path. Same units, same formulas.

### Stock data - NO concern
All 12 stocks in the portfolio have clean, complete data for the test periods. No gaps, no missing prices.

### Trade count - WORTH WATCHING
About 45% of all trades (186 out of 410) are covered call writes from the income engine (PERM). These aren't stock buys/sells - they're options overlays that generate premium income. If you strip those out, the system makes about 1 actual stock trade every 2.5 trading days, which is reasonable for an active strategy managing 12 positions through multiple market regimes.

---

## Bottom Line

The system is now more closely aligned with the original design. The main fix was removing an artificial Bitcoin selling rule that was never part of the spec. Everything else - the risk scoring, the data pipelines, the stock coverage - checked out clean. The returns are strong, the risk management is functioning, and the portfolio weights are hitting their targets.

---

*Full PDF reports for both periods are available in the SAMPLES folder.*
