"""
ARMS Engine: Thesis Retirement Protocol (TRP)

This module provides the rigorous exit logic for ARMS. It triggers an 
orderly exit of a position when its structural thesis is compromised, 
ensuring that we do not "hope" for a recovery when the data has shifted.

Tier 0 Promotion (Addendum 3, Section 4):
  Once CCM has 90 days of data, TRP can execute automatically when:
    - TIS BROKEN 30+ days
    - CDF at 0.60× or below
    - No PM reaffirmation in 45 days
  Before CCM has 90 days: Tier 1 with 24h veto window.

"Silence is trust in the architecture."

Reference: ARMS FSD v1.1, Section 5.3
Reference: ARMS Intelligence Architecture Addendum 3, Section 4
"""

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional

# --- Internal Imports ---
from engine.tdc import ThesisReviewResult
from engine.cdf import CDFStatus
from reporting.audit_log import SessionLogEntry, append_to_log
from execution.order_request import OrderRequest
from execution.confirmation_queue import QueuedAction

# --- Data Structures ---

@dataclass
class TRPStatus:
    """Represents the retirement state of a specific position."""
    ticker: str
    is_retirement_due: bool
    trigger_reason: str
    tis_broken_days: int
    cdf_multiplier: float
    tier: int = 1
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- Configuration ---

# Days a TIS score must be 'BROKEN' to trigger retirement
BROKEN_THRESHOLD_DAYS = 30

# CDF multiplier threshold for retirement eligibility
CDF_EXIT_MULTIPLIER = 0.60

# CCM state file (same path used by conviction_calibration.py)
CCM_STATE_PATH = 'achelion_arms/state/calibration_state.json'
CCM_MATURITY_DAYS = 90


def _ccm_has_90_days() -> bool:
    """Check if CCM has been running for at least 90 days."""
    if not os.path.exists(CCM_STATE_PATH):
        return False
    try:
        with open(CCM_STATE_PATH, 'r') as f:
            state = json.load(f)
        first_cal = state.get('first_calibrated_at')
        if not first_cal:
            return False
        first_dt = datetime.datetime.fromisoformat(first_cal)
        now = datetime.datetime.now(datetime.timezone.utc)
        return (now - first_dt).days >= CCM_MATURITY_DAYS
    except (json.JSONDecodeError, ValueError, KeyError):
        return False


# --- TRP Logic ---

def run_trp_check(
    ticker: str,
    tdc_result: ThesisReviewResult,
    cdf_status: CDFStatus,
    days_tis_broken: int,
    pm_reaffirmation_days_ago: Optional[int] = None
) -> TRPStatus:
    """
    Evaluates the 3 conditions for the Thesis Retirement Protocol.
    Trigger: TIS BROKEN 30d + CDF at 0.60x + no PM reaffirmation 45d.

    Tier determination:
       - CCM has 90+ days of data → Tier 0 (auto-execute, no veto)
       - CCM <90 days → Tier 1 (24h veto window)
    """
    is_retirement_due = False
    reason = "THESIS_INTACT"
    
    # 1. Thesis Condition (BROKEN for 30+ days)
    thesis_compromised = (tdc_result.tis_label == 'BROKEN' and days_tis_broken >= BROKEN_THRESHOLD_DAYS)
    
    # 2. Performance Condition (CDF at 0.60x)
    performance_exhausted = (cdf_status.current_multiplier <= CDF_EXIT_MULTIPLIER)
    
    # 3. PM Reaffirmation (No reaffirmation in last 45 days)
    no_recent_reaffirmation = (pm_reaffirmation_days_ago is None or pm_reaffirmation_days_ago > 45)
    
    if thesis_compromised and performance_exhausted and no_recent_reaffirmation:
        is_retirement_due = True
        reason = "TRIPLE_SIGNAL_RETIREMENT"
    elif thesis_compromised and performance_exhausted:
        is_retirement_due = True
        reason = "DUAL_SIGNAL_RETIREMENT"

    # Determine tier based on CCM maturity
    ccm_mature = _ccm_has_90_days()
    tier = 0 if ccm_mature else 1

    status = TRPStatus(
        ticker=ticker,
        is_retirement_due=is_retirement_due,
        trigger_reason=reason,
        tis_broken_days=days_tis_broken,
        cdf_multiplier=cdf_status.current_multiplier,
        tier=tier
    )
    
    # Trigger Action if Due
    if is_retirement_due:
        if tier == 0:
            # Tier 0: Auto-execute — no veto window
            order = OrderRequest(
                ticker=ticker,
                action='SELL',
                quantity=0.0,  # 0.0 = full-exit instruction
                order_type='VWAP',
                execution_window_min=60,
                slippage_budget_bps=20.0,
                priority=2,
                triggering_module='TRP',
                triggering_signal=f"Thesis Retirement Protocol [Tier 0]: {reason}. CCM 90-day validated.",
                tier=0,
                confirmation_required=False,
                veto_window_hours=0.0
            )

            append_to_log(SessionLogEntry(
                timestamp=status.timestamp,
                action_type='TRP_RETIREMENT_TIER0',
                triggering_module='TRP',
                triggering_signal=f"Tier 0 auto-retirement for {ticker}. Reason: {reason}.",
                ticker=ticker
            ))

        else:
            # Tier 1: Requires PM confirmation with 24h veto
            order = OrderRequest(
                ticker=ticker,
                action='SELL',
                quantity=0.0,
                order_type='VWAP',
                execution_window_min=60,
                slippage_budget_bps=20.0,
                priority=2,
                triggering_module='TRP',
                triggering_signal=f"Thesis Retirement Protocol [Tier 1]: {reason}.",
                tier=1,
                confirmation_required=True,
                veto_window_hours=24.0
            )

            append_to_log(SessionLogEntry(
                timestamp=status.timestamp,
                action_type='TRP_RETIREMENT',
                triggering_module='TRP',
                triggering_signal=f"Retirement triggered for {ticker}. Reason: {reason}.",
                ticker=ticker
            ))
        
            queued_action = QueuedAction(
                action_id=f"trp_{ticker}_{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M')}",
                item=order,
                triggering_module='TRP',
                rationale=order.triggering_signal,
                queued_at=datetime.datetime.now(datetime.timezone.utc),
                veto_window_hours=24.0
            )
    
    return status

if __name__ == '__main__':
    print("ARMS TRP Module Active (Simulation Mode)")
    
    # Simulate a retirement scenario
    mock_tdc = ThesisReviewResult(
        position="TSLA", tis_score=3.2, tis_label="BROKEN", 
        gates_affected=[1, 2], trigger_alert=None # type: ignore
    )
    mock_cdf = CDFStatus(
        ticker="TSLA", days_underperforming=100, underperformance_pp=15.0, 
        current_multiplier=0.60, next_decay_at=None, is_orderly_exit_due=True
    )
    
    res = run_trp_check(
        ticker="TSLA",
        tdc_result=mock_tdc,
        cdf_status=mock_cdf,
        days_tis_broken=35,
        pm_reaffirmation_days_ago=50
    )
    
    print(f"TRP Status: {res}")
