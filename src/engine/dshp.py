"""
ARMS Module: Defensive Sleeve Harvest Protocol (DSHP)

Tier 0 eligible — promotes to autonomous execution after 90 days
of validated operation with zero PM vetoes.

Monitors defensive positions (SGOL, DBMF) for significant appreciation
and trims them back to target weights, crystallizing gains.

When Tier 0 validated:
  - No veto window. No confirmation. System harvests and logs.
  - PM sees result in module status panel, not decision queue.

When still Tier 1 (pre-validation):
  - Queued with veto window for PM review.

"Silence is trust in the architecture."

Reference: ARMS Module Specification — PTRH Automation + DSHP, Section 2
Reference: Addendum 3, Section 4 — Tier 0 Promotion
"""

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional

# --- Internal Imports ---
from config.dshp_config import (
    SGOL_TARGET_WEIGHT, SGOL_HARVEST_THRESHOLD, 
    DBMF_TARGET_WEIGHT, DBMF_HARVEST_THRESHOLD, DBMF_DRIFT_THRESHOLD,
    DSHP_VETO_WINDOW_HOURS
)
from execution.order_request import OrderRequest
from execution.confirmation_queue import QueuedAction
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class DefensivePosition:
    """Represents a single position in the defensive sleeve."""
    ticker: str
    entry_value: float     # Cost basis
    current_value: float
    current_weight: float  # current_value / total_nav


@dataclass(frozen=True)
class DSHPHarvestAction:
    instrument: str
    trigger: Literal['APPRECIATION', 'WEIGHT_DRIFT']
    trim_amount_usd: float
    rationale: str

# --- Tier 0 Validation ---

DSHP_VALIDATION_STATE_PATH = 'achelion_arms/state/dshp_tier0_validation.json'
DSHP_VALIDATION_DAYS = 90  # 90 days of zero vetoes required


def _load_validation_state() -> dict:
    """Load DSHP Tier 0 validation state."""
    if os.path.exists(DSHP_VALIDATION_STATE_PATH):
        with open(DSHP_VALIDATION_STATE_PATH, 'r') as f:
            return json.load(f)
    return {'validation_start': None, 'veto_count': 0, 'promoted': False}


def _save_validation_state(state: dict):
    """Save DSHP Tier 0 validation state."""
    os.makedirs(os.path.dirname(DSHP_VALIDATION_STATE_PATH), exist_ok=True)
    with open(DSHP_VALIDATION_STATE_PATH, 'w') as f:
        json.dump(state, f)


def is_tier0_validated() -> bool:
    """
    Check if DSHP has been promoted to Tier 0.
    Requires 90 consecutive days of operation with zero PM vetoes.
    """
    state = _load_validation_state()
    if state.get('promoted', False):
        return True

    if state.get('validation_start') is None:
        # Start validation window now
        state['validation_start'] = datetime.date.today().isoformat()
        state['veto_count'] = 0
        _save_validation_state(state)
        return False

    start = datetime.date.fromisoformat(state['validation_start'])
    days_elapsed = (datetime.date.today() - start).days

    if state.get('veto_count', 0) > 0:
        # Reset: veto occurred during validation window
        state['validation_start'] = datetime.date.today().isoformat()
        state['veto_count'] = 0
        _save_validation_state(state)
        return False

    if days_elapsed >= DSHP_VALIDATION_DAYS:
        # Promote to Tier 0
        state['promoted'] = True
        state['promoted_at'] = datetime.date.today().isoformat()
        _save_validation_state(state)
        append_to_log(SessionLogEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            action_type='DSHP_TIER0_PROMOTED',
            triggering_module='DSHP',
            triggering_signal=f'DSHP promoted to Tier 0 after {days_elapsed} days with zero vetoes.',
        ))
        return True

    return False


def record_dshp_veto():
    """Called when PM vetoes a DSHP action. Resets Tier 0 validation."""
    state = _load_validation_state()
    state['veto_count'] = state.get('veto_count', 0) + 1
    state['promoted'] = False
    state['validation_start'] = datetime.date.today().isoformat()
    _save_validation_state(state)


