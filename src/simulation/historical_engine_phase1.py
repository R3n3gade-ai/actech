"""
Phase 1 Historical Engine — Static Book Simulation
====================================================
Proves ARAS + PDS protect a static portfolio during known crashes.

Mechanics:
  - Initialize 100% QQQ (or custom single asset) on start date
  - Daily loop: Macro Compass → ARAS → PDS → deleverage if triggered
  - No rebalancing, no hedging, no intelligence layer
  - Pure regime detection + drawdown protection validation

Expected Result (2020 COVID test):
  System detects VIX spike → CRASH → deleverage to 15% ceiling →
  drastically outperforms buy-and-hold in max drawdown.
"""

import logging
import sys
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Add src/ to path for production module imports
_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src not in sys.path:
    sys.path.insert(0, _src)

from simulation.data_loader import load_historical_data
from data_feeds.interfaces import SignalRecord

logger = logging.getLogger(__name__)


@dataclass
class SimulationResult:
    """Container for simulation output."""
    history: pd.DataFrame
    trade_ledger: List[dict] = field(default_factory=list)
    config: dict = field(default_factory=dict)


def _make_signal_record(signal_type: str, value: float) -> SignalRecord:
    """Create a SignalRecord compatible with production macro_compass."""
    return SignalRecord(
        ticker="MACRO",
        signal_type=signal_type,
        value=value,
        raw_value=value,
        source="BACKTEST",
        timestamp="",
        cost_tier="FALLBACK",
    )


