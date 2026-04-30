"""
ARMS Engine: Factor Exposure Monitor (FEM)

Advisory sub-module that detects hidden concentration risk.
Flags the PM when correlated exposure becomes too high.

Five factors per THB v4.0, Section 2:
  1. AI Capex Cycle
  2. Taiwan Manufacturing
  3. BTC Beta (digital asset correlation)
  4. Dollar Sensitivity (DXY exposure)
  5. Rate Sensitivity (interest rate exposure)

Thresholds:
  WATCH  → any single factor >65%
  ALERT  → any single factor >80% OR cross-sleeve correlation >0.70

Tier 0 Paired Trim (Addendum 3, Section 4):
  When FEM reaches ALERT (>80%) for >24 hours AND a specific trim is
  identified AND trim does not require a full SENTINEL re-run, it
  executes automatically. Paired trim is deterministic.

Reference: THB v4.0, Section 2
Reference: Addendum 3, Section 4 — Tier 0 Promotion
"""
import datetime
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from reporting.audit_log import SessionLogEntry, append_to_log
from execution.order_request import OrderRequest

# ------------------------------------------------------------------ #
#  Factor → Ticker Mappings (from THB v4.0)
# ------------------------------------------------------------------ #

# Factor 1: AI Capex Cycle — companies whose revenue depends on AI infrastructure spend
AI_CAPEX_TICKERS = {'NVDA', 'AMD', 'ALAB', 'MU', 'MRVL', 'AVGO', 'ANET', 'ARM'}

# Factor 2: Taiwan Manufacturing — companies with critical TSMC/Taiwan supply dependency
TAIWAN_MFG_TICKERS = {'NVDA', 'AMD', 'AVGO', 'MU', 'MRVL', 'ARM', 'ALAB'}

# Factor 3: BTC Beta — digital asset positions correlated with Bitcoin
BTC_BETA_TICKERS = {'IBIT', 'ETHB', 'BSOL', 'PLTR', 'TSLA'}

# Factor 4: Dollar Sensitivity — positions materially affected by DXY moves
DOLLAR_SENSITIVITY_TICKERS = {'NVDA', 'AMD', 'AVGO', 'ANET', 'MU'}

# Factor 5: Rate Sensitivity — positions sensitive to interest rate changes
RATE_SENSITIVITY_TICKERS = {'VRT', 'ETN', 'ANET', 'SGOV'}

CROSS_SLEEVE_CORRELATION_THRESHOLD = 0.70

# Tier 0 paired trim config
FEM_ALERT_DURATION_HOURS = 24  # Must sustain ALERT for 24h before auto-trim
FEM_ALERT_STATE_PATH = 'achelion_arms/state/fem_alert_state.json'
FEM_MAX_TRIM_WEIGHT = 0.02  # Max 2pp trim per cycle to avoid over-correction


@dataclass
class FactorExposureSignal:
    factors: Dict[str, float]
    status: str
    highest_exposure_factor: str
    highest_exposure_pct: float


@dataclass
class FEMPairedTrim:
    """Tier 0 auto-trim recommendation when ALERT persists >24h."""
    ticker: str
    current_weight: float
    trim_weight: float  # How much to reduce
    factor: str
    signal: str


def _load_alert_state() -> dict:
    """Load persistent ALERT state for duration tracking."""
    if os.path.exists(FEM_ALERT_STATE_PATH):
        with open(FEM_ALERT_STATE_PATH, 'r') as f:
            return json.load(f)
    return {}


def _save_alert_state(state: dict):
    """Save ALERT state for duration tracking."""
    os.makedirs(os.path.dirname(FEM_ALERT_STATE_PATH), exist_ok=True)
    with open(FEM_ALERT_STATE_PATH, 'w') as f:
        json.dump(state, f)


