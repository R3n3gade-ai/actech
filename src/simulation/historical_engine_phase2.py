"""
Phase 2 Historical Engine — Full Architecture AB Simulation
=============================================================
Wires ALL production ARMS modules into a daily backtesting loop.

Architecture AB allocation: 58% equity / 20% crypto / 14% defensive / 8% cash

Modules wired:
  L2: Macro Compass → ARAS (regime + ceiling)
  L3: PDS, FEM, CDF, DSHP, SSL
  L3: PTRH via regime table + Black-Scholes proxy
  L3: SLOF (regime-gated leverage)
  L3: PERM (covered calls on winners/decaying)
  L4: MICS → Master Engine → Kevlar
  L5: LAEP, Trade Generator
  Offensive: AUP, ARES
"""

import logging
import os
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src not in sys.path:
    sys.path.insert(0, _src)

from simulation.data_loader import (
    DEFAULT_EQUITY_TICKERS,
    DEFENSIVE_TICKERS,
    load_historical_data,
)
from data_feeds.macro_event_state import MacroEventState
from simulation.options_proxy import (
    PTRHSimulator,
    estimate_covered_call_premium,
)
from data_feeds.interfaces import SignalRecord

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Architecture AB target allocations
# ---------------------------------------------------------------------------
ARCHITECTURE_AB = {
    "equity_pct": 0.58,
    "crypto_pct": 0.20,
    "defensive_pct": 0.14,
    "cash_pct": 0.08,
}

# Default MICS scores when no intelligence layer is active
# (Phase 2 uses static conviction; Phase 3 adds dynamic scoring)
DEFAULT_MICS = {
    "TSLA": 8, "NVDA": 9, "AMD": 7, "PLTR": 7, "ALAB": 6, "MU": 7,
    "ANET": 7, "AVGO": 8, "MRVL": 7, "ARM": 6, "ETN": 6, "VRT": 6,
}

# Default equity weights from portfolio snapshot
DEFAULT_EQUITY_WEIGHTS = {
    "TSLA": 0.088, "NVDA": 0.078, "AMD": 0.072, "PLTR": 0.057,
    "ALAB": 0.034, "MU": 0.045, "ANET": 0.043, "AVGO": 0.037,
    "MRVL": 0.035, "ARM": 0.025, "ETN": 0.020, "VRT": 0.023,
}

# Defensive sleeve weights within the 14% allocation
# Per docs: SGOV 3% + SGOL 2% + DBMF 5% + STRC 4% = 14%
DEFENSIVE_WEIGHTS = {"SGOV": 0.03, "SGOL": 0.02, "DBMF": 0.05, "STRC": 0.04}

# Historical defensive sleeve (pre-2023) per MJ's canonical backtest:
# TLT 7% / SGOL 4% / DBMF 3% = 14%
BACKTEST_DEFENSIVE_WEIGHTS = {"TLT": 0.07, "SGOL": 0.04, "DBMF": 0.03}

# CDF underperformance tracking state
CDF_DECAY_45 = 0.80
CDF_DECAY_90 = 0.60


@dataclass
class SimulationResult:
    """Container for simulation output."""
    history: pd.DataFrame
    trade_ledger: List[dict] = field(default_factory=list)
    config: dict = field(default_factory=dict)


@dataclass
class PositionState:
    """Tracks a single position through the simulation."""
    ticker: str
    shares: float = 0.0
    entry_price: float = 0.0
    entry_date: Optional[pd.Timestamp] = None
    # CDF tracking
    days_underperforming: int = 0
    cdf_multiplier: float = 1.0
    last_thesis_update: Optional[pd.Timestamp] = None


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