def run_simulation_phase1(
    start_date: str = "2020-01-02",
    end_date: str = "2020-12-31",
    initial_capital: float = 10_000_000,
    asset: str = "QQQ",
) -> SimulationResult:
    """
    Phase 1: Static book single-asset simulation.

    Parameters
    ----------
    start_date : str — Start date 'YYYY-MM-DD'
    end_date : str — End date 'YYYY-MM-DD'
    initial_capital : float — Starting NAV
    asset : str — Single asset to hold (default QQQ)

    Returns
    -------
    SimulationResult with .history DataFrame and .trade_ledger
    """
    # Import production modules
    from engine.macro_compass import calculate_macro_regime_score, get_regime_label
    from engine.aras import ARASAssessor
    from engine.drawdown_sentinel import run_pds_check

    # Load historical data
    data = load_historical_data(
        start_date, end_date,
        equity_tickers=[asset] if asset not in ("SPY", "QQQ") else [],
        include_individual_tickers=(asset not in ("SPY", "QQQ")),
    )

    # Get asset price series
    if asset in data.benchmark_prices.columns:
        prices = data.benchmark_prices[asset]
    elif asset in data.equity_prices.columns:
        prices = data.equity_prices[asset]
    else:
        raise ValueError(f"Asset '{asset}' not found in loaded data")

    trading_days = prices.dropna().index
    benchmark_spy = data.benchmark_prices["SPY"].reindex(trading_days).ffill()
    benchmark_qqq = data.benchmark_prices["QQQ"].reindex(trading_days).ffill()

    # Initialize state
    aras = ARASAssessor()
    nav = initial_capital
    high_water_mark = nav
    shares = initial_capital / prices.iloc[0]
    cash = 0.0
    trade_ledger = []

    # History tracking
    history_rows = []

    logger.info(
        f"Phase 1 simulation: {asset} | {start_date} → {end_date} | "
        f"${initial_capital:,.0f} initial"
    )

    for i, date in enumerate(trading_days):
        price = prices.loc[date]
        vix = data.macro_signals.loc[date, "VIX"] if date in data.macro_signals.index else 20.0
        tnx = data.macro_signals.loc[date, "TNX_yield"] if date in data.macro_signals.index else 2.0
        hy_spread = data.macro_signals.loc[date, "HY_spread_bps"] if date in data.macro_signals.index else 400.0
        pmi = data.macro_signals.loc[date, "PMI"] if date in data.macro_signals.index else 50.0

        # ── Step 1: Mark to Market ──
        position_value = shares * price
        nav = position_value + cash
        high_water_mark = max(high_water_mark, nav)

        # ── Step 2: Macro Compass ──
        signals = [
            _make_signal_record("VIX_INDEX", vix),
            _make_signal_record("HY_CREDIT_SPREAD", hy_spread / 100.0),  # Convert bps to %
            _make_signal_record("PMI_NOWCAST", pmi),
            _make_signal_record("10Y_TREASURY_YIELD", tnx),
        ]
        regime_score = calculate_macro_regime_score(signals)

        # ── Step 3: ARAS ──
        aras_output = aras.assess(regime_score)
        equity_ceiling = aras_output.equity_ceiling_pct

        # ── Step 4: PDS ──
        pds_signal = run_pds_check(nav, high_water_mark)
        effective_ceiling = min(equity_ceiling, pds_signal.pds_ceiling)

        # ── Step 5: Rebalance if ceiling breached ──
        current_equity_pct = position_value / nav if nav > 0 else 0

        if current_equity_pct > effective_ceiling and position_value > 0:
            # Sell down to ceiling
            target_equity_value = nav * effective_ceiling
            sell_value = position_value - target_equity_value
            shares_to_sell = sell_value / price if price > 0 else 0
            shares -= shares_to_sell
            cash += sell_value

            trade_ledger.append({
                "Date": str(date.date()),
                "Action": "SELL",
                "Ticker": asset,
                "Shares": round(shares_to_sell, 2),
                "Price": round(price, 2),
                "Value": round(sell_value, 2),
                "Trigger": f"Ceiling breach: {current_equity_pct:.1%} > {effective_ceiling:.1%}",
                "Module": "ARAS" if equity_ceiling < 1.0 else "PDS",
                "Regime": aras_output.regime,
                "PDS_Status": pds_signal.status,
            })

        elif (current_equity_pct < effective_ceiling * 0.95 and cash > 0
              and aras_output.regime in ("RISK_ON", "WATCH")):
            # Re-deploy cash when regime improves (partial)
            target_equity_value = nav * effective_ceiling
            buy_value = min(cash, target_equity_value - position_value)
            if buy_value > 0:
                shares_to_buy = buy_value / price if price > 0 else 0
                shares += shares_to_buy
                cash -= buy_value

                trade_ledger.append({
                    "Date": str(date.date()),
                    "Action": "BUY",
                    "Ticker": asset,
                    "Shares": round(shares_to_buy, 2),
                    "Price": round(price, 2),
                    "Value": round(buy_value, 2),
                    "Trigger": f"Re-deploy: regime={aras_output.regime}",
                    "Module": "ARES_SIMPLE",
                    "Regime": aras_output.regime,
                    "PDS_Status": pds_signal.status,
                })

        # Recalculate after trades
        position_value = shares * price
        nav = position_value + cash
        drawdown = (nav / high_water_mark) - 1.0

        # Record history
        history_rows.append({
            "Date": date,
            "NAV": nav,
            "High_Water_Mark": high_water_mark,
            "Drawdown": drawdown,
            "Equity_Pct": position_value / nav if nav > 0 else 0,
            "Cash_Pct": cash / nav if nav > 0 else 0,
            "Regime": aras_output.regime,
            "Regime_Score": regime_score,
            "Equity_Ceiling": equity_ceiling,
            "PDS_Status": pds_signal.status,
            "PDS_Ceiling": pds_signal.pds_ceiling,
            "Effective_Ceiling": effective_ceiling,
            "VIX": vix,
            "HY_Spread": hy_spread,
            "PMI": pmi,
            "TNX_Yield": tnx,
            "SPY_Price": benchmark_spy.loc[date] if date in benchmark_spy.index else np.nan,
            "QQQ_Price": benchmark_qqq.loc[date] if date in benchmark_qqq.index else np.nan,
            f"{asset}_Price": price,
        })

    # Build DataFrame
    history = pd.DataFrame(history_rows)
    history = history.set_index("Date")

    logger.info(
        f"Phase 1 complete: {len(history)} days, "
        f"Final NAV: ${history['NAV'].iloc[-1]:,.0f}, "
        f"Return: {(history['NAV'].iloc[-1] / initial_capital - 1) * 100:.2f}%, "
        f"Max DD: {history['Drawdown'].min() * 100:.2f}%"
    )

    return SimulationResult(
        history=history,
        trade_ledger=trade_ledger,
        config={
            "phase": 1,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "asset": asset,
        },
    )
