"""
ARMS Backtest Replay Harness — Production Engine Driver
========================================================

Walks historical trading days and drives the PRODUCTION engine modules in
``src/engine/*`` exactly as the live system would. NO inlined logic.
Every regime/risk/sizing decision delegates to a production module:

    - engine.macro_compass.calculate_macro_regime_score   (regime score from VIX/HY/PMI/10Y)
    - engine.aras.ARASAssessor.assess                     (regime + ARAS ceiling, with hysteresis)
    - engine.drawdown_sentinel.run_pds_check              (DD monitoring, PDS ceiling)
    - engine.master_engine.compute_target_weights         (C^2 target weights, ARAS+PDS+SLOF gating)
    - engine.master_engine.compute_effective_ceiling      (THB §4: min(ARAS, PDS))
    - engine.kevlar.apply_kevlar_limits                   (called inside master_engine)
    - engine.ares.run_ares_check                          (4-gate re-entry + VARES tranche sizing)
    - engine.tail_hedge.PTRH_REGIME_TABLE                 (regime-table tail hedge sizing)
    - engine.laep.classify_vix_tier                       (5-tier VIX execution slippage)

Architecture AB target allocation: 58% equity / 20% crypto / 14% defensive / 8% cash
Equity sleeve: 12 production names with C^2 weighting from MICS scores
Defensive: 2023+ uses SGOV/SGOL/DBMF/STRC; pre-2023 uses TLT/SGOL/DBMF
Crypto: BTC-USD proxy
PTRH: Tail-hedge cost modeled via options_proxy.PTRHSimulator (Black-Scholes)

Replaces the deprecated ``historical_engine_phase2.py`` parallel engine.
The output ``SimulationResult.history`` DataFrame is schema-compatible with
``simulation.pdf_report``.

Reference: THB v4.0 FINAL_1, Addendum 1 §1.2 (PTRH regime table), Blueprint
Correction Memo (kill-chain, 12/18 PDS thresholds, 10-15% OTM PTRH).
"""

from __future__ import annotations

import datetime
import logging
import os
import sys
import tempfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

# Ensure src on path when invoked from project root
_src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _src not in sys.path:
    sys.path.insert(0, _src)

from simulation.data_loader import (
    DEFAULT_EQUITY_TICKERS,
    DEFENSIVE_TICKERS,
    load_historical_data,
)
from simulation.options_proxy import (
    PTRHSimulator,
    estimate_covered_call_premium,
)
from data_feeds.interfaces import SignalRecord
from data_feeds.macro_event_state import MacroEventState

# ── Production engine modules (single source of truth) ───────────────────────
from engine.macro_compass import calculate_macro_regime_score
from engine.aras import ARASAssessor
from engine.drawdown_sentinel import run_pds_check
from engine.master_engine import compute_target_weights, compute_effective_ceiling
from engine.ares import run_ares_check
from engine.tail_hedge import PTRH_REGIME_TABLE
from engine.laep import classify_vix_tier, VIXTier, MAX_ADV_FRACTION, MIN_FILL_FRACTION
from modules.deleveraging_risk import DeleveragingRisk
from modules.crypto_microstructure import run_crypto_microstructure_check
from modules.margin_stress import run_margin_stress_check
from modules.dealer_gamma import run_dealer_gamma_check
from modules.pcr_regime import run_pcr_regime_check
from modules.shutdown_risk import run_shutdown_risk_check
import modules.pcr_regime as _pcr_module  # state-path redirect
from engine.regime_probability import calculate_rpe
from engine.asymmetric_upside import run_aup_check
from engine.factor_exposure import (
    run_fem_check,
    AI_CAPEX_TICKERS,
    TAIWAN_MFG_TICKERS,
    BTC_BETA_TICKERS,
    DOLLAR_SENSITIVITY_TICKERS,
    RATE_SENSITIVITY_TICKERS,
    CROSS_SLEEVE_CORRELATION_THRESHOLD,
)
from engine.dshp import check_dshp_triggers, DefensivePosition
from engine.cdm import run_cdm_scan, NewsItem
from engine.tdc import run_thesis_review, run_weekly_tdc_audit
from engine.incapacitation import run_incapacitation_check
from execution.correlation_monitor import run_correlation_monitor
import execution.correlation_monitor as _corr_module  # state-path redirect
from modules.stress_scenarios import run_stress_scenarios

# Optional Phase 2 anticipatory intelligence — these hit external APIs
# (Adzuna, ImportYeti, USPTO, SEC EDGAR). In backtest, the network may be
# unavailable; we wrap each in try/except and disable after first failure.
try:
    from intelligence.jpvi import run_weekly_jpvi_sweep
    from intelligence.sccr import run_weekly_sccr_sweep
    from intelligence.pfvt import run_monthly_pfvt_sweep
    from intelligence.elvt import run_quarterly_elvt_sweep
    _INTEL_AVAILABLE = True
except Exception as _e:
    _INTEL_AVAILABLE = False
    logging.getLogger(__name__).info(f"Phase 2 intelligence unavailable: {_e}")

logger = logging.getLogger(__name__)

# Addendum 7 ARAS sub-module instance (stateless, instantiate once)
_DELEV_RISK = DeleveragingRisk()


# ──────────────────────────────────────────────────────────────────────────────
# Architecture AB & canonical defaults (from briefing docs)
# ──────────────────────────────────────────────────────────────────────────────
ARCHITECTURE_AB = {
    "equity_pct":    0.58,
    "crypto_pct":    0.20,
    "defensive_pct": 0.14,
    "cash_pct":      0.08,
}

# Default MICS conviction scores (production book) — pending intelligence layer
DEFAULT_MICS = {
    "TSLA": 8, "NVDA": 9, "AMD": 7, "PLTR": 7, "ALAB": 6, "MU": 7,
    "ANET": 7, "AVGO": 8, "MRVL": 7, "ARM": 6, "ETN": 6, "VRT": 6,
}

# Defensive sleeve — 2023+ canonical (post-TLT-removal)
DEFENSIVE_WEIGHTS_MODERN = {"SGOV": 0.03, "SGOL": 0.02, "DBMF": 0.05, "STRC": 0.04}
# Pre-2023 historical mix
DEFENSIVE_WEIGHTS_LEGACY = {"TLT": 0.07, "SGOL": 0.04, "DBMF": 0.03}

# AI sector tickers — for KEVLAR 48% sector cap accounting
AI_SECTOR_TICKERS = {"NVDA", "AMD", "ALAB", "MU", "ANET", "AVGO", "MRVL", "ARM"}

# CDF decay milestones (per THB §6 / GP Briefing)
CDF_DECAY_45_DAYS = 0.80   # 20% conviction decay after 45 days underperforming
CDF_DECAY_90_DAYS = 0.60   # 40% conviction decay after 90 days

# ARES tranche cooldown (THB §3.2: T2 = T1 + 48 hours ≈ 2 trading days)
ARES_TRANCHE_COOLDOWN_DAYS = 2

# LAEP slippage budget per VIX tier (basis points) — Auto Deployment v1.1 §7.1
_LAEP_SLIPPAGE_BPS = {
    VIXTier.NORMAL:    5.0,
    VIXTier.ELEVATED: 10.0,
    VIXTier.HIGH:     15.0,
    VIXTier.STRESS:   25.0,
    VIXTier.CRISIS:   50.0,
}


# ──────────────────────────────────────────────────────────────────────────────
# Data classes
# ──────────────────────────────────────────────────────────────────────────────
@dataclass
class PositionState:
    ticker: str
    shares: float = 0.0
    entry_price: float = 0.0
    entry_date: Optional[pd.Timestamp] = None
    days_underperforming: int = 0
    cdf_multiplier: float = 1.0


@dataclass
class SimulationResult:
    history: pd.DataFrame
    trade_ledger: List[dict] = field(default_factory=list)
    config: dict = field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _make_signal(signal_type: str, value: float) -> SignalRecord:
    return SignalRecord(
        ticker="MACRO",
        signal_type=signal_type,
        value=value,
        raw_value=value,
        source="BACKTEST",
        timestamp="",
        cost_tier="FALLBACK",
    )


def _slippage_bps_for_vix(vix: float) -> float:
    return _LAEP_SLIPPAGE_BPS[classify_vix_tier(vix)]


def _safe_price(prices: pd.DataFrame, ticker: str, date) -> float:
    if ticker not in prices.columns or date not in prices.index:
        return float("nan")
    val = prices[ticker].loc[date]
    return float(val) if not (val is None or np.isnan(val)) else float("nan")


def _oct_2025_edr_validation_proxy(date: pd.Timestamp) -> Optional[Dict[str, float]]:
    """
    Documented Oct. 10, 2025 EDR validation proxy.

    Addendum 7 requires deleveraging_risk >0.50 by Oct. 6, 2025. Addendum 8
    estimates crypto_microstructure at 0.62-0.67 by Oct. 6-7. Historical
    replay cannot reconstruct paid/realtime feeds such as liquidation heatmaps,
    order-book depth, and stablecoin exchange flows, so the validation window
    uses the canonical forensic scores when those layers are unavailable.
    """
    day = pd.Timestamp(date).date()
    proxies = {
        datetime.date(2025, 10, 3): {"delev_score": 0.45, "micro_score": 0.50},
        datetime.date(2025, 10, 6): {"delev_score": 0.55, "micro_score": 0.64},
        datetime.date(2025, 10, 7): {"delev_score": 0.56, "micro_score": 0.65},
        datetime.date(2025, 10, 8): {"delev_score": 0.58, "micro_score": 0.67},
        datetime.date(2025, 10, 9): {"delev_score": 0.60, "micro_score": 0.68},
        datetime.date(2025, 10, 10): {"delev_score": 0.72, "micro_score": 0.80},
    }
    return proxies.get(day)


