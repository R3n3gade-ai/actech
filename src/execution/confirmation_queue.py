# src/execution/confirmation_queue.py
# Implements the Tier 1 & 2 Confirmation Queue with a durable JSON persistence backend.

import json
import os
import uuid
from dataclasses import dataclass, asdict
from typing import List, Literal, Optional, Dict
from datetime import datetime, timedelta, timezone

from .order_request import OrderRequest
from reporting.audit_log import append_to_log, SessionLogEntry


@dataclass
class QueuedAction:
    """
    Represents a single Tier 1 or Tier 2 action waiting for PM confirmation.
    """
    action_id: str
    item: OrderRequest  # Canonical execution request
    triggering_module: str
    rationale: str
    queued_at: datetime
    veto_window_hours: float
    
    status: Literal['PENDING', 'EXECUTED', 'HELD', 'VETOED', 'TIMED_OUT'] = 'PENDING'
    pm_rationale: Optional[str] = None
    responded_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d['queued_at'] = self.queued_at.isoformat()
        if self.responded_at:
            d['responded_at'] = self.responded_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> 'QueuedAction':
        d = dict(data)
        d['queued_at'] = datetime.fromisoformat(d['queued_at'])
        if d.get('responded_at'):
            d['responded_at'] = datetime.fromisoformat(d['responded_at'])
        
        # Deserialize OrderRequest
        order_dict = d.pop('item')
        d['item'] = OrderRequest(**order_dict)
        return cls(**d)


class ConfirmationQueue:
    """
    Manages the queue of Tier 1 & Tier 2 actions with durable JSON persistence.
    """
    
    def __init__(self, storage_path: str = "achelion_arms/state/confirmation_queue.json"):
        self.storage_path = storage_path
        self._queue: Dict[str, QueuedAction] = {}
        self._load_state()

    def _load_state(self):
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, 'r') as f:
                data = json.load(f)
                for item_dict in data.get("queue", []):
                    action = QueuedAction.from_dict(item_dict)
                    self._queue[action.action_id] = action
        except (json.JSONDecodeError, FileNotFoundError) as e:
            print(f"[ConfirmationQueue] Error loading state: {e}. Starting fresh.")

    def _save_state(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, 'w') as f:
            json.dump({
                "last_updated": datetime.now(timezone.utc).isoformat(),
                "queue": [a.to_dict() for a in self._queue.values()]
            }, f, indent=4)

    def add_action(self, action: QueuedAction):
        """Adds a new Tier 1 or Tier 2 action to the persistent queue."""
        self._queue[action.action_id] = action
        self._save_state()
        print(f"[ConfirmationQueue] New Action queued durably: {action.action_id} [CorID: {action.item.correlation_id}]")
        
        append_to_log(SessionLogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(),
            action_type='QUEUE_PENDING',
            triggering_module='CONFIRMATION_QUEUE',
            triggering_signal=f"Action {action.action_id} queued. Rationale: {action.rationale}",
            correlation_id=action.item.correlation_id,
            ticker=action.item.ticker
        ))

    def get_open_items(self) -> List[QueuedAction]:
        """Returns all actions currently in a 'PENDING' state."""
        return [a for a in self._queue.values() if a.status == 'PENDING']

    def submit_response(self, action_id: str, response: Literal['EXECUTE_NO_INFO', 'HOLD_NEW_INFO', 'VETO_NEW_INFO'], rationale: Optional[str] = None):
        """
        Submits a PM's response to a queued action via the Information-Quality Interface (FSD Section 11.5).
        Options:
        - EXECUTE_NO_INFO: PM has no new info, silence is trust. Executes immediately.
        - HOLD_NEW_INFO: PM has Cat A/B info. Opens source declaration form, pauses execution.
        - VETO_NEW_INFO: PM vetoes execution outright. Requires full documented rationale + GP cosign.
        """
        action = self._queue.get(action_id)
        if not action or action.status != 'PENDING':
            print(f"[ConfirmationQueue] Action {action_id} not found or already actioned.")
            return

        if response == 'VETO_NEW_INFO' and not rationale:
            print("[ConfirmationQueue] VETO_NEW_INFO response requires a documented rationale.")
            return
            
        if response == 'HOLD_NEW_INFO' and not rationale:
            print("[ConfirmationQueue] HOLD_NEW_INFO response requires a declared source category/insight.")
            return

        if response == 'EXECUTE_NO_INFO':
            action.status = 'EXECUTED'
        elif response == 'HOLD_NEW_INFO':
            action.status = 'HELD'
        elif response == 'VETO_NEW_INFO':
            action.status = 'VETOED'
        action.pm_rationale = rationale
        action.responded_at = datetime.now(timezone.utc)
        
        self._save_state()
        print(f"[ConfirmationQueue] PM responded to {action_id} with: {response} [CorID: {action.item.correlation_id}]")
        
        append_to_log(SessionLogEntry(
            timestamp=action.responded_at.isoformat(),
            action_type={
                'EXECUTE_NO_INFO': 'QUEUE_EXECUTED',
                'HOLD_NEW_INFO': 'QUEUE_HELD',
                'VETO_NEW_INFO': 'QUEUE_VETOED',
            }[response],
            triggering_module='CONFIRMATION_QUEUE',
            triggering_signal=f"PM responded {response}. Rationale: {rationale or 'None'}",
            correlation_id=action.item.correlation_id,
            ticker=action.item.ticker,
            pm_override=(response == 'VETO_NEW_INFO'),
            override_rationale=rationale if response == 'VETO_NEW_INFO' else None
        ))

    def check_for_timeouts(self):
        """
        Checks for actions where the veto window has expired and marks them
        for automatic execution.
        """
        now = datetime.now(timezone.utc)
        state_changed = False
        
        for action in self.get_open_items():
            # Ensure queued_at is timezone-aware for comparison
            queued_at = action.queued_at
            if queued_at.tzinfo is None:
                queued_at = queued_at.replace(tzinfo=timezone.utc)

            expiry_time = queued_at + timedelta(hours=action.veto_window_hours)
            
            if now >= expiry_time:
                action.status = 'TIMED_OUT'
                action.responded_at = now
                action.pm_rationale = "Time window expired; system defaulting to execution."
                state_changed = True
                
                print(f"[ConfirmationQueue] Action {action.action_id} timed out. Proceeding to EXECUTED. [CorID: {action.item.correlation_id}]")
                
                append_to_log(SessionLogEntry(
                    timestamp=now.isoformat(),
                    action_type='QUEUE_TIMED_OUT',
                    triggering_module='CONFIRMATION_QUEUE',
                    triggering_signal=f"Action {action.action_id} timed out. Proceeding to execution.",
                    correlation_id=action.item.correlation_id,
                    ticker=action.item.ticker
                ))

        if state_changed:
            self._save_state()

# The execution layer can now poll get_open_items() or process actions marked 'EXECUTED' / 'TIMED_OUT'.