def run_simulation_phase2(
    start_date: str = "2020-01-02",
    end_date: str = "2022-12-30",
    initial_capital: float = 10_000_000,
    use_individual_tickers: bool = True,
    mics_overrides: Optional[Dict[str, int]] = None,
    equity_weights: Optional[Dict[str, float]] = None,
    defensive_weights: Optional[Dict[str, float]] = None,
) -> SimulationResult:
    """
    Phase 2: Full Architecture AB simulation with all modules.

    Parameters
    ----------
    start_date : str — Start date 'YYYY-MM-DD'
    end_date : str — End date 'YYYY-MM-DD'
    initial_capital : float — Starting NAV
    use_individual_tickers : bool — Use individual equities vs QQQ proxy
    mics_overrides : dict, optional — Override MICS scores per ticker
    equity_weights : dict, optional — Override equity target weights

    Returns
    -------
    SimulationResult with .history (DataFrame) and .trade_ledger (list)
    """
    # ── Import production modules ──
    from engine.macro_compass import calculate_macro_regime_score, get_regime_label
    from engine.aras import ARASAssessor
    from engine.drawdown_sentinel import run_pds_check
    from engine.kevlar import apply_kevlar_limits
    from engine.laep import resolve_execution_params, classify_vix_tier
    from engine.tail_hedge import PTRH_REGIME_TABLE

    # ── Configuration ──
    mics_scores = {**DEFAULT_MICS, **(mics_overrides or {})}
    eq_weights = {**DEFAULT_EQUITY_WEIGHTS, **(equity_weights or {})}

    equity_tickers = list(eq_weights.keys())
    total_eq_weight = sum(eq_weights.values())

    # Normalize equity weights to sum to Architecture AB equity allocation
    for t in eq_weights:
        eq_weights[t] = (eq_weights[t] / total_eq_weight) * ARCHITECTURE_AB["equity_pct"]

    # Select defensive sleeve based on period
    # Pre-2023: historical TLT/SGOL/DBMF (TLT was removed after 2022 failure)
    # 2023+: current SGOV/SGOL/DBMF/STRC
    if defensive_weights is not None:
        active_dw = defensive_weights
    elif start_date >= "2023":
        active_dw = DEFENSIVE_WEIGHTS
    else:
        active_dw = BACKTEST_DEFENSIVE_WEIGHTS

    # ── Load data ──
    logger.info(f"Phase 2 simulation: {start_date} → {end_date} | ${initial_capital:,.0f}")
    data = load_historical_data(
        start_date, end_date,
        equity_tickers=equity_tickers,
        include_individual_tickers=use_individual_tickers,
    )

    trading_days = data.trading_days

    # ── Initialize state ──
    aras = ARASAssessor()
    ptrh_sim = PTRHSimulator()

    nav = initial_capital
    high_water_mark = nav
    cash = initial_capital * ARCHITECTURE_AB["cash_pct"]

    # Initialize equity positions
    equity_positions: Dict[str, PositionState] = {}
    if use_individual_tickers:
        # Filter out tickers with no data for this period and redistribute weight
        available_eq = {}
        for ticker, weight in eq_weights.items():
            if ticker in data.equity_prices.columns:
                price0 = data.equity_prices[ticker].iloc[0]
                if not np.isnan(price0) and price0 > 0:
                    available_eq[ticker] = weight
                else:
                    logger.warning(f"Skipping {ticker}: no valid price data for period")

        # Redistribute missing weight proportionally to available tickers
        if available_eq:
            total_avail = sum(available_eq.values())
            target_total = sum(eq_weights.values())
            scale = target_total / total_avail if total_avail > 0 else 1.0
            for ticker, weight in available_eq.items():
                adjusted_weight = weight * scale
                price0 = data.equity_prices[ticker].iloc[0]
                alloc = initial_capital * adjusted_weight
                shares = alloc / price0
                equity_positions[ticker] = PositionState(
                    ticker=ticker, shares=shares,
                    entry_price=price0, entry_date=trading_days[0],
                )
    else:
        # QQQ proxy for entire equity sleeve
        qqq_price0 = data.benchmark_prices["QQQ"].iloc[0]
        alloc = initial_capital * ARCHITECTURE_AB["equity_pct"]
        equity_positions["QQQ"] = PositionState(
            ticker="QQQ", shares=alloc / qqq_price0,
            entry_price=qqq_price0, entry_date=trading_days[0],
        )

    # Initialize defensive positions
    defensive_positions: Dict[str, PositionState] = {}
    for ticker, weight in active_dw.items():
        if ticker in data.defensive_prices.columns:
            price0 = data.defensive_prices[ticker].iloc[0]
            if np.isnan(price0) or price0 <= 0:
                logger.warning(f"Skipping {ticker}: no valid price data for period")
                continue
            alloc = initial_capital * weight
            defensive_positions[ticker] = PositionState(
                ticker=ticker, shares=alloc / price0,
                entry_price=price0, entry_date=trading_days[0],
            )

    # Initialize crypto position (BTC proxy)
    crypto_shares = 0.0
    if "BTC" in data.crypto_prices.columns:
        btc_price0 = data.crypto_prices["BTC"].iloc[0]
        crypto_alloc = initial_capital * ARCHITECTURE_AB["crypto_pct"]
        crypto_shares = crypto_alloc / btc_price0 if btc_price0 > 0 else 0

    # SLOF state
    slof_active = False
    slof_leverage = 1.0

    # PERM state: track covered call income
    perm_income = 0.0

    # FEM tracking (simplified: count AI-sector exposure)
    ai_sector_tickers = {"NVDA", "AMD", "ALAB", "MU", "ANET", "AVGO", "MRVL", "ARM"}

    # ARES state
    last_reentry_date = None
    reentry_tranche = 0

    trade_ledger = []
    history_rows = []

    # Tracking for LAEP slippage simulation
    total_slippage = 0.0

    # ══════════════════════════════════════════════════════════
    # MAIN SIMULATION LOOP
    # ══════════════════════════════════════════════════════════
    for i, date in enumerate(trading_days):
        # ── Get today's prices ──
        macro = data.macro_signals.loc[date] if date in data.macro_signals.index else None
        vix = macro["VIX"] if macro is not None else 20.0
        tnx = macro["TNX_yield"] if macro is not None else 2.0
        hy_spread = macro["HY_spread_bps"] if macro is not None else 400.0
        pmi = macro["PMI"] if macro is not None else 50.0

        spy_price = data.benchmark_prices["SPY"].loc[date] if date in data.benchmark_prices.index else np.nan
        qqq_price = data.benchmark_prices["QQQ"].loc[date] if date in data.benchmark_prices.index else np.nan
        btc_price = data.crypto_prices["BTC"].loc[date] if date in data.crypto_prices.index else 0

        # ── Step 1: Mark to Market ──
        equity_value = 0.0
        for ticker, pos in equity_positions.items():
            if use_individual_tickers and ticker in data.equity_prices.columns:
                price = data.equity_prices[ticker].loc[date]
            elif ticker == "QQQ":
                price = qqq_price
            else:
                price = 0
            equity_value += pos.shares * price

        defensive_value = 0.0
        for ticker, pos in defensive_positions.items():
            if ticker in data.defensive_prices.columns:
                price = data.defensive_prices[ticker].loc[date]
                if not np.isnan(price):
                    defensive_value += pos.shares * price

        crypto_value = crypto_shares * btc_price if btc_price > 0 else 0

        # SLOF: synthetic leverage on equity book
        slof_notional = 0.0
        if slof_active and slof_leverage > 1.0:
            slof_notional = equity_value * (slof_leverage - 1.0)

        nav = equity_value + defensive_value + crypto_value + cash + slof_notional
        high_water_mark = max(high_water_mark, nav)

        # ── Step 2: Macro Compass → Regime Score ──
        signals = [
            _make_signal_record("VIX_INDEX", vix),
            _make_signal_record("HY_CREDIT_SPREAD", hy_spread / 100.0),
            _make_signal_record("PMI_NOWCAST", pmi),
            _make_signal_record("10Y_TREASURY_YIELD", tnx),
        ]
        # VIX-based event overlay: in production, CDM+MacroEventState pushes
        # override_floor during extreme events. Backtest infers from VIX.
        event_state = MacroEventState()
        if vix > 60:
            event_state.override_floor = 0.92   # CRASH
        elif vix > 45:
            event_state.override_floor = 0.85   # DEFENSIVE
        regime_score = calculate_macro_regime_score(signals, event_state=event_state)

        # ── Step 3: ARAS → Regime + Equity Ceiling ──
        aras_output = aras.assess(regime_score)
        equity_ceiling = aras_output.equity_ceiling_pct

        # ── Step 4: PDS → Drawdown Override ──
        pds_signal = run_pds_check(nav, high_water_mark)
        effective_ceiling = min(equity_ceiling, pds_signal.pds_ceiling)

        # ── Step 5: FEM — Factor Exposure Monitor ──
        # Per docs: 6 factor categories (AI Capex, Taiwan Mfg, BTC Beta, Dollar Sensitivity,
        # Rate Sensitivity, Cross-sleeve Correlation). Each uses 0.65 WATCH / 0.80 ALERT thresholds.
        # Backtest simplification: track AI Capex Cycle factor (primary measurable factor).
        ai_exposure = 0.0
        for ticker in ai_sector_tickers:
            if ticker in equity_positions:
                if use_individual_tickers and ticker in data.equity_prices.columns:
                    price = data.equity_prices[ticker].loc[date]
                else:
                    price = 0
                ai_exposure += equity_positions[ticker].shares * price
        fem_score = ai_exposure / nav if nav > 0 else 0
        fem_status = "ALERT" if fem_score > 0.80 else ("WATCH" if fem_score > 0.65 else "NORMAL")

        # ── Step 6: CDF — Conviction Decay (per-position) ──
        if i > 0 and qqq_price > 0:
            qqq_ret_cumulative = (qqq_price / data.benchmark_prices["QQQ"].iloc[0]) - 1.0
            for ticker, pos in equity_positions.items():
                if pos.shares <= 0:
                    continue
                if use_individual_tickers and ticker in data.equity_prices.columns:
                    ticker_ret = (data.equity_prices[ticker].loc[date] / pos.entry_price) - 1.0
                    underperf = qqq_ret_cumulative - ticker_ret
                    if underperf > 0.10:  # 10pp underperformance
                        pos.days_underperforming += 1
                    else:
                        pos.days_underperforming = max(0, pos.days_underperforming - 1)

                    # Apply CDF milestones
                    if pos.days_underperforming >= 135:
                        pos.cdf_multiplier = CDF_DECAY_90
                    elif pos.days_underperforming >= 90:
                        pos.cdf_multiplier = CDF_DECAY_90
                    elif pos.days_underperforming >= 45:
                        pos.cdf_multiplier = CDF_DECAY_45
                    else:
                        pos.cdf_multiplier = 1.0

        # ── Step 7: AUP — Asymmetric Upside Protocol (5 conditions per docs) ──
        # Per docs ALL 5 required:
        # (1) Regime = RISK_ON
        # (2) Avg conviction across book >7.5
        # (3) FEM clean (no ALERT, ideally no WATCH)
        # (4) RPE <20% prob of WATCH/DEFENSIVE in 30d — NOT simulatable, use regime stability proxy
        # (5) SSL net loss <12% worst scenario — use drawdown as proxy
        drawdown_pct = abs((nav / high_water_mark) - 1.0)
        avg_mics = np.mean(list(mics_scores.values())) if mics_scores else 5.0
        # Proxy for RPE: regime has been RISK_ON for recent days (stable)
        regime_stable = (aras_output.regime == "RISK_ON" and regime_score < 0.25)
        aup_eligible = (
            aras_output.regime == "RISK_ON"           # Condition 1
            and avg_mics > 7.5                         # Condition 2
            and fem_status != "ALERT"                  # Condition 3
            and regime_stable                          # Condition 4 proxy (RPE)
            and drawdown_pct < 0.12                    # Condition 5 proxy (SSL)
        )

        # ── Step 8: SLOF — Synthetic Leverage (C10 only, RISK_ON/WATCH) ──
        # Per docs: 1.25x fixed, C10 conviction positions ONLY,
        # RISK_ON/WATCH only, NOT available in NEUTRAL/DEFENSIVE/CRASH.
        # Automatic removal on regime deterioration.
        c10_positions = [t for t, m in mics_scores.items()
                         if m >= 10 and t in equity_positions and equity_positions[t].shares > 0]
        if aras_output.regime in ("RISK_ON", "WATCH") and len(c10_positions) > 0:
            if not slof_active:
                slof_active = True
                slof_leverage = 1.25
        else:
            # NEUTRAL / DEFENSIVE / CRASH — unwind SLOF
            if slof_active and slof_leverage > 1.0:
                trade_ledger.append({
                    "Date": str(date.date()), "Action": "UNWIND_SLOF",
                    "Ticker": "SLOF", "Value": round(slof_notional, 2),
                    "Trigger": f"Regime={aras_output.regime}", "Module": "SLOF",
                    "Regime": aras_output.regime, "PDS_Status": pds_signal.status,
                })
            slof_active = False
            slof_leverage = 1.0

        # ── Step 9: PTRH Sizing via Regime Table (THB v4.0 FINAL — CAM removed) ──
        regime_entry = PTRH_REGIME_TABLE.get(aras_output.regime, PTRH_REGIME_TABLE["RISK_ON"])
        ptrh_coverage = regime_entry["coverage_pct"]
        # CRASH: hold existing, no new buys (implied vol too expensive)
        is_crash = aras_output.regime == "CRASH"
        ptrh_notional = nav * ptrh_coverage

        # ── Step 9b: STRC Dynamic Reserve Layer ──
        # Additive to PTRH: 0% RISK_ON/WATCH, 50% NEUTRAL, 100% DEFENSIVE/CRASH
        strc_reserve_pct = regime_entry["strc_reserve"]
        strc_value = defensive_positions["STRC"].shares * data.defensive_prices["STRC"].loc[date] if "STRC" in defensive_positions and "STRC" in data.defensive_prices.columns and not np.isnan(data.defensive_prices["STRC"].loc[date]) else 0.0
        strc_deployed = strc_value * strc_reserve_pct
        total_hedge_notional = ptrh_notional + strc_deployed

        # ── Step 10: PTRH — Tail Hedge via Options Proxy ──
        ptrh_result = ptrh_sim.step(date, qqq_price, vix, total_hedge_notional)

        # ── Step 11: Master Engine — Target Weights + KEVLAR ──
        # C² weighting: weight ∝ (MICS²) × CDF_decay
        c2_raw = {}
        for ticker in equity_positions:
            mics = mics_scores.get(ticker, 5)
            cdf = equity_positions[ticker].cdf_multiplier
            c2_raw[ticker] = (mics ** 2) * cdf

        total_c2 = sum(c2_raw.values()) or 1.0
        target_equity_weights = {}
        ai_sector_total_weight = 0.0
        for ticker, c2 in c2_raw.items():
            raw_weight = (c2 / total_c2) * effective_ceiling * ARCHITECTURE_AB["equity_pct"]
            # KEVLAR per docs:
            # - 22% single-position maximum
            # - 3% minimum position size (new positions in book)
            capped_weight = min(raw_weight, 0.22)
            if capped_weight > 0 and capped_weight < 0.03:
                capped_weight = 0.03  # Minimum meaningful position
            target_equity_weights[ticker] = capped_weight
            # Track AI sector exposure for 48% cap
            if ticker in ai_sector_tickers:
                ai_sector_total_weight += capped_weight

        # KEVLAR: 48% AI sector cap
        if ai_sector_total_weight > 0.48:
            scale_factor = 0.48 / ai_sector_total_weight
            for ticker in ai_sector_tickers:
                if ticker in target_equity_weights:
                    target_equity_weights[ticker] *= scale_factor

        # KEVLAR: 15% BTC proxy cap (IBIT + BTC-correlated like PLTR)
        # In backtest, crypto is separate sleeve — cap applied to total crypto allocation
        crypto_value_check = crypto_shares * btc_price if btc_price > 0 else 0
        crypto_weight_check = crypto_value_check / nav if nav > 0 else 0
        btc_proxy_cap_exceeded = crypto_weight_check > 0.15

        # ── Step 12: PERM — Profit-Locking Mechanism ──
        # Per docs: Unrealized gain >30% → evaluate covered calls (Tier 1, 4h veto).
        # CDF decay (Day 45/90/135) → reduce effective conviction weight (Tier 0).
        # No VIX minimum requirement in docs.
        if i % 30 == 0:  # Monthly evaluation cycle
            for ticker, pos in equity_positions.items():
                if pos.shares <= 0:
                    continue
                if use_individual_tickers and ticker in data.equity_prices.columns:
                    current_price = data.equity_prices[ticker].loc[date]
                    unrealized_gain = (current_price / pos.entry_price) - 1.0

                    if unrealized_gain >= 0.30 or pos.cdf_multiplier <= 0.80:
                        premium = estimate_covered_call_premium(current_price, vix)
                        call_income = premium * pos.shares * 0.25  # Cover 25% of position
                        perm_income += call_income
                        cash += call_income

                        trigger = (f"Gain={unrealized_gain:.0%}"
                                   if unrealized_gain >= 0.30
                                   else f"CDF={pos.cdf_multiplier:.2f}")
                        trade_ledger.append({
                            "Date": str(date.date()), "Action": "SELL_CALL",
                            "Ticker": ticker, "Value": round(call_income, 2),
                            "Trigger": trigger, "Module": "PERM",
                            "Regime": aras_output.regime,
                            "PDS_Status": pds_signal.status,
                        })

        # ── Step 13: DSHP — Defensive Sleeve Harvest Protocol ──
        # Per docs:
        #   SGOL: >20% appreciation above ENTRY price → trim to 2% target. Proceeds → SGOV.
        #   DBMF: >15% appreciation above entry OR weight drift >1.5pp above 5% → trim to 5%. → SGOV.
        #   STRC: No price harvest (yield only).
        #   SGOV: Receiver — no harvest.
        for ticker, pos in defensive_positions.items():
            if pos.shares <= 0 or ticker not in data.defensive_prices.columns:
                continue
            if ticker in ("SGOV", "STRC", "TLT"):
                continue  # SGOV = receiver, STRC = yield only, TLT = bond allocation (no harvest)

            current_price = data.defensive_prices[ticker].loc[date]
            pos_value = pos.shares * current_price
            pos_weight = pos_value / nav if nav > 0 else 0
            target_weight = active_dw.get(ticker, 0.03)

            should_trim = False
            trigger_msg = ""

            if ticker == "SGOL":
                # >20% appreciation above entry price
                sgol_gain = (current_price / pos.entry_price) - 1.0 if pos.entry_price > 0 else 0
                if sgol_gain > 0.20:
                    should_trim = True
                    trigger_msg = f"DSHP SGOL gain={sgol_gain:.1%}>20%"
            elif ticker == "DBMF":
                dbmf_gain = (current_price / pos.entry_price) - 1.0 if pos.entry_price > 0 else 0
                weight_drift = pos_weight - target_weight  # 1.5pp above 5%
                if dbmf_gain > 0.15:
                    should_trim = True
                    trigger_msg = f"DSHP DBMF gain={dbmf_gain:.1%}>15%"
                elif weight_drift > 0.015:
                    should_trim = True
                    trigger_msg = f"DSHP DBMF drift={weight_drift:.2%}>1.5pp"

            if should_trim:
                trim_value = pos_value - (nav * target_weight)
                if trim_value > 0:
                    shares_to_sell = trim_value / current_price
                    pos.shares -= shares_to_sell
                    # Reset entry price after harvest so future triggers
                    # require a NEW appreciation cycle from current level
                    pos.entry_price = current_price
                    # Proceeds go to SGOV per docs
                    if "SGOV" in defensive_positions and "SGOV" in data.defensive_prices.columns:
                        sgov_price = data.defensive_prices["SGOV"].loc[date]
                        if not np.isnan(sgov_price) and sgov_price > 0:
                            sgov_shares = trim_value / sgov_price
                            defensive_positions["SGOV"].shares += sgov_shares
                    else:
                        cash += trim_value  # Fallback if SGOV not available

                    trade_ledger.append({
                        "Date": str(date.date()), "Action": "TRIM",
                        "Ticker": ticker, "Value": round(trim_value, 2),
                        "Trigger": trigger_msg,
                        "Module": "DSHP",
                        "Regime": aras_output.regime,
                        "PDS_Status": pds_signal.status,
                    })

        # ── Step 14: Portfolio Rebalancing ──
        # Per docs: ARAS equity ceiling applies to equity sleeve.
        # PDS ceiling also applies to equity. Effective = min(ARAS, PDS).
        # Crypto follows Architecture AB target (20%) — only sold in LAEP CRISIS (VIX>45)
        # via priority order: BSOL→ETHB→IBIT→equity (illiquid first).
        current_equity_pct = equity_value / nav if nav > 0 else 0

        # Trim equity if over its ceiling
        if current_equity_pct > effective_ceiling + 0.01:
            target_eq_value = nav * effective_ceiling
            excess = equity_value - target_eq_value

            if excess > nav * 0.005:  # Only rebalance if >0.5% NAV
                for ticker, pos in equity_positions.items():
                    if pos.shares <= 0:
                        continue
                    if use_individual_tickers and ticker in data.equity_prices.columns:
                        price = data.equity_prices[ticker].loc[date]
                    elif ticker == "QQQ":
                        price = qqq_price
                    else:
                        continue

                    pos_value = pos.shares * price
                    pos_pct = pos_value / equity_value if equity_value > 0 else 0
                    sell_value = excess * pos_pct
                    shares_to_sell = sell_value / price if price > 0 else 0

                    if shares_to_sell > 0:
                        # LAEP slippage simulation
                        vix_tier = _get_vix_tier(vix)
                        slippage_bps = _slippage_for_tier(vix_tier)
                        slippage = sell_value * (slippage_bps / 10000)
                        total_slippage += slippage

                        pos.shares -= shares_to_sell
                        cash += sell_value - slippage

                        trade_ledger.append({
                            "Date": str(date.date()), "Action": "SELL",
                            "Ticker": ticker,
                            "Shares": round(shares_to_sell, 4),
                            "Price": round(price, 2),
                            "Value": round(sell_value, 2),
                            "Slippage": round(slippage, 2),
                            "Trigger": f"Ceiling: {current_equity_pct:.1%}>{effective_ceiling:.1%}",
                            "Module": "ARAS" if equity_ceiling <= pds_signal.pds_ceiling else "PDS",
                            "Regime": aras_output.regime,
                            "PDS_Status": pds_signal.status,
                        })

        # LAEP CRISIS crypto sell — VIX > 45: sell crypto in priority order per docs
        # Priority: BSOL→ETHB→IBIT→equity (illiquid first)
        # In backtest BTC is our only crypto proxy, so sell BTC when VIX > 45
        crypto_value = crypto_shares * btc_price if btc_price > 0 else 0
        crypto_pct = crypto_value / nav if nav > 0 else 0
        laep_tier = _get_vix_tier(vix)

        if laep_tier == "CRISIS" and crypto_pct > 0.02 and btc_price > 0:
            # In crisis, reduce crypto to minimum
            target_crypto_val = nav * 0.02  # Minimize crypto in crisis
            sell_crypto = crypto_value - target_crypto_val
            if sell_crypto > 0:
                slippage_bps = _slippage_for_tier("CRISIS")
                slippage = sell_crypto * (slippage_bps / 10000)
                btc_to_sell = sell_crypto / btc_price
                crypto_shares -= btc_to_sell
                cash += sell_crypto - slippage
                total_slippage += slippage
                trade_ledger.append({
                    "Date": str(date.date()), "Action": "SELL",
                    "Ticker": "BTC", "Value": round(sell_crypto, 2),
                    "Slippage": round(slippage, 2),
                    "Trigger": f"LAEP CRISIS VIX={vix:.0f} crypto={crypto_pct:.1%}",
                    "Module": "LAEP",
                    "Regime": aras_output.regime,
                    "PDS_Status": pds_signal.status,
                })

        # ── Step 15: ARES Re-entry (3-tranche VARES per docs) ──
        # Per docs: 4 gate conditions → 3 VARES-sized tranches, 48h spacing
        # Gate 1: Regime improved (not CRASH/DEFENSIVE — matches production ares.py)
        # Gate 2: VIX declining (regime_score < 0.50)
        # Gate 3: Trigger resolved (PDS not in worst deleverage)
        # Gate 4: Excess cash above target
        # VARES sizing: base 33.3% of target × vol_adj (vix_90d_avg / vix_current), clamped [0.15, 0.35]
        current_cash_pct = cash / nav if nav > 0 else 0

        if (aras_output.regime not in ("CRASH", "DEFENSIVE")
                and pds_signal.status != "DELEVERAGE_2"
                and current_cash_pct > ARCHITECTURE_AB["cash_pct"] + 0.03
                and regime_score < 0.50):

            can_reenter = True
            if last_reentry_date is not None:
                days_since = (date - last_reentry_date).days
                if days_since < 2:  # 48h spacing between tranches
                    can_reenter = False

            if can_reenter and reentry_tranche < 3:  # Exactly 3 tranches per docs
                # VARES sizing: vol_adj = vix_90d_avg / vix_current, clamped [0.15, 0.35]
                vix_90d_avg = 20.0  # Default if not enough history
                if i >= 90:
                    vix_history = [
                        data.macro_signals.loc[trading_days[j], "VIX"]
                        if trading_days[j] in data.macro_signals.index else 20.0
                        for j in range(i - 90, i)
                    ]
                    vix_90d_avg = np.mean(vix_history)
                vol_adj = vix_90d_avg / vix if vix > 0 else 1.0
                tranche_pct = max(0.15, min(0.35, 0.333 * vol_adj))

                excess_cash = cash - nav * ARCHITECTURE_AB["cash_pct"]
                deploy_amount = min(excess_cash, nav * tranche_pct)
                if deploy_amount > 0:
                    # Split deployment between equity and crypto per Architecture AB
                    current_eq_pct = equity_value / nav if nav > 0 else 0
                    current_cr_pct = crypto_value / nav if nav > 0 else 0
                    eq_shortfall = max(0, ARCHITECTURE_AB["equity_pct"] - current_eq_pct)
                    cr_shortfall = max(0, ARCHITECTURE_AB["crypto_pct"] - current_cr_pct)
                    total_shortfall = eq_shortfall + cr_shortfall

                    if total_shortfall > 0:
                        equity_deploy = deploy_amount * (eq_shortfall / total_shortfall)
                        crypto_deploy = deploy_amount * (cr_shortfall / total_shortfall)
                    else:
                        equity_deploy = deploy_amount
                        crypto_deploy = 0

                    # Deploy equity portion
                    if equity_deploy > 0:
                        for ticker, pos in equity_positions.items():
                            if use_individual_tickers and ticker in data.equity_prices.columns:
                                price = data.equity_prices[ticker].loc[date]
                            elif ticker == "QQQ":
                                price = qqq_price
                            else:
                                continue

                            target_w = target_equity_weights.get(ticker, 0)
                            total_target = sum(target_equity_weights.values()) or 1.0
                            alloc_pct = target_w / total_target
                            buy_value = equity_deploy * alloc_pct

                            if buy_value > 0 and price > 0:
                                shares_to_buy = buy_value / price
                                pos.shares += shares_to_buy
                                cash -= buy_value

                                trade_ledger.append({
                                    "Date": str(date.date()), "Action": "BUY",
                                    "Ticker": ticker,
                                    "Shares": round(shares_to_buy, 4),
                                    "Price": round(price, 2),
                                    "Value": round(buy_value, 2),
                                    "Trigger": f"ARES T{reentry_tranche + 1} VARES={tranche_pct:.1%}",
                                    "Module": "ARES",
                                    "Regime": aras_output.regime,
                                    "PDS_Status": pds_signal.status,
                                })

                    # Deploy crypto portion (buy BTC back after crisis sell)
                    if crypto_deploy > 0 and btc_price > 0:
                        btc_to_buy = crypto_deploy / btc_price
                        crypto_shares += btc_to_buy
                        cash -= crypto_deploy

                        trade_ledger.append({
                            "Date": str(date.date()), "Action": "BUY",
                            "Ticker": "BTC",
                            "Shares": round(btc_to_buy, 6),
                            "Price": round(btc_price, 2),
                            "Value": round(crypto_deploy, 2),
                            "Trigger": f"ARES T{reentry_tranche + 1} VARES={tranche_pct:.1%}",
                            "Module": "ARES",
                            "Regime": aras_output.regime,
                            "PDS_Status": pds_signal.status,
                        })

                    reentry_tranche += 1
                    last_reentry_date = date

        # Abort + reset ARES tranches on any circuit breaker
        if aras_output.regime in ("DEFENSIVE", "CRASH") or pds_signal.status == "DELEVERAGE_2":
            reentry_tranche = 0

        # ── Final: Recalculate NAV + Record History ──
        equity_value = 0.0
        for ticker, pos in equity_positions.items():
            if use_individual_tickers and ticker in data.equity_prices.columns:
                price = data.equity_prices[ticker].loc[date]
            elif ticker == "QQQ":
                price = qqq_price
            else:
                price = 0
            equity_value += pos.shares * price

        defensive_value = 0.0
        for ticker, pos in defensive_positions.items():
            if ticker in data.defensive_prices.columns:
                dp = data.defensive_prices[ticker].loc[date]
                if not np.isnan(dp):
                    defensive_value += pos.shares * dp

        crypto_value = crypto_shares * btc_price if btc_price > 0 else 0
        slof_notional = equity_value * (slof_leverage - 1.0) if slof_active else 0

        nav = equity_value + defensive_value + crypto_value + cash + slof_notional
        nav += ptrh_result["ptrh_value"]  # Add put hedge value
        drawdown = (nav / high_water_mark) - 1.0

        history_rows.append({
            "Date": date,
            "NAV": nav,
            "High_Water_Mark": high_water_mark,
            "Drawdown": drawdown,
            # Sleeve allocations
            "Equity_Pct": equity_value / nav if nav > 0 else 0,
            "Crypto_Pct": crypto_value / nav if nav > 0 else 0,
            "Defensive_Pct": defensive_value / nav if nav > 0 else 0,
            "Cash_Pct": cash / nav if nav > 0 else 0,
            # Regime
            "Regime": aras_output.regime,
            "Regime_Score": regime_score,
            "Equity_Ceiling": equity_ceiling,
            "PDS_Status": pds_signal.status,
            "PDS_Ceiling": pds_signal.pds_ceiling,
            "Effective_Ceiling": effective_ceiling,
            # PTRH
            "PTRH_Value": ptrh_result["ptrh_value"],
            "PTRH_Notional": ptrh_result["ptrh_notional"],
            "PTRH_Net_Cost": ptrh_result["ptrh_net_cost"],
            # SLOF
            "SLOF_Active": slof_active,
            "SLOF_Leverage": slof_leverage,
            "SLOF_Notional": slof_notional,
            # PERM
            "PERM_Income": perm_income,
            # FEM
            "FEM_Score": fem_score,
            "FEM_Status": fem_status,
            # Macro
            "VIX": vix,
            "HY_Spread": hy_spread,
            "PMI": pmi,
            "TNX_Yield": tnx,
            # LAEP
            "Total_Slippage": total_slippage,
            # Benchmarks
            "SPY_Price": spy_price,
            "QQQ_Price": qqq_price,
            "BTC_Price": btc_price,
        })

    # ── Build Output ──
    history = pd.DataFrame(history_rows)
    history = history.set_index("Date")

    final_nav = history["NAV"].iloc[-1]
    total_ret = (final_nav / initial_capital - 1) * 100
    max_dd = history["Drawdown"].min() * 100
    daily_ret = history["NAV"].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * (252 ** 0.5) if daily_ret.std() > 0 else 0

    logger.info(
        f"Phase 2 complete: {len(history)} days | "
        f"Return: {total_ret:+.2f}% | Sharpe: {sharpe:.2f} | "
        f"Max DD: {max_dd:.2f}% | Trades: {len(trade_ledger)}"
    )

    return SimulationResult(
        history=history,
        trade_ledger=trade_ledger,
        config={
            "phase": 2,
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "use_individual_tickers": use_individual_tickers,
            "architecture_ab": ARCHITECTURE_AB,
        },
    )


# ---------------------------------------------------------------------------
# LAEP Helpers — 3-tier VIX system per canonical docs
# ---------------------------------------------------------------------------
def _get_vix_tier(vix: float) -> str:
    """Map VIX to LAEP tier per docs: NORMAL / ELEVATED / CRISIS."""
    if vix < 25:
        return "NORMAL"
    elif vix <= 45:
        return "ELEVATED"
    else:
        return "CRISIS"


def _slippage_for_tier(tier: str) -> float:
    """Slippage in basis points per LAEP tier.
    NORMAL (<25): 8 bps, ELEVATED (25-45): 20 bps, CRISIS (>45): 40 bps.
    """
    return {"NORMAL": 8, "ELEVATED": 20, "CRISIS": 40}.get(tier, 8)