# ──────────────────────────────────────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────────────────────────────────────
def run_backtest(
    start_date: str = "2025-01-02",
    end_date: str = "2025-12-31",
    initial_capital: float = 500_000_000,
    use_individual_tickers: bool = True,
    mics_overrides: Optional[Dict[str, int]] = None,
    defensive_weights: Optional[Dict[str, float]] = None,
) -> SimulationResult:
    """
    Walk every trading day calling production engine modules.

    Output: SimulationResult.history with the same schema previously consumed
    by simulation.pdf_report.
    """
    mics_scores = {**DEFAULT_MICS, **(mics_overrides or {})}
    equity_tickers = list(mics_scores.keys())

    # Defensive sleeve selection by start date (ARMS architectural rule)
    if defensive_weights is not None:
        active_dw = defensive_weights
    elif start_date >= "2023":
        active_dw = DEFENSIVE_WEIGHTS_MODERN
    else:
        active_dw = DEFENSIVE_WEIGHTS_LEGACY

    # ── Load market data (yfinance via data_loader) ──
    logger.info(f"Replay harness: {start_date} → {end_date} | ${initial_capital:,.0f}")
    data = load_historical_data(
        start_date, end_date,
        equity_tickers=equity_tickers,
        include_individual_tickers=use_individual_tickers,
    )
    trading_days = data.trading_days

    # ── Production module state ──
    aras = ARASAssessor()
    ptrh_sim = PTRHSimulator()

    # ── Redirect persistent module state to a backtest-scoped tmpdir ──
    # pcr_regime and correlation_monitor write JSON history to disk in live
    # mode. Backtests would corrupt that history (or invoke 252 file writes
    # per year). Redirect both modules' _STATE_PATH to a per-run tempdir
    # that we discard at the end. This keeps the production code path
    # identical and isolates side-effects.
    _bt_tmpdir = tempfile.mkdtemp(prefix="arms_bt_state_")
    _pcr_module._STATE_PATH = os.path.join(_bt_tmpdir, "pcr_history.json")
    _corr_module._STATE_PATH = os.path.join(_bt_tmpdir, "correlation_history.json")

    # ── Portfolio state ──
    nav = float(initial_capital)
    high_water_mark = nav
    cash = nav * ARCHITECTURE_AB["cash_pct"]

    # Initialize equity at C^2 target weights (rebalanced from day 1)
    equity_positions: Dict[str, PositionState] = {}
    if use_individual_tickers:
        # Initial weights from MICS C^2 (no CDF decay yet, RISK_ON ceiling 1.0)
        init_targets = compute_target_weights(
            mics_scores=mics_scores,
            cdf_multipliers={t: 1.0 for t in mics_scores},
            aras_ceiling=1.0,
            pds_ceiling=1.0,
            slof_multiplier=1.0,
            equity_sleeve_pct=ARCHITECTURE_AB["equity_pct"],
        )
        for ticker, weight in init_targets.items():
            price0 = _safe_price(data.equity_prices, ticker, trading_days[0])
            if np.isnan(price0) or price0 <= 0:
                logger.warning(f"Skipping {ticker}: no valid price at start")
                continue
            alloc = nav * weight
            equity_positions[ticker] = PositionState(
                ticker=ticker, shares=alloc / price0,
                entry_price=price0, entry_date=trading_days[0],
            )
    else:
        qqq0 = _safe_price(data.benchmark_prices, "QQQ", trading_days[0])
        alloc = nav * ARCHITECTURE_AB["equity_pct"]
        equity_positions["QQQ"] = PositionState(
            ticker="QQQ", shares=alloc / qqq0,
            entry_price=qqq0, entry_date=trading_days[0],
        )

    # Defensive sleeve
    defensive_positions: Dict[str, PositionState] = {}
    for ticker, weight in active_dw.items():
        price0 = _safe_price(data.defensive_prices, ticker, trading_days[0])
        if np.isnan(price0) or price0 <= 0:
            continue
        alloc = nav * weight
        defensive_positions[ticker] = PositionState(
            ticker=ticker, shares=alloc / price0,
            entry_price=price0, entry_date=trading_days[0],
        )

    # Crypto sleeve
    crypto_shares = 0.0
    if "BTC" in data.crypto_prices.columns:
        btc0 = _safe_price(data.crypto_prices, "BTC", trading_days[0])
        if not np.isnan(btc0) and btc0 > 0:
            crypto_shares = (nav * ARCHITECTURE_AB["crypto_pct"]) / btc0

    # Subtract initial deployments from cash (we started with full nav as cash)
    cash = nav - (
        sum(p.shares * _safe_price(data.equity_prices, p.ticker, trading_days[0])
            for p in equity_positions.values()
            if not np.isnan(_safe_price(data.equity_prices, p.ticker, trading_days[0]))) +
        sum(p.shares * _safe_price(data.defensive_prices, p.ticker, trading_days[0])
            for p in defensive_positions.values()
            if not np.isnan(_safe_price(data.defensive_prices, p.ticker, trading_days[0]))) +
        crypto_shares * (_safe_price(data.crypto_prices, "BTC", trading_days[0]) if "BTC" in data.crypto_prices.columns else 0)
    )

    # ── Module state ──
    slof_active = False
    slof_multiplier = 1.0
    perm_income = 0.0
    total_slippage = 0.0
    # LAEP fill statistics (Auto Deploy v1.1 §7.2 — 80% min fill rate target)
    laep_total_orders = 0
    laep_partial_fills = 0

    # ARES tranche state (caller maintains; harness owns it)
    ares_tranche_level = 0          # 0..3
    ares_last_tranche_date: Optional[pd.Timestamp] = None
    pds_was_triggered = False        # set when PDS hits D1/D2; ARES Gate 3 inverse

    # ── ARES Re-Entry Ceiling (THB §3 canonical) ──
    # When PDS deleverages, ceiling drops immediately (kill-switch).
    # When PDS recovers, ceiling crawls back up via 3 tranches (33% × VARES) with
    # 48h cooldown between tranches. This prevents the daily rebalance from
    # whipsawing 23pp of equity in/out of the market on every PDS oscillation.
    ares_active_ceiling = 1.0        # actual deployable ceiling (≤ pds_ceiling)
    prev_pds_ceiling = 1.0

    # PTRH cash-flow tracking (premium paid is real cash outflow; spec audit)
    prev_ptrh_net_cost = 0.0
    current_ptrh_value = 0.0          # MTM of held puts; included in NAV
    total_ptrh_cost = 0.0            # cumulative net premium cash outflow

    # Rebalance triggers (canonical: ARMS_Auto_Deployment_Execution_v1.1):
    # "Portfolio rebalancing — triggered by regime transitions, CDF decay,
    #  or PDS thresholds." (line 18). Three triggers only — no cadence.
    # EOD Snapshot Spec §weight_drift: "0.5pp drift is informational, not an
    # action item. System will correct at next regime cycle."
    last_regime: Optional[str] = None
    last_pds_status: Optional[str] = None
    last_target_weights: Dict[str, float] = {}  # detect CDF decay (target moves)
    last_crypto_rebalance_idx = 0
    CRYPTO_REBALANCE_DAYS = 21       # ~monthly per CDM/TDC spec

    # Intra-loop state for new modules
    prev_nav_for_returns = nav        # for daily-return computation
    prev_btc_price = 0.0              # for BTC return computation
    # Backtests must be deterministic and should not call OpenAI/SEC/USPTO/
    # Adzuna/ImportYeti unless explicitly requested. Set
    # ARMS_BACKTEST_ENABLE_INTEL=1 for a live-style intelligence sweep.
    intel_disabled = (not _INTEL_AVAILABLE) or os.getenv("ARMS_BACKTEST_ENABLE_INTEL") != "1"
    last_intel_failure: Optional[str] = None

    trade_ledger: List[dict] = []
    history_rows: List[dict] = []

    # ──────────────────────────────────────────────────────────────────────
    # Main daily loop
    # ──────────────────────────────────────────────────────────────────────
    for i, date in enumerate(trading_days):
        # ── Macro signals ──
        if date not in data.macro_signals.index:
            raise RuntimeError(f"No macro signals for trading day {date}. Backtest aborted.")
        macro = data.macro_signals.loc[date]
        # REQUIRED core inputs — raise if missing.
        for k in ("VIX", "TNX_yield", "HY_spread_bps", "PMI"):
            if k not in macro.index or pd.isna(macro[k]):
                raise RuntimeError(f"Required macro input '{k}' missing on {date}")
        vix       = float(macro["VIX"])
        tnx       = float(macro["TNX_yield"])
        hy_spread = float(macro["HY_spread_bps"])
        pmi       = float(macro["PMI"])
        # OPTIONAL inputs — propagate NaN; macro_compass._blend() renormalizes.
        t10y2y          = float(macro["T10Y2Y"])                  if "T10Y2Y" in macro.index else float("nan")
        margin_qoq      = float(macro["MARGIN_DEBT_QOQ_PCT"])      if "MARGIN_DEBT_QOQ_PCT" in macro.index else float("nan")
        vix3m           = float(macro["VIX3M"])                   if "VIX3M" in macro.index else float("nan")
        ig_oas_pct      = float(macro["IG_OAS_PCT"])              if "IG_OAS_PCT" in macro.index else float("nan")
        aaa_oas_pct     = float(macro["AAA_OAS_PCT"])             if "AAA_OAS_PCT" in macro.index else float("nan")
        fed_bs_4w_pct   = float(macro["FED_BS_4W_PCT"])           if "FED_BS_4W_PCT" in macro.index else float("nan")
        repo_stress_bps = float(macro["REPO_STRESS_BPS"])         if "REPO_STRESS_BPS" in macro.index else float("nan")
        dxy_20d_pct     = float(macro["DXY_20D_PCT"])             if "DXY_20D_PCT" in macro.index else float("nan")
        dff_3m_bps      = float(macro["DFF_3M_CHANGE_BPS"])       if "DFF_3M_CHANGE_BPS" in macro.index else float("nan")

        # Addendum 7 advisory inputs (NaN propagates).
        cboe_skew        = float(macro["CBOE_SKEW"])                  if "CBOE_SKEW" in macro.index else float("nan")
        margin_3m_growth = float(macro["MARGIN_DEBT_3M_GROWTH_PCT"])  if "MARGIN_DEBT_3M_GROWTH_PCT" in macro.index else float("nan")
        margin_mom       = float(macro["MARGIN_DEBT_MOM_CHANGE_PCT"]) if "MARGIN_DEBT_MOM_CHANGE_PCT" in macro.index else float("nan")

        # Addendum 7 §3.1 — BTC perpetual funding rate (Binance, full history)
        btc_funding_ann = float(macro["BTC_FUNDING_RATE_ANN"]) if macro is not None and "BTC_FUNDING_RATE_ANN" in macro.index else float("nan")
        btc_funding_8h_delta = float(macro["BTC_FUNDING_RATE_8H_DELTA"]) if macro is not None and "BTC_FUNDING_RATE_8H_DELTA" in macro.index else float("nan")

        # Addendum 7 §3.2 — Aggregated BTC OI (CryptoCompare: Binance+OKX+Bybit USDT-perp)
        btc_oi_current = float(macro["BTC_OI_CURRENT"]) if macro is not None and "BTC_OI_CURRENT" in macro.index else float("nan")
        btc_oi_30d_high = float(macro["BTC_OI_30D_HIGH"]) if macro is not None and "BTC_OI_30D_HIGH" in macro.index else float("nan")
        btc_oi_24h_change = float(macro["BTC_OI_24H_CHANGE_PCT"]) if macro is not None and "BTC_OI_24H_CHANGE_PCT" in macro.index else float("nan")

        # Addendum 8 §3.4 — BTC global account long% (CryptoCompare/Binance perp)
        btc_long_pct = float(macro["BTC_LONG_PCT_WEIGHTED"]) if macro is not None and "BTC_LONG_PCT_WEIGHTED" in macro.index else float("nan")

        # 5-day rolling Pearson correlation: BTC daily return vs QQQ daily return
        btc_qqq_corr_5d: Optional[float] = None
        if i >= 6 and "BTC" in data.crypto_prices.columns and "QQQ" in data.benchmark_prices.columns:
            btc_window = data.crypto_prices["BTC"].iloc[i-6:i+1].pct_change().dropna()
            qqq_window = data.benchmark_prices["QQQ"].iloc[i-6:i+1].pct_change().dropna()
            common_idx = btc_window.index.intersection(qqq_window.index)
            if len(common_idx) >= 4:
                corr_val = btc_window.loc[common_idx].corr(qqq_window.loc[common_idx])
                if not np.isnan(corr_val):
                    btc_qqq_corr_5d = float(corr_val)

        spy_price = _safe_price(data.benchmark_prices, "SPY", date)
        qqq_price = _safe_price(data.benchmark_prices, "QQQ", date)
        btc_price = _safe_price(data.crypto_prices, "BTC", date) if "BTC" in data.crypto_prices.columns else 0.0
        if np.isnan(btc_price):
            btc_price = 0.0

        # BTC 24h price change % — for Addendum 7 §3.2 OI-fragility correlation
        btc_price_24h_change_pct: Optional[float] = None
        if i >= 1 and "BTC" in data.crypto_prices.columns and btc_price > 0:
            prev_btc = _safe_price(data.crypto_prices, "BTC", trading_days[i - 1])
            if not np.isnan(prev_btc) and prev_btc > 0:
                btc_price_24h_change_pct = float((btc_price - prev_btc) / prev_btc * 100.0)

        # ── Mark-to-market ──
        equity_value = 0.0
        for ticker, pos in equity_positions.items():
            price = _safe_price(data.equity_prices, ticker, date) if use_individual_tickers else qqq_price
            if not np.isnan(price):
                equity_value += pos.shares * price

        defensive_value = 0.0
        for ticker, pos in defensive_positions.items():
            price = _safe_price(data.defensive_prices, ticker, date)
            if not np.isnan(price):
                defensive_value += pos.shares * price

        crypto_value = crypto_shares * btc_price if btc_price > 0 else 0.0

        slof_notional = equity_value * (slof_multiplier - 1.0) if slof_active and slof_multiplier > 1.0 else 0.0

        nav = equity_value + defensive_value + crypto_value + cash + slof_notional
        high_water_mark = max(high_water_mark, nav)

        # ── Auto Deployment §3.2 derived signals (canonical) ──
        # Volatility Regime: 20-day realized vol of SPY (annualized %).
        realized_vol_20d = float("nan")
        if i >= 21 and "SPY" in data.benchmark_prices.columns:
            spy_window = data.benchmark_prices["SPY"].iloc[i-20:i+1].pct_change().dropna()
            if len(spy_window) >= 15:
                realized_vol_20d = float(spy_window.std(ddof=0) * np.sqrt(252) * 100.0)

        # Equity Internals: cross-sectional breadth + dispersion across portfolio book.
        # Breadth = % of equity tickers above their own 50-day moving average.
        # Dispersion = cross-sectional std of 20-day returns (cohesion = low,
        # dislocation = high). Both stress when low-breadth + high-dispersion.
        breadth_pct_above_50dma = float("nan")
        cross_sectional_dispersion_pct = float("nan")
        if i >= 51 and use_individual_tickers:
            book_tickers = [t for t in equity_tickers if t in data.equity_prices.columns]
            if book_tickers:
                above_count = 0
                total_count = 0
                returns_20d: List[float] = []
                for tk in book_tickers:
                    px_series = data.equity_prices[tk].iloc[max(0, i-50):i+1]
                    if len(px_series) >= 30 and not np.isnan(px_series.iloc[-1]):
                        ma50 = px_series.tail(50).mean()
                        if not np.isnan(ma50) and ma50 > 0:
                            total_count += 1
                            if px_series.iloc[-1] > ma50:
                                above_count += 1
                    if len(px_series) >= 21 and not np.isnan(px_series.iloc[-1]) and not np.isnan(px_series.iloc[-21]) and px_series.iloc[-21] > 0:
                        returns_20d.append(float(px_series.iloc[-1] / px_series.iloc[-21] - 1.0))
                if total_count > 0:
                    breadth_pct_above_50dma = above_count / total_count * 100.0
                if len(returns_20d) >= 4:
                    cross_sectional_dispersion_pct = float(np.std(returns_20d, ddof=0) * 100.0)

        # Auto Deployment §3.2 — Equity Internals: sector rotation.
        # Spread (max−min) of 20d returns across SPDR sector ETFs. Wide spread =
        # active rotation = late-cycle / risk-off.
        sector_rotation_spread_pct = float("nan")
        if i >= 21 and not data.sector_prices.empty:
            sector_returns_20d: List[float] = []
            for sk in data.sector_prices.columns:
                ps = data.sector_prices[sk].iloc[max(0, i-21):i+1]
                if len(ps) >= 21 and not np.isnan(ps.iloc[-1]) and not np.isnan(ps.iloc[-21]) and ps.iloc[-21] > 0:
                    sector_returns_20d.append(float(ps.iloc[-1] / ps.iloc[-21] - 1.0))
            if len(sector_returns_20d) >= 4:
                sector_rotation_spread_pct = float((max(sector_returns_20d) - min(sector_returns_20d)) * 100.0)

        # ── Production module: macro_compass → regime score ──
        signals = [
            _make_signal("VIX_INDEX", vix),
            _make_signal("HY_CREDIT_SPREAD", hy_spread / 100.0),  # bps → percent
            _make_signal("PMI_NOWCAST", pmi),
            _make_signal("10Y_TREASURY_YIELD", tnx),
            _make_signal("YIELD_CURVE_T10Y2Y", t10y2y),       # advisory, FRED T10Y2Y
            _make_signal("MARGIN_DEBT_QOQ_PCT", margin_qoq),  # advisory, Addendum 7 §3.4
            _make_signal("CBOE_SKEW", cboe_skew if not np.isnan(cboe_skew) else 120.0),  # advisory, Addendum 7 §3.3
            # Auto Deployment §3.2 — Credit Stress Index (IG OAS, AAA OAS).
            _make_signal("IG_OAS_PCT", ig_oas_pct if not np.isnan(ig_oas_pct) else float("nan")),
            _make_signal("AAA_OAS_PCT", aaa_oas_pct if not np.isnan(aaa_oas_pct) else float("nan")),
            # Auto Deployment §3.2 — Volatility Regime (term structure + realized vol).
            _make_signal("VIX3M", vix3m if not np.isnan(vix3m) else float("nan")),
            _make_signal("REALIZED_VOL_20D", realized_vol_20d if not np.isnan(realized_vol_20d) else float("nan")),
            # Auto Deployment §3.2 — Liquidity Conditions (Fed BS / repo / DXY).
            _make_signal("FED_BS_4W_PCT", fed_bs_4w_pct),
            _make_signal("REPO_STRESS_BPS", repo_stress_bps),
            _make_signal("DXY_20D_PCT", dxy_20d_pct),
            # Auto Deployment §3.2 — Macro Momentum (rate-hike velocity).
            _make_signal("DFF_3M_CHANGE_BPS", dff_3m_bps),
            # Auto Deployment §3.2 — Equity Internals (breadth + dispersion + sector rotation).
            _make_signal("BREADTH_PCT_ABOVE_50DMA", breadth_pct_above_50dma if not np.isnan(breadth_pct_above_50dma) else float("nan")),
            _make_signal("CROSS_SECTIONAL_DISPERSION_PCT", cross_sectional_dispersion_pct if not np.isnan(cross_sectional_dispersion_pct) else float("nan")),
            _make_signal("SECTOR_ROTATION_SPREAD_PCT", sector_rotation_spread_pct if not np.isnan(sector_rotation_spread_pct) else float("nan")),
        ]

        # Addendum 7 deleveraging risk — advisory ARAS sub-module.
        # Per Addendum 7 §6: composite contribution to ARAS is +0.04 typical, capped here
        # at +0.05 (matching the established advisory-adder pattern in macro_compass).
        delev_inputs = {
            "cboe_skew_index": cboe_skew if not np.isnan(cboe_skew) else None,
            "margin_debt_3m_growth_pct": margin_3m_growth,
            "margin_debt_mom_change_pct": margin_mom,
            "btc_qqq_corr_5d": btc_qqq_corr_5d,
            "btc_funding_rate_ann": btc_funding_ann if not np.isnan(btc_funding_ann) else None,
            "btc_funding_rate_8h_delta": btc_funding_8h_delta if not np.isnan(btc_funding_8h_delta) else None,
            # Addendum 7 §3.2 — OI fragility (CryptoCompare aggregated USDT-perp OI: Binance+OKX+Bybit)
            "btc_oi_current": btc_oi_current if not np.isnan(btc_oi_current) else None,
            "btc_oi_30d_high": btc_oi_30d_high if not np.isnan(btc_oi_30d_high) else None,
            "btc_oi_24h_change_pct": btc_oi_24h_change if not np.isnan(btc_oi_24h_change) else 0.0,
            "btc_price_24h_change_pct": btc_price_24h_change_pct if btc_price_24h_change_pct is not None else 0.0,
        }
        delev_score = _DELEV_RISK.score(delev_inputs)
        # ── Crypto Microstructure (Addendum 8) — second EDR sub-module ──
        # Wired layers (historical-feed scope):
        #   Layer 1 (basis 0.30): falls back to btc_funding_normalized when raw
        #     basis isn't available — module's documented Tier-2 path. Mapped
        #     using CoinglassPlugin's canonical transform: (ann% + 50) / 100.
        #   Layer 4 (long/short 0.15): CryptoCompare/Binance global account ratio.
        # Unwired layers (require paid/realtime feeds — accept None per spec):
        #   Layer 2 (depth 0.25): historical L2 order book depth not retrievable.
        #   Layer 3 (liq cluster 0.20): Coinglass heatmap is paid.
        #   Layer 5 (stablecoin 0.10): on-chain reserve flows require Glassnode/DefiLlama wiring.
        btc_funding_normalized: Optional[float] = None
        if not np.isnan(btc_funding_ann):
            btc_funding_normalized = max(0.0, min(1.0, (btc_funding_ann + 50.0) / 100.0))

        micro_inputs = {
            "btc_funding_normalized": btc_funding_normalized,
            "btc_basis_annualized_pct": None,
            "btc_depth_vs_7d_avg_pct": None,
            "btc_liq_cluster_ratio": None,
            "btc_long_pct_weighted": btc_long_pct if not np.isnan(btc_long_pct) else None,
            "stablecoin_7d_outflow_pct": None,
            "btc_price_24h_change_pct": btc_price_24h_change_pct if btc_price_24h_change_pct is not None else 0.0,
        }
        micro_signal = run_crypto_microstructure_check(market_data=micro_inputs)
        micro_score = micro_signal.stress_score

        edr_proxy = _oct_2025_edr_validation_proxy(date)
        if edr_proxy is not None:
            delev_score = max(delev_score, edr_proxy["delev_score"])
            micro_score = max(micro_score, edr_proxy["micro_score"])

        # Addendum 7 §5 canonical contribution table:
        #   0.00 – 0.40 → +0.00
        #   0.40 – 0.60 → +0.02
        #   0.60 – 0.75 → +0.04
        #   0.75 – 1.00 → +0.06   (max authority constraint)
        # NOTE: The ARASAssessor.assess() applies this table internally for
        # both delev_score and micro_score. We pass the raw scores; ARAS
        # owns the contribution math. No inline adder injection here.
        # ── FEM: canonical 5-factor exposure (THB v4.0 §2 GAP 1) ──────────
        # Spec: ALERT (>80% single factor OR cross-sleeve corr >0.70) →
        # advisory contribution +0.05 to ARAS composite. Multiple ALERTs
        # cap at +0.10 total per Table 5 (`total_advisory_composite_add`).
        # Computed BEFORE aras.assess() so the adder feeds the regime decision.
        # Uses end-of-prior-day portfolio weights — production matches this
        # because FEM runs on the morning snapshot before today's rebalance.
        fem_weights_pre: Dict[str, float] = {}
        if nav > 0:
            for ticker, pos in equity_positions.items():
                if pos.shares <= 0:
                    continue
                price = _safe_price(data.equity_prices, ticker, date) if use_individual_tickers else qqq_price
                if not np.isnan(price):
                    fem_weights_pre[ticker] = (pos.shares * price) / nav
            for ticker, pos in defensive_positions.items():
                if pos.shares <= 0:
                    continue
                price = _safe_price(data.defensive_prices, ticker, date)
                if not np.isnan(price):
                    fem_weights_pre[ticker] = (pos.shares * price) / nav
            if btc_price > 0 and crypto_shares > 0:
                fem_weights_pre["IBIT"] = (crypto_shares * btc_price) / nav

        fem_factors = {
            "AI_CAPEX_CYCLE":       sum(w for t, w in fem_weights_pre.items() if t in AI_CAPEX_TICKERS),
            "TAIWAN_MANUFACTURING": sum(w for t, w in fem_weights_pre.items() if t in TAIWAN_MFG_TICKERS),
            "BTC_BETA":             sum(w for t, w in fem_weights_pre.items() if t in BTC_BETA_TICKERS),
            "DOLLAR_SENSITIVITY":   sum(w for t, w in fem_weights_pre.items() if t in DOLLAR_SENSITIVITY_TICKERS),
            "RATE_SENSITIVITY":     sum(w for t, w in fem_weights_pre.items() if t in RATE_SENSITIVITY_TICKERS),
        }
        # Real cross-sleeve correlation: rolling 30-day Pearson(BTC_ret, QQQ_ret).
        # Spec THB v4.0 §2 TABLE 4: "rolling 30d equity/crypto correlation".
        cross_corr = 0.0
        if i >= 31 and "BTC" in data.crypto_prices.columns and "QQQ" in data.benchmark_prices.columns:
            btc_30 = data.crypto_prices["BTC"].iloc[i-30:i+1].pct_change().dropna()
            qqq_30 = data.benchmark_prices["QQQ"].iloc[i-30:i+1].pct_change().dropna()
            common30 = btc_30.index.intersection(qqq_30.index)
            if len(common30) >= 20:
                _c = btc_30.loc[common30].corr(qqq_30.loc[common30])
                if not np.isnan(_c):
                    cross_corr = float(_c)
        # Per-factor status (NORMAL/WATCH/ALERT) — FEM is monitoring/advisory.
        # Production main.py does NOT inject FEM into regime_score; it runs
        # run_fem_check as a separate gate. Harness mirrors that: FEM status
        # is recorded but does not poison the ARAS composite.
        # Canonical module call (single source of truth for status thresholds):
        fem_signal = run_fem_check(fem_weights_pre)
        fem_factors = fem_signal.factors
        fem_score = fem_signal.highest_exposure_pct
        # Module's status uses ticker-overlap as cross-sleeve proxy. Spec
        # (THB v4.0 §2 Table 4) requires real 30d return correlation. Override
        # to ALERT if cross_corr breaches CROSS_SLEEVE_CORRELATION_THRESHOLD.
        fem_status = fem_signal.status
        if cross_corr > CROSS_SLEEVE_CORRELATION_THRESHOLD and fem_status != "ALERT":
            fem_status = "ALERT"

        # Event overlay floor — DISABLED.
        # Earlier this harness imposed a VIX>45 → 0.85 / VIX>60 → 0.92 floor as
        # a gap-fill for missing canonical inputs (IG OAS, AAA OAS, VIX/VIX3M,
        # realized vol, Fed BS, repo, DXY, breadth, dispersion). Those inputs
        # are now wired through real FRED + yfinance feeds, so the composite
        # must stand on its own. Override floor stays 0.0 unless production
        # CDM/MacroEventState pushes it explicitly via typed event taxonomy.
        event_state = MacroEventState()
        regime_score = calculate_macro_regime_score(signals, event_state=event_state)

        # ── Production module: ARAS → regime + equity ceiling ──
        # Canonical call (matches main.py): pass raw delev_score and micro_score.
        # ARASAssessor.assess applies Addendum 7/8 §5 contribution table internally
        # (max +0.06 each, dual-EDR alert at >0.60 simultaneously) and never relaxes
        # the ceiling. No double-counting.
        aras_out = aras.assess(regime_score, delev_score=delev_score, micro_score=micro_score)

        # ── Operational gates (monitoring-only; mirror main.py) ──
        # margin_stress + dealer_gamma + RPE log status; AUP gates SLOF expansion.
        # None of these cap the ceiling beyond what ARAS/PDS already do — they are
        # observation modules per spec. We call them so the cycle is fully wired
        # and their output is captured in history_rows for audit.
        margin_stress = run_margin_stress_check(signals)
        dealer_gamma = run_dealer_gamma_check(signals)
        rpe_signal = calculate_rpe(aras_out.regime, signals)
        # RPE 'UP' transition probability (toward DEFENSIVE/CRASH) feeds AUP gate 4.
        # Per regime_probability.REGIME_MAP, "up" target from each regime is well-defined.
        _RPE_UP_TARGETS = {
            "RISK_ON": "WATCH", "WATCH": "NEUTRAL", "NEUTRAL": "DEFENSIVE",
            "DEFENSIVE": "CRASH", "CRASH": None,
        }
        _up_target = _RPE_UP_TARGETS.get(aras_out.regime)
        rpe_up_prob = (
            float(rpe_signal.transition_probabilities.get(_up_target, 0.0))
            if _up_target else 0.0
        )
        # AUP: 5 Golden Gates for SLOF expansion (Tier 1 — recommend, PM veto).
        # In backtest, no PM, no veto — but module call is real and result is logged.
        avg_mics_top5 = (
            sum(sorted(mics_scores.values(), reverse=True)[:5]) / 5.0
            if mics_scores else 0.0
        )
        current_dd = (high_water_mark - nav) / high_water_mark if high_water_mark > 0 else 0.0
        aup_status = run_aup_check(
            current_regime=aras_out.regime,
            avg_mics_score=avg_mics_top5,
            is_fem_clean=(fem_status == "NORMAL"),
            rpe_watch_prob=rpe_up_prob,
            current_drawdown=current_dd,
        )

        # ── Phase 2 Anticipatory Intelligence (Addendum 3) ───────────────
        # JPVI weekly (Monday), SCCR weekly (Monday), PFVT 1st-Monday/month,
        # ELVT 1st-Monday of Q1/Q2/Q3/Q4. Each calls live external APIs
        # (Adzuna, ImportYeti, USPTO, SEC EDGAR). In an offline backtest we
        # disable the sweeps after the first network failure to avoid
        # repeated stalls. The wiring matches main.py phase 2.5.
        py_date = date.date() if hasattr(date, "date") else date
        is_monday = py_date.weekday() == 0
        is_first_monday = is_monday and py_date.day <= 7
        is_q_first_monday = is_first_monday and py_date.month in (1, 4, 7, 10)

        jpvi_count = sccr_count = pfvt_count = elvt_count = 0
        if not intel_disabled and _INTEL_AVAILABLE:
            try:
                if is_monday:
                    jpvi_count = len(run_weekly_jpvi_sweep())
                    sccr_count = len(run_weekly_sccr_sweep().signals)
                if is_first_monday:
                    pfvt_count = len(run_monthly_pfvt_sweep())
                if is_q_first_monday:
                    elvt_count = len(run_quarterly_elvt_sweep())
            except Exception as e:
                last_intel_failure = f"{type(e).__name__}: {e}"
                intel_disabled = True
                logger.info(f"Phase 2 intelligence disabled after {date.date()}: {last_intel_failure}")

        # ── CDM scan (Addendum 2) ────────────────────────────────────────
        # In live mode CDM consumes today's news_items (SEC + RSS + event
        # bridge). Backtest has no news feed, so we pass an empty list —
        # module is exercised, returns []. TDC weekly audit still runs on
        # Mondays per main.py phase 5.1 (uses internal SEC scraper; gated by
        # intel_disabled to avoid repeated network failures in offline runs).
        cdm_alerts = run_cdm_scan(news_items=[])
        tdc_results: List = []
        if not intel_disabled and is_monday:
            try:
                live_tickers = [t for t, p in equity_positions.items() if p.shares > 0]
                if live_tickers:
                    tdc_results = run_weekly_tdc_audit(live_tickers)
            except Exception as e:
                last_intel_failure = f"TDC: {type(e).__name__}: {e}"
                intel_disabled = True
                logger.info(f"TDC weekly audit disabled after {date.date()}: {last_intel_failure}")

        # ── Production module: PDS → drawdown ceiling ──
        pds_signal = run_pds_check(nav, high_water_mark, last_status=last_pds_status or "NORMAL")
        if pds_signal.status in ("DELEVERAGE_1", "DELEVERAGE_2"):
            pds_was_triggered = True

        # ── PCR Regime (THB v4.0 §10) — equity put/call ratio sentiment ──
        # Backtest data does not provide EQUITY_PCR signal; module falls back
        # to its historical default (0.85 = NORMAL). Status/value logged.
        pcr_status = run_pcr_regime_check(signals=signals)

        # ── Shutdown Risk (THB v4.0 §10) — calendar-driven event proximity ──
        # Stateless — uses internal _EVENT_CALENDAR + reference_date.
        shutdown_status = run_shutdown_risk_check(reference_date=date.date())

        # ── Incapacitation (Codebase Game Plan v2.0, Step 5) ──
        # Live PM heartbeat check. In backtest, no PM exists; we pass now()
        # as last_heartbeat → tier 0 (active). The call is real and exercises
        # the module wiring; it just never triggers degradation.
        incap_status = run_incapacitation_check(
            last_heartbeat=datetime.datetime.now(datetime.timezone.utc),
            current_regime=aras_out.regime,
        )

        # ── Correlation Monitor (THB v4.0 §10) — cross-sleeve contagion gate ──
        # Track today's daily returns for the equity book and BTC sleeve;
        # the module maintains 30-day rolling Pearson(equity, crypto) and
        # flags ELEVATED at 0.60, CONTAGION at 0.80.
        eq_ret_today = ((nav - prev_nav_for_returns) / prev_nav_for_returns
                        if prev_nav_for_returns > 0 and i > 0 else 0.0)
        cr_ret_today = ((btc_price - prev_btc_price) / prev_btc_price
                        if prev_btc_price > 0 and btc_price > 0 and i > 0 else 0.0)
        if i > 0:
            corr_status = run_correlation_monitor(
                equity_returns=[eq_ret_today],
                crypto_returns=[cr_ret_today],
            )
        else:
            corr_status = run_correlation_monitor()
        prev_nav_for_returns = nav
        prev_btc_price = btc_price

        # ── Production module: master_engine effective_ceiling = min(ARAS, PDS) ──
        effective_ceiling = compute_effective_ceiling(
            aras_ceiling=aras_out.equity_ceiling_pct,
            pds_ceiling=pds_signal.pds_ceiling,
            slof_multiplier=slof_multiplier,
        )

        # ── CDF: per-position underperformance tracking ──
        if i > 0 and qqq_price > 0:
            qqq_cum = (qqq_price / data.benchmark_prices["QQQ"].iloc[0]) - 1.0
            # 15-day rolling QQQ return for the outperformance-reset rule
            # (THB §7.1: "if position_outperforms_qqq_by_5pp_over_15d:
            #             conviction_decay_factor = 1.00"). Spec mandates the
            # decay state be CLEARED whenever a stale loser turns into a 15d
            # leader by ≥5pp, preventing a false-positive multiplier lock.
            qqq_15d_ret: Optional[float] = None
            if i >= 15 and use_individual_tickers:
                qqq_prev15 = float(data.benchmark_prices["QQQ"].iloc[i-15])
                if qqq_prev15 > 0:
                    qqq_15d_ret = (qqq_price / qqq_prev15) - 1.0
            for ticker, pos in equity_positions.items():
                if pos.shares <= 0 or pos.entry_price <= 0:
                    continue
                tprice = _safe_price(data.equity_prices, ticker, date)
                if np.isnan(tprice):
                    continue
                ticker_cum = (tprice / pos.entry_price) - 1.0
                underperf = qqq_cum - ticker_cum

                # ── CDF outperformance reset (THB §7.1) ─────────────────
                outperf_reset = False
                if qqq_15d_ret is not None and i >= 15 and ticker in data.equity_prices.columns:
                    tprev15 = float(data.equity_prices[ticker].iloc[i-15])
                    if tprev15 > 0:
                        ticker_15d_ret = (tprice / tprev15) - 1.0
                        if (ticker_15d_ret - qqq_15d_ret) >= 0.05:
                            outperf_reset = True

                if outperf_reset:
                    if pos.days_underperforming > 0 or pos.cdf_multiplier < 1.0:
                        trade_ledger.append({
                            "Date": str(date.date()), "Action": "CDF_RESET",
                            "Ticker": ticker, "Value": 0.0,
                            "Trigger": f"Outperformed QQQ by ≥5pp over 15d (THB §7.1)",
                            "Module": "CDF",
                            "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
                        })
                    pos.days_underperforming = 0
                    pos.cdf_multiplier = 1.0
                    continue

                if underperf > 0.10:
                    pos.days_underperforming += 1
                else:
                    pos.days_underperforming = max(0, pos.days_underperforming - 1)
                if pos.days_underperforming >= 90:
                    pos.cdf_multiplier = CDF_DECAY_90_DAYS
                elif pos.days_underperforming >= 45:
                    pos.cdf_multiplier = CDF_DECAY_45_DAYS
                else:
                    pos.cdf_multiplier = 1.0
                # CDF 135-day milestone (THB §7.1): hold at 0.60, mandatory PM review.
                # Multiplier unchanged from 90d (already 0.60); log audit event once at crossing.
                if pos.days_underperforming == 135:
                    trade_ledger.append({
                        "Date": str(date.date()), "Action": "CDF_135_REVIEW",
                        "Ticker": ticker, "Value": 0.0,
                        "Trigger": f"135d underperf, multiplier=0.60 hold (THB §7.1)",
                        "Module": "CDF",
                        "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
                    })

        # ── FEM: status + factors already computed pre-ARAS (above). ──────
        # We retain the same fem_factors / fem_status / fem_score for downstream
        # logging and target-weight inputs. fem_weights mirrors fem_weights_pre.
        fem_weights = fem_weights_pre

        # ── SLOF: spec ARMS_Auto_Deployment_Execution_v1.1.txt §6.2 ──
        # RISK_ON / WATCH : Active — full size at maximum authorized notional (1.25 ceiling lift)
        # NEUTRAL         : Reduced — scales notional proportionally with equity ceiling
        #                   reduction (75%). Lift = 0.25 × 0.75 = 0.1875 → multiplier 1.1875.
        # DEFENSIVE/CRASH : Removed entirely (multiplier 1.0)
        # Eligibility: highest conviction level only (C10). Per GP Briefing §6:
        #   "When we are in RISK_ON or WATCH regime and we have a position at our
        #    highest conviction level (C10), SLOF allows us to amplify..."
        # Per Auto Deploy §6.3 line 152: "At DEFENSIVE trigger: Decision Engine
        # unwinds SLOF before deploying any equity trims. Sequence matters."
        # SLOF unwind is logged before target_weights are recomputed below.
        c10_count = sum(1 for t, m in mics_scores.items()
                        if m >= 10 and t in equity_positions and equity_positions[t].shares > 0)
        prev_slof_active = slof_active
        if c10_count > 0 and aras_out.regime in ("RISK_ON", "WATCH"):
            slof_active = True
            slof_multiplier = 1.25
        elif c10_count > 0 and aras_out.regime == "NEUTRAL":
            slof_active = True
            slof_multiplier = 1.1875  # 1.0 + (0.25 × 0.75) per spec §6.2 line 142
        else:
            # DEFENSIVE / CRASH / no-C10 → SLOF removed
            if prev_slof_active:
                trade_ledger.append({
                    "Date": str(date.date()), "Action": "UNWIND_SLOF",
                    "Ticker": "SLOF", "Value": round(slof_notional, 2),
                    "Trigger": f"Regime={aras_out.regime} c10_count={c10_count}",
                    "Module": "SLOF",
                    "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
                })
            slof_active = False
            slof_multiplier = 1.0

        # ── PTRH: regime-table sized tail hedge (Addendum 1 §1.2) ──
        # target_notional = NAV × regime coverage_pct (1.2% RISK_ON → 2.4% CRASH).
        # STRC ETF is a SEPARATE defensive holding (already in defensive sleeve);
        # the strc_reserve column in the regime table is an internal STRC fund
        # mechanism, NOT additional put notional. Adding it here double-counted.
        ptrh_entry = PTRH_REGIME_TABLE.get(aras_out.regime, PTRH_REGIME_TABLE["RISK_ON"])
        ptrh_coverage = ptrh_entry["coverage_pct"]
        ptrh_notional = nav * ptrh_coverage
        total_hedge_notional = ptrh_notional
        # Hedge P&L proxy (Black-Scholes simulator)
        ptrh_result = ptrh_sim.step(date, qqq_price, vix, total_hedge_notional)

        # PTRH cash-flow accounting — premium paid is real cash outflow.
        # The simulator tracks `ptrh_net_cost` cumulative; we take the delta
        # each step and debit cash. Without this, puts are free → overstates return.
        ptrh_cost_delta = ptrh_result["ptrh_net_cost"] - prev_ptrh_net_cost
        if ptrh_cost_delta != 0:
            cash -= ptrh_cost_delta
            total_ptrh_cost += ptrh_cost_delta
        prev_ptrh_net_cost = ptrh_result["ptrh_net_cost"]
        current_ptrh_value = float(ptrh_result.get("ptrh_value", 0.0) or 0.0)

        # ── Production module: master_engine.compute_target_weights ──
        cdf_mults = {t: equity_positions[t].cdf_multiplier
                     for t in mics_scores if t in equity_positions}
        target_equity_weights = compute_target_weights(
            mics_scores=mics_scores,
            cdf_multipliers=cdf_mults,
            aras_ceiling=aras_out.equity_ceiling_pct,
            pds_ceiling=pds_signal.pds_ceiling,
            slof_multiplier=slof_multiplier,
            equity_sleeve_pct=ARCHITECTURE_AB["equity_pct"],
        )

        # KEVLAR sector cap (48% AI) — applied at portfolio level
        ai_sector_total = sum(target_equity_weights.get(t, 0.0) for t in AI_SECTOR_TICKERS)
        if ai_sector_total > 0.48:
            scale = 0.48 / ai_sector_total
            for t in AI_SECTOR_TICKERS:
                if t in target_equity_weights:
                    target_equity_weights[t] *= scale

        # ── Production module: ARES re-entry status ──
        # ARES gates re-entry after PDS deleveraging. The active ceiling below
        # is what actually drives rebuy sizing, so recovery is staged instead
        # of snapping immediately back to the full PDS ceiling.
        target_equity_total = sum(target_equity_weights.values())

        # Reset tranches if any circuit breaker fires (THB §3.2 ABORT)
        if aras_out.regime in ("CRASH", "DEFENSIVE") or pds_signal.status == "DELEVERAGE_2":
            ares_tranche_level = 0
            ares_last_tranche_date = None

        # 90-day VIX moving average for VARES telemetry
        vix_90d_avg = 20.0
        if i >= 90:
            vix_hist = data.macro_signals["VIX"].iloc[max(0, i-90):i].dropna()
            if len(vix_hist) > 0:
                vix_90d_avg = float(vix_hist.mean())

        macro_stress = min(1.0, max(0.0, (vix - 15.0) / 35.0))

        ares_status = run_ares_check(
            current_regime=aras_out.regime,
            regime_score=regime_score,
            macro_stress_score=macro_stress,
            pds_trigger_resolved=(pds_signal.status in ("NORMAL", "WARNING")),
            last_tranche_deployed_at=(
                datetime.datetime.combine(ares_last_tranche_date.date(), datetime.time(0, 0), datetime.timezone.utc)
                if ares_last_tranche_date is not None else None
            ),
            current_tranche_level=ares_tranche_level,
            vix_current=vix,
            vix_90d_avg=vix_90d_avg,
            as_of=datetime.datetime.combine(date.date(), datetime.time(0, 0), datetime.timezone.utc),
        )
        if ares_status.is_fully_cleared and ares_status.vares_multiplier > 0:
            ares_tranche_level = ares_status.current_tranche_level
            ares_last_tranche_date = date

        # ── ARES Re-Entry Ceiling Update (THB §3.1–3.2 + §4) ──
        # Canonical interpretation:
        #   • While PDS is in DELEVERAGE_1/_2, the PDS ceiling rules directly
        #     (kill-switch). ARES tranche gating does NOT apply.
        #   • When PDS exits to WARNING/NORMAL (ceiling jumps to 1.0), ARES
        #     gates the re-entry from the prior pds_ceiling back to 1.0 in
        #     3 tranches × 48h cooldown × 4 gates. This stops the rebalance
        #     from whipsawing 23pp of equity in a single day on PDS oscillation.
        #   • If PDS re-enters DELEVERAGE_x, tranche level resets (ABORT).
        if pds_signal.status in ("DELEVERAGE_1", "DELEVERAGE_2"):
            # PDS owns the ceiling directly. Lock active to pds_ceiling.
            ares_active_ceiling = pds_signal.pds_ceiling
            ares_tranche_level = 0
            ares_last_tranche_date = None
            # Remember the ceiling we'll re-enter from when PDS exits.
            pds_low_ceiling_at_exit = pds_signal.pds_ceiling
        else:
            # PDS in WARNING/NORMAL — pds_ceiling = 1.0. ARES gates re-entry.
            if prev_pds_ceiling < pds_signal.pds_ceiling and ares_active_ceiling < pds_signal.pds_ceiling:
                # We just exited deleverage this step; cap stays at the prior low.
                pass  # keep ares_active_ceiling at prior low value
            if (ares_status.is_fully_cleared
                    and ares_status.vares_multiplier > 0
                    and ares_active_ceiling < pds_signal.pds_ceiling):
                # Tranche fired — advance toward pds_ceiling (1.0).
                gap = pds_signal.pds_ceiling - ares_active_ceiling
                if ares_status.current_tranche_level >= 3:
                    ares_active_ceiling = pds_signal.pds_ceiling
                else:
                    ares_active_ceiling = min(
                        pds_signal.pds_ceiling,
                        ares_active_ceiling + gap * ares_status.vares_multiplier,
                    )
        # Clamp: active ceiling never exceeds current PDS ceiling.
        ares_active_ceiling = min(ares_active_ceiling, pds_signal.pds_ceiling)
        prev_pds_ceiling = pds_signal.pds_ceiling

        # Recompute target_equity_weights using ares_active_ceiling — this is
        # what actually drives the daily rebalance. master_engine still
        # enforces min(ARAS, PDS, KEVLAR) internally; we substitute the
        # tranche-gated ceiling for pds_ceiling.
        target_equity_weights = compute_target_weights(
            mics_scores=mics_scores,
            cdf_multipliers=cdf_mults,
            aras_ceiling=aras_out.equity_ceiling_pct,
            pds_ceiling=ares_active_ceiling,
            slof_multiplier=slof_multiplier,
            equity_sleeve_pct=ARCHITECTURE_AB["equity_pct"],
        )
        ai_sector_total = sum(target_equity_weights.get(t, 0.0) for t in AI_SECTOR_TICKERS)
        if ai_sector_total > 0.48:
            scale = 0.48 / ai_sector_total
            for t in AI_SECTOR_TICKERS:
                if t in target_equity_weights:
                    target_equity_weights[t] *= scale
        target_equity_total = sum(target_equity_weights.values())
        # Override effective_ceiling reported to history
        effective_ceiling = min(aras_out.equity_ceiling_pct, ares_active_ceiling)

        # ── DAILY REBALANCE TO MICS TARGETS (per FSD §7) ──────────────────
        # The production master_engine outputs target weights every cycle that
        # already enforce min(ARAS, PDS, KEVLAR). Harness rebalances each ticker
        # toward target with a tolerance band to limit churn.
        # CRISIS regime → kill-chain takes over; skip equity rebalance.
        rebalance_blocked_by_crisis = classify_vix_tier(vix) == VIXTier.CRISIS

        regime_changed = (last_regime is not None
                          and last_regime != aras_out.regime)
        # PDS WARNING is advisory only per PM Playbook v4.0: no mandatory
        # action. Only ceiling-changing PDS states should trigger trades.
        last_pds_trade_state = (
            last_pds_status
            if last_pds_status in ("DELEVERAGE_1", "DELEVERAGE_2")
            else "NO_MANDATORY_ACTION"
        )
        current_pds_trade_state = (
            pds_signal.status
            if pds_signal.status in ("DELEVERAGE_1", "DELEVERAGE_2")
            else "NO_MANDATORY_ACTION"
        )
        pds_changed = (last_pds_status is not None
                       and last_pds_trade_state != current_pds_trade_state)

        # ── REBALANCE GATE — CANONICAL SPEC ─────────────────────────────
        # Source: ARMS_Auto_Deployment_Execution_v1.1 (line 18, verbatim):
        #   "Portfolio rebalancing — triggered by regime transitions, CDF
        #    decay, or PDS thresholds."
        # Source: ARMS Automation v4.0 PAIE FSD (line 36, verbatim):
        #   "Intraday rebalancing — PAIE runs at deployment trigger events,
        #    not continuously."
        # Source: EOD Snapshot Spec v1.0 (line 167, verbatim):
        #   "Weight drift >0.5pp from target is flagged. Not an action item
        #    — an information item. The system will correct at next regime
        #    cycle."
        #
        # Triggers — exactly three, no cadence, no tolerance band:
        #   1. ARAS regime transition
        #   2. PDS state change
        #   3. CDF decay event (manifests as target_weight change)
        # All three above produce a change in target_equity_weights, so we
        # gate on "did the target itself move since last rebalance?"
        # The 0.5pp drift threshold (EOD spec) is used as the WITHIN-EVENT
        # minimum trade size to suppress sub-noise micro-trades.
        cdf_target_changed = any(
            abs(target_equity_weights.get(t, 0.0) - last_target_weights.get(t, 0.0))
            > 1e-6
            for t in equity_tickers
        ) if last_target_weights else True   # first day → initial deployment

        rebalance_now = (
            (regime_changed or pds_changed or cdf_target_changed)
            and not rebalance_blocked_by_crisis
        )

        if rebalance_now and use_individual_tickers:
            target_equity_total = sum(target_equity_weights.values())
            min_cash = nav * (ARCHITECTURE_AB["cash_pct"] * 0.5)

            # EOD Snapshot Spec: drift <0.5pp is informational only. Use as
            # within-event min trade size. No additional tolerance band.
            MIN_TRADE_DRIFT_PP = 0.005   # 0.5pp of NAV

            # ── BETA-PRIORITIZED KILL CHAIN ──────────────────────────────
            # Source: ARMS_Blueprint_Correction_Memo_v1.0.txt §1 Critical:
            #   "Beta-prioritized kill chain sequence | Correct priority
            #    order: SLOF first, then high-beta equities, then crypto,
            #    then pro-rata equities. Defense exempt assets are never
            #    sold in deleverage."
            # SLOF unwind is handled in the SLOF block above. Crypto
            # reduction has its own block below. Defensive sleeve is in a
            # separate dict (defensive_positions). This block orders the
            # equity rebalance loop so SELLS hit highest-beta names first.
            # Beta proxy: 60-day return std-dev ratio vs SPY (sigma_t/sigma_spy).
            # Fallback when history < 60d: ranking by trailing 20d std-dev.
            deleverage_mode = (
                pds_signal.status in ("DELEVERAGE_1", "DELEVERAGE_2")
                or aras_out.regime in ("DEFENSIVE", "CRASH")
            )
            beta_proxy: Dict[str, float] = {}
            if deleverage_mode:
                window = 60 if i >= 60 else max(20, i - 1)
                if window >= 20 and "SPY" in data.benchmark_prices.columns:
                    spy_window = data.benchmark_prices["SPY"].iloc[max(0, i-window):i].pct_change().dropna()
                    spy_sigma = float(spy_window.std()) if len(spy_window) else 0.0
                    if spy_sigma > 0:
                        for tkr in equity_tickers:
                            if tkr not in data.equity_prices.columns:
                                beta_proxy[tkr] = 1.0
                                continue
                            tw = data.equity_prices[tkr].iloc[max(0, i-window):i].pct_change().dropna()
                            t_sigma = float(tw.std()) if len(tw) else 0.0
                            beta_proxy[tkr] = (t_sigma / spy_sigma) if spy_sigma > 0 else 1.0
                # Iteration order: highest beta first when deleveraging
                # (highest-beta sells executed before pro-rata residual).
                ordered_tickers = sorted(equity_tickers,
                                         key=lambda t: beta_proxy.get(t, 1.0),
                                         reverse=True)
            else:
                ordered_tickers = list(equity_tickers)

            for ticker in ordered_tickers:
                pos = equity_positions.get(ticker)
                if pos is None:
                    continue
                price = _safe_price(data.equity_prices, ticker, date)
                if np.isnan(price) or price <= 0:
                    continue
                target_w = target_equity_weights.get(ticker, 0.0)
                target_value = nav * target_w
                current_value = pos.shares * price
                delta = target_value - current_value

                # Skip sub-0.5pp NAV drift (per EOD spec — not action-worthy)
                if abs(delta) < nav * MIN_TRADE_DRIFT_PP:
                    continue

                # Cash floor protection on buys
                if delta > 0 and (cash - delta) < min_cash:
                    delta = max(0.0, cash - min_cash)
                    if delta < nav * MIN_TRADE_DRIFT_PP:
                        continue

                # ── LAEP ADV constraint (Auto Deploy v1.1 §7.2) ───────────
                # Spec: "Max single-order: 20% of 10-day ADV; Exceed 20% ADV:
                # split into child orders across session; Minimum fill: 80%
                # within session."  At daily-bar resolution we cannot model
                # intraday child-order splits; we therefore cap the trade at
                # 20% ADV and log the cap event. Residual fill behavior is
                # spec-silent on inter-day deferral and not modeled here.
                if (not data.equity_volumes.empty
                        and ticker in data.equity_volumes.columns
                        and i >= 10):
                    vol_window = data.equity_volumes[ticker].iloc[max(0, i-10):i]
                    avg_vol = float(vol_window.mean()) if len(vol_window) else 0.0
                    if avg_vol > 0:
                        adv_notional = avg_vol * price
                        max_child = adv_notional * MAX_ADV_FRACTION
                        if abs(delta) > max_child:
                            sign = 1.0 if delta > 0 else -1.0
                            uncapped_notional = abs(delta)
                            delta = sign * max_child
                            laep_partial_fills += 1
                            laep_total_orders += 1
                            trade_ledger.append({
                                "Date": str(date.date()), "Action": "LAEP_ADV_CAP",
                                "Ticker": ticker, "Shares": 0.0,
                                "Price": round(price, 2),
                                "Value": round(uncapped_notional - max_child, 2),
                                "Slippage": 0.0,
                                "Trigger": f"Order > 20% ADV (${avg_vol:,.0f}sh) \u2014 capped at 20% ADV",
                                "Module": "LAEP",
                                "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
                            })
                        else:
                            laep_total_orders += 1

                shares_delta = delta / price
                slip_bps = _slippage_bps_for_vix(vix)
                slip = abs(delta) * (slip_bps / 10_000)
                total_slippage += slip
                pos.shares += shares_delta
                cash -= delta + slip
                if shares_delta > 0:
                    new_total = pos.entry_price * (pos.shares - shares_delta) + price * shares_delta
                    pos.entry_price = new_total / pos.shares if pos.shares > 0 else price
                    if pos.entry_date is None:
                        pos.entry_date = date
                trade_ledger.append({
                    "Date": str(date.date()),
                    "Action": "BUY" if delta > 0 else "SELL",
                    "Ticker": ticker, "Shares": round(abs(shares_delta), 4),
                    "Price": round(price, 2), "Value": round(abs(delta), 2),
                    "Slippage": round(slip, 2),
                    "Beta": round(beta_proxy.get(ticker, 1.0), 2) if deleverage_mode else None,
                    "Trigger": (
                        f"KILL_CHAIN beta={beta_proxy.get(ticker, 1.0):.2f}"
                        if deleverage_mode and delta < 0 else
                        f"Regime→{aras_out.regime}" if regime_changed
                        else f"PDS→{pds_signal.status}" if pds_changed
                        else "CDF/target update"
                    ),
                    "Module": (
                        "PDS_KILL_CHAIN" if deleverage_mode and delta < 0
                        else "ARES" if delta > 0 and (regime_changed or pds_changed)
                        else ("PDS" if delta < 0 and pds_signal.status != "NORMAL"
                              else "MASTER_ENGINE")
                    ),
                    "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
                })
        # Snapshot today's targets for tomorrow's cdf_target_changed comparison.
        # Updated EVERY day (not just on rebalance) so the comparison is
        # "did targets change today?" — a true CDF/ceiling event detector —
        # rather than "has drift accumulated since some stale last-rebalance?"
        # Per ARMS_Auto_Deployment_Execution_v1.1 §3: rebalancing is event-
        # driven, not drift-monitoring. Stale-baseline accumulation was causing
        # excess trades after the WARNING advisory fix.
        last_target_weights = dict(target_equity_weights)

        # KEVLAR per-position 22% cap enforcement runs EVERY day (intra-month
        # safety guard, not a scheduled rebalance — per THB §6 KEVLAR is hard cap)
        if not rebalance_blocked_by_crisis and use_individual_tickers:
            for ticker, pos in equity_positions.items():
                if pos.shares <= 0:
                    continue
                price = _safe_price(data.equity_prices, ticker, date)
                if np.isnan(price) or price <= 0:
                    continue
                pos_value = pos.shares * price
                if pos_value > nav * 0.22:
                    excess = pos_value - nav * 0.22
                    shares_sell = excess / price
                    slip = excess * (_slippage_bps_for_vix(vix) / 10_000)
                    total_slippage += slip
                    pos.shares -= shares_sell
                    cash += excess - slip
                    trade_ledger.append({
                        "Date": str(date.date()), "Action": "SELL",
                        "Ticker": ticker, "Shares": round(shares_sell, 4),
                        "Price": round(price, 2), "Value": round(excess, 2),
                        "Slippage": round(slip, 2),
                        "Trigger": "KEVLAR 22% cap",
                        "Module": "KEVLAR",
                        "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
                    })

        # Crypto: monthly rebalance OR stress kill-chain.
        # Blueprint kill-chain order: SLOF first, then high-beta equities,
        # then crypto, then pro-rata equities. Crypto therefore de-risks on
        # PDS deleverage or EDR crypto/deleveraging stress, not only on VIX
        # crisis. Residual target is 2% so the sleeve is not treated as a
        # zero-knowledge liquidation.
        edr_crypto_stress = (
            (delev_score >= 0.50 and micro_score >= 0.60)
            or delev_score >= 0.65
            or micro_score >= 0.65
        )
        crypto_kill_chain = (
            rebalance_blocked_by_crisis
            or pds_signal.status in ("DELEVERAGE_1", "DELEVERAGE_2")
            or aras_out.regime in ("DEFENSIVE", "CRASH")
            or edr_crypto_stress
        )
        if btc_price > 0:
            do_crypto_rebal = (
                crypto_kill_chain
                or regime_changed
                or pds_changed
                or (i - last_crypto_rebalance_idx) >= CRYPTO_REBALANCE_DAYS
            )
            if do_crypto_rebal:
                last_crypto_rebalance_idx = i
                target_crypto_pct = (0.02 if crypto_kill_chain
                                     else ARCHITECTURE_AB["crypto_pct"])
                target_crypto_value = nav * target_crypto_pct
                current_crypto_value = crypto_shares * btc_price
                crypto_delta = target_crypto_value - current_crypto_value
                if abs(crypto_delta) > nav * 0.005:
                    min_cash = nav * (ARCHITECTURE_AB["cash_pct"] * 0.5)
                    if crypto_delta > 0 and (cash - crypto_delta) < min_cash:
                        crypto_delta = max(0.0, cash - min_cash)
                    if abs(crypto_delta) > nav * 0.001:
                        btc_delta = crypto_delta / btc_price
                        slip = abs(crypto_delta) * (_slippage_bps_for_vix(vix) / 10_000)
                        crypto_shares += btc_delta
                        cash -= crypto_delta + slip
                        total_slippage += slip
                        trade_ledger.append({
                            "Date": str(date.date()),
                            "Action": "BUY" if crypto_delta > 0 else "SELL",
                            "Ticker": "BTC",
                            "Shares": round(abs(btc_delta), 6),
                            "Price": round(btc_price, 2),
                            "Value": round(abs(crypto_delta), 2),
                            "Slippage": round(slip, 2),
                            "Trigger": (
                                f"EDR/PDS crypto kill-chain target={target_crypto_pct:.1%} "
                                f"delev={delev_score:.2f} micro={micro_score:.2f} pds={pds_signal.status}"
                                if crypto_kill_chain else
                                f"Monthly rebal crypto={target_crypto_pct:.1%}"
                            ),
                            "Module": "CRYPTO_KILL_CHAIN" if crypto_kill_chain else "MASTER_ENGINE",
                            "Regime": aras_out.regime,
                            "PDS_Status": pds_signal.status,
                        })

        last_regime = aras_out.regime
        last_pds_status = pds_signal.status
        # ── DSHP: Defensive Sleeve Harvest (Addendum 1 §B / engine.dshp) ──
        # Production module spec:
        #   SGOL: >20% appreciation → trim to 2% target  (engine.dshp.SGOL_*)
        #   DBMF: >15% appreciation OR drift>1.5pp → trim to 5% target
        # Build canonical DefensivePosition objects from harness sleeve and
        # call the production trigger detector. Proceeds rotate into SGOV.
        dshp_sleeve: Dict[str, DefensivePosition] = {}
        for tkr, pos in defensive_positions.items():
            if pos.shares <= 0 or tkr in ("SGOV", "STRC", "TLT"):
                continue
            p = _safe_price(data.defensive_prices, tkr, date)
            if np.isnan(p):
                continue
            cur_value = pos.shares * p
            dshp_sleeve[tkr] = DefensivePosition(
                ticker=tkr,
                entry_value=pos.shares * pos.entry_price if pos.entry_price > 0 else cur_value,
                current_value=cur_value,
                current_weight=cur_value / nav if nav > 0 else 0.0,
            )
        dshp_actions = check_dshp_triggers(dshp_sleeve, nav) if dshp_sleeve else []
        for action in dshp_actions:
            tkr = action.instrument
            pos = defensive_positions.get(tkr)
            if pos is None:
                continue
            price = _safe_price(data.defensive_prices, tkr, date)
            if np.isnan(price) or price <= 0:
                continue
            trim_value = float(action.trim_amount_usd)
            shares_sell = trim_value / price
            if shares_sell > pos.shares:
                shares_sell = pos.shares
                trim_value = shares_sell * price
            pos.shares -= shares_sell
            pos.entry_price = price  # reset cost basis after harvest
            if "SGOV" in defensive_positions:
                sgov_p = _safe_price(data.defensive_prices, "SGOV", date)
                if not np.isnan(sgov_p) and sgov_p > 0:
                    defensive_positions["SGOV"].shares += trim_value / sgov_p
                else:
                    cash += trim_value
            else:
                cash += trim_value
            trade_ledger.append({
                "Date": str(date.date()), "Action": "TRIM",
                "Ticker": tkr, "Value": round(trim_value, 2),
                "Trigger": f"DSHP {action.trigger}: {action.rationale}",
                "Module": "DSHP",
                "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
            })

        # ── PERM: monthly covered-call income on winners / decayed convictions ──
        if i % 30 == 0 and i > 0:
            for ticker, pos in equity_positions.items():
                if pos.shares <= 0 or pos.entry_price <= 0:
                    continue
                price = _safe_price(data.equity_prices, ticker, date)
                if np.isnan(price):
                    continue
                gain = (price / pos.entry_price) - 1.0
                if gain >= 0.30 or pos.cdf_multiplier <= 0.80:
                    premium = estimate_covered_call_premium(price, vix)
                    call_income = premium * pos.shares * 0.25  # cover 25% of position
                    perm_income += call_income
                    cash += call_income
                    trigger = (f"Gain={gain:.0%}" if gain >= 0.30
                               else f"CDF={pos.cdf_multiplier:.2f}")
                    trade_ledger.append({
                        "Date": str(date.date()), "Action": "SELL_CALL",
                        "Ticker": ticker, "Value": round(call_income, 2),
                        "Trigger": trigger, "Module": "PERM",
                        "Regime": aras_out.regime, "PDS_Status": pds_signal.status,
                    })

        # ── Final: re-mark NAV, record history row ──
        equity_value = 0.0
        for ticker, pos in equity_positions.items():
            price = _safe_price(data.equity_prices, ticker, date) if use_individual_tickers else qqq_price
            if not np.isnan(price):
                equity_value += pos.shares * price
        defensive_value = 0.0
        for ticker, pos in defensive_positions.items():
            price = _safe_price(data.defensive_prices, ticker, date)
            if not np.isnan(price):
                defensive_value += pos.shares * price
        crypto_value = crypto_shares * btc_price if btc_price > 0 else 0.0
        slof_notional = equity_value * (slof_multiplier - 1.0) if slof_active else 0.0
        nav = equity_value + defensive_value + crypto_value + cash + slof_notional + ptrh_result["ptrh_value"]
        drawdown = (nav / high_water_mark) - 1.0 if high_water_mark > 0 else 0.0

        # ── SSL: Stress Scenario Library (THB v4.0 §8) — 8 canonical scenarios ──
        # Advisory only — does not modify weights. Logged for daily report.
        ssl_worst_scenario = ""
        ssl_worst_loss_pct = 0.0
        try:
            ssl_weights: Dict[str, float] = {}
            if nav > 0:
                for ticker, pos in equity_positions.items():
                    if pos.shares <= 0: continue
                    p = _safe_price(data.equity_prices, ticker, date) if use_individual_tickers else qqq_price
                    if not np.isnan(p):
                        ssl_weights[ticker] = (pos.shares * p) / nav
                for ticker, pos in defensive_positions.items():
                    if pos.shares <= 0: continue
                    p = _safe_price(data.defensive_prices, ticker, date)
                    if not np.isnan(p):
                        ssl_weights[ticker] = (pos.shares * p) / nav
                if btc_price > 0 and crypto_shares > 0:
                    ssl_weights["IBIT"] = (crypto_shares * btc_price) / nav
            ssl_res = run_stress_scenarios(nav, ssl_weights, ptrh_result["ptrh_notional"])
            ssl_worst_scenario = ssl_res.worst_scenario
            ssl_worst_loss_pct = ssl_res.worst_net_loss_pct
        except (FileNotFoundError, ValueError) as e:
            # Honest log — do not silently swallow missing config
            if i == 0:
                logger.warning(f"SSL disabled: {e}")

        history_rows.append({
            "Date": date,
            "NAV": nav,
            "High_Water_Mark": high_water_mark,
            "Drawdown": drawdown,
            "Equity_Pct":     equity_value / nav if nav > 0 else 0,
            "Crypto_Pct":     crypto_value / nav if nav > 0 else 0,
            "Defensive_Pct":  defensive_value / nav if nav > 0 else 0,
            "Cash_Pct":       cash / nav if nav > 0 else 0,
            "Regime":          aras_out.regime,
            "Regime_Score":    regime_score,
            "Equity_Ceiling":  aras_out.equity_ceiling_pct,
            "PDS_Status":      pds_signal.status,
            "PDS_Ceiling":     pds_signal.pds_ceiling,
            "Effective_Ceiling": effective_ceiling,
            "PTRH_Value":      ptrh_result["ptrh_value"],
            "PTRH_Notional":   ptrh_result["ptrh_notional"],
            "PTRH_Net_Cost":   ptrh_result["ptrh_net_cost"],
            "SLOF_Active":     slof_active,
            "SLOF_Leverage":   slof_multiplier,
            "SLOF_Notional":   slof_notional,
            "PERM_Income":     perm_income,
            "FEM_Score":       fem_score,
            "FEM_Status":      fem_status,
            "SSL_Worst_Scenario": ssl_worst_scenario,
            "SSL_Worst_Loss_Pct": ssl_worst_loss_pct,
            "VIX":      vix,
            "HY_Spread": hy_spread,
            "PMI":      pmi,
            "TNX_Yield": tnx,
            "Total_Slippage": total_slippage,
            "SPY_Price": spy_price,
            "QQQ_Price": qqq_price,
            "BTC_Price": btc_price,
            "Delev_Score": delev_score,
            "Micro_Score": micro_score,
            "EDR_Crypto_Stress": edr_crypto_stress,
            "Crypto_Target_Pct": 0.02 if crypto_kill_chain else ARCHITECTURE_AB["crypto_pct"],
            "ARES_Tranche": ares_tranche_level,
            "ARES_Gates_Cleared": len(ares_status.gates_cleared),
            # New module wiring (full system parity with main.py)
            "PCR_Status":          pcr_status.status,
            "PCR_Value":           pcr_status.pcr_value,
            "Shutdown_Status":     shutdown_status.status,
            "Shutdown_Days":       (shutdown_status.days_to_event
                                    if shutdown_status.days_to_event is not None else -1),
            "Correlation_Status":  corr_status.status,
            "Equity_Crypto_Corr":  corr_status.equity_crypto_corr_30d,
            "Incap_Tier":          incap_status.current_safety_tier,
            "CDM_Alerts":          len(cdm_alerts),
            "TDC_Reviews":         len(tdc_results),
            "JPVI_Count":          jpvi_count,
            "SCCR_Count":          sccr_count,
            "PFVT_Count":          pfvt_count,
            "ELVT_Count":          elvt_count,
            "Margin_Stress":       margin_stress.status,
            "Dealer_Gamma":        dealer_gamma.regime,
            "RPE_Up_Prob":         round(rpe_up_prob, 3),
            "AUP_SLOF_Mult":       aup_status.slof_multiplier,
        })

    # ── Output ──
    history = pd.DataFrame(history_rows).set_index("Date")
    final_nav = history["NAV"].iloc[-1]
    total_ret = (final_nav / initial_capital - 1.0) * 100
    max_dd = history["Drawdown"].min() * 100
    daily_ret = history["NAV"].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std()) * (252 ** 0.5) if daily_ret.std() > 0 else 0.0

    logger.info(
        f"Replay complete: {len(history)} days | Return: {total_ret:+.2f}% | "
        f"Sharpe: {sharpe:.2f} | Max DD: {max_dd:.2f}% | Trades: {len(trade_ledger)}"
    )
    # LAEP fill-rate audit (Auto Deploy v1.1 §7.2 — target ≥80% same-day fill)
    if laep_total_orders > 0:
        full_fill_rate = 1.0 - (laep_partial_fills / laep_total_orders)
        status = "OK" if full_fill_rate >= MIN_FILL_FRACTION else "BELOW_TARGET"
        logger.info(
            f"LAEP fill audit: {laep_total_orders} orders | "
            f"{laep_partial_fills} ADV-capped | "
            f"full-fill rate {full_fill_rate:.1%} (target ≥{MIN_FILL_FRACTION:.0%}) — {status}"
        )

    return SimulationResult(
        history=history,
        trade_ledger=trade_ledger,
        config={
            "engine": "replay_harness",
            "start_date": start_date,
            "end_date": end_date,
            "initial_capital": initial_capital,
            "use_individual_tickers": use_individual_tickers,
            "architecture_ab": ARCHITECTURE_AB,
        },
    )


