"""
ARMS Execution: Escalation Engine

Implements Escalation Rule v2: Cumulative intraday drawdown auto-suppress.
Tracks cumulative intraday loss across all executed orders. When cumulative
loss exceeds -2.5% of NAV in a single session, suppresses all Tier 0
autonomous execution for the remainder of the session. PM can still
manually submit Tier 2 orders.

State resets at the start of each trading session (0830 CT).

Reference: THB v4.0, Section 10
"""
import os
import json
import datetime
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class EscalationStatus:
    cumulative_intraday_loss_pct: float
    is_suppressed: bool
    orders_blocked_count: int
    detail: str

_STATE_PATH = os.path.join('achelion_arms', 'state', 'escalation_state.json')

# Thresholds
SUPPRESS_THRESHOLD = -0.025  # -2.5% cumulative intraday loss
WARNING_THRESHOLD = -0.015   # -1.5% early warning

def _load_state() -> dict:
    if os.path.exists(_STATE_PATH):
        try:
            with open(_STATE_PATH, 'r') as f:
                state = json.load(f)
                # Reset if from a different trading day
                saved_date = state.get('date', '')
                if saved_date != datetime.date.today().isoformat():
                    return {'date': datetime.date.today().isoformat(), 'cumulative_loss': 0.0, 'orders_blocked': 0, 'suppressed_at': None}
                return state
        except Exception:
            pass
    return {'date': datetime.date.today().isoformat(), 'cumulative_loss': 0.0, 'orders_blocked': 0, 'suppressed_at': None}

def _save_state(state: dict):
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, 'w') as f:
        json.dump(state, f)

def record_execution_pnl(pnl_pct: float):
    """Record P&L impact from an executed order (as decimal, e.g. -0.003 for -0.3%)."""
    state = _load_state()
    state['cumulative_loss'] = state.get('cumulative_loss', 0.0) + pnl_pct
    if state['cumulative_loss'] <= SUPPRESS_THRESHOLD and not state.get('suppressed_at'):
        state['suppressed_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _save_state(state)

def run_escalation_engine(intraday_loss: Optional[float] = None) -> EscalationStatus:
    """
    Evaluate cumulative intraday loss and determine suppression state.
    
    Args:
        intraday_loss: If provided, overrides the tracked cumulative loss (for backward compat).
                       Otherwise uses the internally tracked state.
    
    Returns:
        EscalationStatus with suppression decision and details.
    """
    state = _load_state()
    
    if intraday_loss is not None:
        cum_loss = intraday_loss
        state['cumulative_loss'] = intraday_loss
        _save_state(state)
    else:
        cum_loss = state.get('cumulative_loss', 0.0)
    
    orders_blocked = state.get('orders_blocked', 0)
    
    if cum_loss <= SUPPRESS_THRESHOLD:
        is_suppressed = True
        detail = (f"SUPPRESSED: Cumulative intraday loss {cum_loss:.2%} breaches "
                  f"{SUPPRESS_THRESHOLD:.1%} threshold. Tier 0 autonomous execution halted. "
                  f"{orders_blocked} orders blocked this session.")
    elif cum_loss <= WARNING_THRESHOLD:
        is_suppressed = False
        detail = (f"WARNING: Cumulative intraday loss {cum_loss:.2%} approaching "
                  f"suppression threshold ({SUPPRESS_THRESHOLD:.1%}).")
    else:
        is_suppressed = False
        detail = f"NORMAL: Cumulative intraday P&L {cum_loss:+.2%}."
    
    return EscalationStatus(
        cumulative_intraday_loss_pct=cum_loss,
        is_suppressed=is_suppressed,
        orders_blocked_count=orders_blocked,
        detail=detail,
    )

def block_order():
    """Increment the blocked-order counter when an order is suppressed."""
    state = _load_state()
    state['orders_blocked'] = state.get('orders_blocked', 0) + 1
    _save_state(state)
