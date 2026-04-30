from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional, Set

from execution.queue_reasoning import QueueReasoningSignal


QueueState = Literal[
    'LOCKED',
    'WATCH',
    'TRIGGERED',
    'EVAL_ONLY',
    'HOLD_CURRENT_WEIGHT',
    'REMOVED',
    'MONITOR_LIST',
    'UNLOCKED'
]

QueueReason = Literal[
    'ASYMMETRY_RETAINED',
    'CONSENSUS_PRICED',
    'SENTINEL_UNCONFIRMED',
    'TIME_HORIZON_MISMATCH',
    'VALUATION_COMPRESSION_REQUIRED',
    'REGIME_NOT_CLEARED',
    'RISK_ON_EVAL_ONLY',
    'THESIS_MONITOR_ONLY',
    'PM_OVERRIDE',
    'UNKNOWN'
]

QueueLane = Literal['NEUTRAL', 'RISK_ON', 'MONITOR']


# ─── State Machine: Valid Transitions ──────────────────────────────────────────
# Key = current state, Value = set of valid next states.
# 'ABSENT' is a synthetic state for tickers newly entering the queue.
VALID_TRANSITIONS: Dict[str, Set[str]] = {
    'ABSENT':              {'LOCKED', 'WATCH', 'EVAL_ONLY', 'MONITOR_LIST', 'REMOVED'},
    'LOCKED':              {'WATCH', 'TRIGGERED', 'EVAL_ONLY', 'REMOVED', 'MONITOR_LIST', 'LOCKED'},
    'WATCH':               {'TRIGGERED', 'LOCKED', 'EVAL_ONLY', 'REMOVED', 'MONITOR_LIST', 'WATCH'},
    'TRIGGERED':           {'LOCKED', 'WATCH', 'HOLD_CURRENT_WEIGHT', 'REMOVED', 'TRIGGERED'},
    'EVAL_ONLY':           {'WATCH', 'LOCKED', 'TRIGGERED', 'REMOVED', 'MONITOR_LIST', 'EVAL_ONLY'},
    'HOLD_CURRENT_WEIGHT': {'EVAL_ONLY', 'REMOVED', 'MONITOR_LIST', 'WATCH', 'HOLD_CURRENT_WEIGHT'},
    'REMOVED':             {'LOCKED', 'WATCH', 'REMOVED'},  # Re-entry requires going back to LOCKED/WATCH
    'MONITOR_LIST':        {'LOCKED', 'WATCH', 'EVAL_ONLY', 'REMOVED', 'MONITOR_LIST'},
    'UNLOCKED':            {'LOCKED', 'WATCH', 'TRIGGERED', 'UNLOCKED'},
}

logger = logging.getLogger('arms.queue_governance')


def validate_transition(ticker: str, old_state: str, new_state: str) -> bool:
    """
    Returns True if the transition is valid per the state machine.
    Logs a warning for invalid transitions but does NOT block them —
    the system is in hardening phase and blocking could halt the cycle.
    """
    valid_set = VALID_TRANSITIONS.get(old_state, set())
    if new_state not in valid_set:
        logger.warning(
            f"[QueueGovernance] INVALID TRANSITION for {ticker}: "
            f"{old_state} -> {new_state}. Valid targets from {old_state}: {sorted(valid_set)}"
        )
        print(
            f"[QueueGovernance] WARNING: Invalid state transition for {ticker}: "
            f"{old_state} -> {new_state}"
        )
        return False
    return True


@dataclass
class QueueGovernanceEntry:
    ticker: str
    lane: QueueLane
    state: QueueState
    reason: QueueReason
    target_weight_pct: Optional[float]
    trigger_rule: str
    execution_instruction: str
    notes: str = ''


@dataclass
class QueueGovernanceState:
    headline_status: str
    neutral_queue: List[QueueGovernanceEntry] = field(default_factory=list)
    risk_on_queue: List[QueueGovernanceEntry] = field(default_factory=list)
    removed_items: List[QueueGovernanceEntry] = field(default_factory=list)
    monitor_list: List[QueueGovernanceEntry] = field(default_factory=list)