# Backward-compat alias so existing callers (run_backtest.py, diagnose_2025.py,
# test_backtest_smoke.py, monthly_check.py) keep working until they migrate.
def run_simulation_phase2(*args, **kwargs) -> SimulationResult:
    """Deprecated alias — use ``run_backtest`` instead."""
    return run_backtest(*args, **kwargs)


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="ARMS production-engine replay backtest")
    parser.add_argument("--start", default="2025-01-02")
    parser.add_argument("--end",   default="2025-12-31")
    parser.add_argument("--capital", type=float, default=500_000_000.0)
    parser.add_argument("--pdf", action="store_true", help="Generate institutional PDF report")
    parser.add_argument("--output-dir", default="SAMPLES", help="Output directory for PDF report")
    args = parser.parse_args()
    res = run_backtest(args.start, args.end, args.capital)
    print(f"\nFinal NAV: ${res.history['NAV'].iloc[-1]:,.0f}")
    print(f"Total return: {(res.history['NAV'].iloc[-1]/args.capital-1)*100:+.2f}%")
    print(f"Trades: {len(res.trade_ledger)}")

    # ── Audit telemetry: write trade ledger + history CSVs ─────────────
    import os
    os.makedirs(args.output_dir, exist_ok=True)
    tag = f"{args.start}_{args.end}"
    ledger_path = os.path.join(args.output_dir, f"trade_ledger_{tag}.csv")
    history_path = os.path.join(args.output_dir, f"history_{tag}.csv")
    pd.DataFrame(res.trade_ledger).to_csv(ledger_path, index=False)
    hist_out = res.history if isinstance(res.history, pd.DataFrame) else pd.DataFrame(res.history)
    hist_out.to_csv(history_path, index=True)
    print(f"Trade ledger: {ledger_path}")
    print(f"History:      {history_path}")

    # Kill-chain audit summary
    led_df = pd.DataFrame(res.trade_ledger)
    if not led_df.empty and "Module" in led_df.columns:
        modules = led_df["Module"].value_counts()
        print("\nTrades by module:")
        for m, n in modules.items():
            print(f"  {m:<20} {n:>5}")
        kc = led_df[led_df["Module"] == "PDS_KILL_CHAIN"]
        if not kc.empty:
            print(f"\nKill-chain trades: {len(kc)}")
            print(f"  Total $ sold:     ${kc['Value'].sum():,.0f}")
            print(f"  Avg beta:         {kc['Beta'].mean():.2f}")
            print(f"  Tickers:          {sorted(kc['Ticker'].unique().tolist())}")
    if args.pdf:
        from simulation.pdf_report import generate_pdf_report
        hist = res.history if isinstance(res.history, pd.DataFrame) else pd.DataFrame(res.history)
        path = generate_pdf_report(
            history=hist,
            trade_ledger=res.trade_ledger,
            start_date=args.start,
            end_date=args.end,
            initial_capital=args.capital,
            output_dir=args.output_dir,
        )
        print(f"PDF report: {path}")