def run_fem_check(portfolio_weights: Dict[str, float]) -> FactorExposureSignal:
    """
    Runs the 5-factor exposure analysis on the current portfolio weights.

    Args:
        portfolio_weights: Dict mapping ticker → portfolio weight (0.0-1.0).

    Returns:
        FactorExposureSignal with all factor exposures and advisory status.
    """
    factor_map = {
        'AI_CAPEX_CYCLE': AI_CAPEX_TICKERS,
        'TAIWAN_MANUFACTURING': TAIWAN_MFG_TICKERS,
        'BTC_BETA': BTC_BETA_TICKERS,
        'DOLLAR_SENSITIVITY': DOLLAR_SENSITIVITY_TICKERS,
        'RATE_SENSITIVITY': RATE_SENSITIVITY_TICKERS,
    }

    factors: Dict[str, float] = {}
    for factor_name, ticker_set in factor_map.items():
        exposure = sum(w for t, w in portfolio_weights.items() if t in ticker_set)
        factors[factor_name] = exposure

    # Determine highest factor
    highest_factor = max(factors, key=factors.get)  # type: ignore[arg-type]
    highest_pct = factors[highest_factor]

    # Cross-sleeve correlation proxy: overlap between AI Capex and Taiwan Mfg
    ai_tickers_held = {t for t in portfolio_weights if t in AI_CAPEX_TICKERS and portfolio_weights[t] > 0}
    taiwan_tickers_held = {t for t in portfolio_weights if t in TAIWAN_MFG_TICKERS and portfolio_weights[t] > 0}
    overlap = ai_tickers_held & taiwan_tickers_held
    total_unique = ai_tickers_held | taiwan_tickers_held
    cross_sleeve_corr = len(overlap) / len(total_unique) if total_unique else 0.0

    # Status determination per THB v4.0 thresholds
    status = "NORMAL"
    if highest_pct > 0.80 or cross_sleeve_corr > CROSS_SLEEVE_CORRELATION_THRESHOLD:
        status = "ALERT"
    elif highest_pct > 0.65:
        status = "WATCH"

    # --- Track ALERT duration for Tier 0 paired trim ---
    alert_state = _load_alert_state()
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    if status == "ALERT":
        if 'alert_start' not in alert_state:
            alert_state['alert_start'] = now_iso
            alert_state['alert_factor'] = highest_factor
        alert_state['last_check'] = now_iso
    else:
        # Clear alert tracking when status drops below ALERT
        alert_state = {}

    _save_alert_state(alert_state)

    return FactorExposureSignal(
        factors=factors,
        status=status,
        highest_exposure_factor=highest_factor,
        highest_exposure_pct=highest_pct
    )


def generate_paired_trims(
    portfolio_weights: Dict[str, float],
    fem_signal: FactorExposureSignal,
) -> List[OrderRequest]:
    """
    Tier 0: Generate auto-trim orders when ALERT has persisted >24 hours.
    
    Identifies the smallest trim within the alerting factor that brings
    exposure below 80%. Trim is deterministic — no SENTINEL re-run needed.
    
    Returns:
        List of Tier 0 OrderRequest objects for auto-execution.
        Empty list if conditions not met.
    """
    if fem_signal.status != "ALERT":
        return []

    alert_state = _load_alert_state()
    if 'alert_start' not in alert_state:
        return []

    # Check duration
    try:
        alert_start = datetime.datetime.fromisoformat(alert_state['alert_start'])
    except (ValueError, KeyError):
        return []

    now = datetime.datetime.now(datetime.timezone.utc)
    hours_in_alert = (now - alert_start).total_seconds() / 3600

    if hours_in_alert < FEM_ALERT_DURATION_HOURS:
        return []  # Not sustained long enough

    # Identify the alerting factor's tickers
    factor_ticker_map = {
        'AI_CAPEX_CYCLE': AI_CAPEX_TICKERS,
        'TAIWAN_MANUFACTURING': TAIWAN_MFG_TICKERS,
        'BTC_BETA': BTC_BETA_TICKERS,
        'DOLLAR_SENSITIVITY': DOLLAR_SENSITIVITY_TICKERS,
        'RATE_SENSITIVITY': RATE_SENSITIVITY_TICKERS,
    }

    alert_factor = fem_signal.highest_exposure_factor
    factor_tickers = factor_ticker_map.get(alert_factor, set())

    # Find the smallest-weight position in the alerting factor to trim
    # (minimize portfolio disruption)
    held_in_factor = {
        t: w for t, w in portfolio_weights.items()
        if t in factor_tickers and w > 0
    }

    if not held_in_factor:
        return []

    # Sort by weight ascending — trim the smallest position first
    sorted_positions = sorted(held_in_factor.items(), key=lambda x: x[1])

    orders = []
    excess = fem_signal.highest_exposure_pct - 0.78  # Target 78% (buffer below 80%)

    for ticker, weight in sorted_positions:
        if excess <= 0:
            break

        trim_amount = min(weight, excess, FEM_MAX_TRIM_WEIGHT)
        if trim_amount < 0.001:
            continue

        signal = (f"FEM Paired Trim: {alert_factor} at {fem_signal.highest_exposure_pct:.1%} "
                  f"for {hours_in_alert:.0f}h (>24h threshold). "
                  f"Trimming {ticker} by {trim_amount:.2%}.")

        order = OrderRequest(
            ticker=ticker,
            action='SELL',
            quantity=trim_amount,
            quantity_kind='WEIGHT_PCT',
            order_type='MARKET',
            execution_window_min=30,
            slippage_budget_bps=10.0,
            priority=2,
            triggering_module='FEM',
            triggering_signal=signal,
            tier=0,
            confirmation_required=False,
            veto_window_hours=0.0,
        )
        orders.append(order)

        append_to_log(SessionLogEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            action_type='FEM_PAIRED_TRIM',
            triggering_module='FEM',
            triggering_signal=signal,
            ticker=ticker,
        ))

        excess -= trim_amount

    if orders:
        # Reset alert state after trim execution
        _save_alert_state({})

    return orders