def _fmt_target(target_weight_pct: Optional[float]) -> str:
    if target_weight_pct is None:
        return 'N/A'
    return f"{target_weight_pct * 100:.1f}%"


def build_queue_governance_state(
    strategic_items,
    current_score: float,
    current_regime: str,
    ares_fully_cleared: bool,
    thesis_state_by_ticker: Optional[Dict[str, Dict[str, str]]] = None,
    reasoning_signals: Optional[Dict[str, QueueReasoningSignal]] = None,
) -> QueueGovernanceState:
    """
    Transitional typed queue state.

    This does NOT yet implement full canonical asymmetry / SENTINEL / consensus reasoning.
    It is an honest typed bridge that replaces thin ad hoc queue scaffolding in reporting.
    """
    thesis_state_by_ticker = thesis_state_by_ticker or {}
    reasoning_signals = reasoning_signals or {}
    neutral_queue: List[QueueGovernanceEntry] = []
    risk_on_queue: List[QueueGovernanceEntry] = []
    removed_items: List[QueueGovernanceEntry] = []
    monitor_list: List[QueueGovernanceEntry] = []

    for item in strategic_items:
        lane: QueueLane = 'RISK_ON' if item.trigger_threshold <= 0.45 else 'NEUTRAL'
        thesis = thesis_state_by_ticker.get(item.ticker.upper(), {})
        thesis_status = thesis.get('status', 'MISSING')
        gate3_score = thesis.get('gate3_score')
        source_category = thesis.get('source_category', 'None')
        reasoning = reasoning_signals.get(item.ticker.upper())

        if current_score <= item.trigger_threshold and ares_fully_cleared:
            state = 'TRIGGERED'
            reason = 'ASYMMETRY_RETAINED'
        elif current_score <= (item.trigger_threshold + 0.10):
            state = 'WATCH'
            reason = 'REGIME_NOT_CLEARED'
        else:
            state = 'LOCKED'
            reason = 'REGIME_NOT_CLEARED'

        if lane == 'RISK_ON' and state != 'TRIGGERED':
            state = 'EVAL_ONLY'
            reason = 'RISK_ON_EVAL_ONLY'

        if reasoning:
            if reasoning.action == 'REMOVE':
                state = 'REMOVED'
            elif reasoning.action == 'MONITOR':
                state = 'MONITOR_LIST'
            elif reasoning.action == 'HOLD':
                state = 'HOLD_CURRENT_WEIGHT'
            elif reasoning.action == 'EVAL_ONLY':
                state = 'EVAL_ONLY'
            reason = reasoning.reason  # type: ignore[assignment]

        notes = f"Queue item #{item.id_num}; thesis_status={thesis_status}; gate3={gate3_score}; source={source_category}. {reasoning.note if reasoning else ''}"
        entry = QueueGovernanceEntry(
            ticker=item.ticker,
            lane=lane,
            state=state,
            reason=reason,
            target_weight_pct=item.target_weight_pct,
            trigger_rule=f"score <= {item.trigger_threshold:.2f}",
            execution_instruction=item.execution_instruction,
            notes=notes
        )

        if state == 'REMOVED':
            removed_items.append(entry)
        elif state == 'MONITOR_LIST':
            monitor_list.append(entry)
        elif state == 'HOLD_CURRENT_WEIGHT':
            monitor_list.append(entry)
        elif lane == 'NEUTRAL':
            neutral_queue.append(entry)
        else:
            risk_on_queue.append(entry)

    headline_status = 'UNLOCKED' if ares_fully_cleared else 'LOCKED'
    if not ares_fully_cleared and any(e.state == 'WATCH' for e in neutral_queue + risk_on_queue):
        headline_status = 'WATCH'

    return QueueGovernanceState(
        headline_status=headline_status,
        neutral_queue=neutral_queue,
        risk_on_queue=risk_on_queue,
        removed_items=removed_items,
        monitor_list=monitor_list,
    )