def check_dshp_triggers(
    sleeve_positions: Dict[str, DefensivePosition],
    total_nav: float,
) -> List[DSHPHarvestAction]:
    """Deterministic DSHP trigger evaluation used by tests and orchestration."""
    actions: List[DSHPHarvestAction] = []

    if 'SGOL' in sleeve_positions:
        sgol = sleeve_positions['SGOL']
        appreciation = (sgol.current_value - sgol.entry_value) / sgol.entry_value if sgol.entry_value > 0 else 0.0
        if appreciation > SGOL_HARVEST_THRESHOLD:
            trim_amount = sgol.current_value - (total_nav * SGOL_TARGET_WEIGHT)
            if trim_amount > 0:
                actions.append(DSHPHarvestAction(
                    instrument='SGOL',
                    trigger='APPRECIATION',
                    trim_amount_usd=trim_amount,
                    rationale=f"Appreciation Harvest: {appreciation:.2%}",
                ))

    if 'DBMF' in sleeve_positions:
        dbmf = sleeve_positions['DBMF']
        appreciation = (dbmf.current_value - dbmf.entry_value) / dbmf.entry_value if dbmf.entry_value > 0 else 0.0
        weight_drift = dbmf.current_weight - DBMF_TARGET_WEIGHT
        trigger = None
        rationale = ''

        if appreciation > DBMF_HARVEST_THRESHOLD:
            trigger = 'APPRECIATION'
            rationale = f"Appreciation Harvest: {appreciation:.2%}"
        elif weight_drift > DBMF_DRIFT_THRESHOLD:
            trigger = 'WEIGHT_DRIFT'
            rationale = f"Weight Drift Harvest: {dbmf.current_weight:.2%}"

        if trigger is not None:
            trim_amount = dbmf.current_value - (total_nav * DBMF_TARGET_WEIGHT)
            if trim_amount > 0:
                actions.append(DSHPHarvestAction(
                    instrument='DBMF',
                    trigger=trigger,
                    trim_amount_usd=trim_amount,
                    rationale=rationale,
                ))

    return actions


# --- DSHP Core Logic ---

def _build_harvest_order(ticker: str, trim_amount: float, signal: str,
                         tier0: bool, timestamp_str: str) -> tuple:
    """Build order and optionally queued action for a harvest trim."""
    tier = 0 if tier0 else 1
    confirm = not tier0
    veto_hours = 0.0 if tier0 else DSHP_VETO_WINDOW_HOURS

    order = OrderRequest(
        ticker=ticker,
        action='SELL',
        quantity=trim_amount,
        quantity_kind='NOTIONAL_USD',
        order_type='MARKET',
        execution_window_min=30,
        slippage_budget_bps=8.0,
        priority=3,
        triggering_module='DSHP',
        triggering_signal=signal,
        tier=tier,
        confirmation_required=confirm,
        veto_window_hours=veto_hours,
    )

    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='DSHP_TRIGGER',
        triggering_module='DSHP',
        triggering_signal=signal,
        ticker=ticker,
    ))

    if tier0:
        # Tier 0: return order directly (no veto queue)
        return order, None
    else:
        # Tier 1: wrap in QueuedAction for PM review
        action = QueuedAction(
            action_id=f"dshp_{ticker.lower()}_{timestamp_str}",
            item=order,
            triggering_module='DSHP',
            rationale=signal,
            queued_at=datetime.datetime.now(datetime.timezone.utc),
            veto_window_hours=DSHP_VETO_WINDOW_HOURS,
        )
        return order, action


def run_dshp_check(sleeve_positions: Dict[str, DefensivePosition], 
                   total_nav: float) -> List[QueuedAction]:
    """
    Checks defensive sleeve positions against harvest thresholds.
    
    When Tier 0 validated: returns empty list (orders execute directly).
    When Tier 1: returns QueuedAction list for PM review.
    
    Args:
        sleeve_positions: Dict of DefensivePosition objects, keyed by ticker.
        total_nav: Total portfolio Net Asset Value.
        
    Returns:
        List of QueuedAction objects (empty if Tier 0).
    """
    queued_actions = []
    direct_orders = []
    timestamp_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d_%H%M')
    tier0 = is_tier0_validated()

    if tier0:
        print("[DSHP] Operating in Tier 0 — autonomous harvest execution")

    for harvest in check_dshp_triggers(sleeve_positions, total_nav):
        order, action = _build_harvest_order(
            harvest.instrument,
            harvest.trim_amount_usd,
            harvest.rationale,
            tier0,
            timestamp_str,
        )
        if action:
            queued_actions.append(action)
        else:
            direct_orders.append(order)

    return queued_actions

if __name__ == '__main__':
    print("ARMS DSHP Module Active (Simulation Mode)")
    
    # Simulate a drift in DBMF
    mock_nav = 50_000_000
    mock_positions = {
        'DBMF': DefensivePosition(
            ticker='DBMF',
            current_value=mock_nav * 0.07, # 7.0% weight (2.0pp drift)
            entry_value=mock_nav * 0.05,
            current_weight=0.07
        )
    }
    
    actions = run_dshp_check(mock_positions, mock_nav)
    for a in actions:
        print(f"Queued: {a.action_id} - {a.rationale}")
