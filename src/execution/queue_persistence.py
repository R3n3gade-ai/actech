from __future__ import annotations

import datetime
import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, List

from engine.bridge_paths import bridge_path
from execution.queue_state import QueueGovernanceEntry, QueueGovernanceState, validate_transition
from reporting.audit_log import SessionLogEntry, append_to_log


QUEUE_STATE_PATH = bridge_path('ARMS_QUEUE_STATE_JSON', 'queue_governance_state.json')
QUEUE_TRANSITION_LOG_PATH = bridge_path('ARMS_QUEUE_TRANSITION_LOG', 'queue_transition_log.jsonl')


@dataclass
class QueueTransition:
    ticker: str
    old_state: str
    new_state: str
    old_reason: str
    new_reason: str
    changed_at: str
    note: str
    valid_transition: bool = True


@dataclass
class PersistedQueueSnapshot:
    updated_at: str
    headline_status: str
    entries: Dict[str, dict]


def _flatten_state(state: QueueGovernanceState) -> Dict[str, QueueGovernanceEntry]:
    out: Dict[str, QueueGovernanceEntry] = {}
    for entry in state.neutral_queue + state.risk_on_queue + state.removed_items + state.monitor_list:
        out[entry.ticker.upper()] = entry
    return out


def load_queue_snapshot() -> PersistedQueueSnapshot:
    if not os.path.exists(QUEUE_STATE_PATH):
        return PersistedQueueSnapshot(updated_at='', headline_status='UNKNOWN', entries={})
    try:
        with open(QUEUE_STATE_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        return PersistedQueueSnapshot(
            updated_at=raw.get('updated_at', ''),
            headline_status=raw.get('headline_status', 'UNKNOWN'),
            entries=raw.get('entries', {}),
        )
    except Exception as e:
        print(f"[QueuePersistence] Failed to load queue snapshot: {e}")
        return PersistedQueueSnapshot(updated_at='', headline_status='UNKNOWN', entries={})


def diff_queue_state(previous: PersistedQueueSnapshot, current: QueueGovernanceState) -> List[QueueTransition]:
    transitions: List[QueueTransition] = []
    previous_entries = previous.entries or {}
    current_entries = _flatten_state(current)
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    all_tickers = sorted(set(previous_entries.keys()) | set(current_entries.keys()))
    for ticker in all_tickers:
        old = previous_entries.get(ticker)
        new = current_entries.get(ticker)

        old_state = old.get('state', 'ABSENT') if old else 'ABSENT'
        old_reason = old.get('reason', 'ABSENT') if old else 'ABSENT'
        new_state = new.state if new else 'ABSENT'
        new_reason = new.reason if new else 'ABSENT'

        if old_state != new_state or old_reason != new_reason:
            # Validate against state machine
            is_valid = validate_transition(ticker, old_state, new_state)
            note = new.notes if new else 'Ticker removed from current queue snapshot.'
            transitions.append(QueueTransition(
                ticker=ticker,
                old_state=old_state,
                new_state=new_state,
                old_reason=old_reason,
                new_reason=new_reason,
                changed_at=now_iso,
                note=note,
                valid_transition=is_valid,
            ))

    return transitions


def _append_transition_log(transitions: List[QueueTransition]) -> None:
    """Append transitions to a persistent JSONL audit log (one JSON object per line)."""
    if not transitions:
        return
    os.makedirs(os.path.dirname(QUEUE_TRANSITION_LOG_PATH), exist_ok=True)
    with open(QUEUE_TRANSITION_LOG_PATH, 'a', encoding='utf-8') as f:
        for tr in transitions:
            f.write(json.dumps(asdict(tr)) + '\n')


def load_transition_history(limit: int = 200) -> List[dict]:
    """Load the most recent N transitions from the persistent log."""
    if not os.path.exists(QUEUE_TRANSITION_LOG_PATH):
        return []
    entries = []
    try:
        with open(QUEUE_TRANSITION_LOG_PATH, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
    except Exception as e:
        print(f"[QueuePersistence] Failed to load transition log: {e}")
    return entries[-limit:]


def persist_queue_state(state: QueueGovernanceState) -> List[QueueTransition]:
    previous = load_queue_snapshot()
    transitions = diff_queue_state(previous, state)
    current_entries = _flatten_state(state)

    payload = {
        'updated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'headline_status': state.headline_status,
        'entries': {ticker: asdict(entry) for ticker, entry in current_entries.items()},
    }

    os.makedirs(os.path.dirname(QUEUE_STATE_PATH), exist_ok=True)
    with open(QUEUE_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)

    # Persistent JSONL audit trail (queryable history)
    _append_transition_log(transitions)

    for tr in transitions:
        validity_tag = '' if tr.valid_transition else ' [INVALID TRANSITION]'
        append_to_log(SessionLogEntry(
            timestamp=tr.changed_at,
            action_type='QUEUE_STATE_CHANGE',
            triggering_module='QUEUE_GOVERNANCE',
            triggering_signal=f"{tr.ticker}: {tr.old_state}/{tr.old_reason} -> {tr.new_state}/{tr.new_reason}.{validity_tag} {tr.note}",
            ticker=tr.ticker,
        ))

    if transitions:
        invalid_count = sum(1 for t in transitions if not t.valid_transition)
        print(f"[QueuePersistence] {len(transitions)} transition(s) persisted, {invalid_count} invalid.")

    return transitions
