# src/reporting/audit_log.py
# Implements the immutable session log for all system actions.
# This is the foundational memory layer for the learning loop and audit trail.

import json
import os
import threading
from dataclasses import dataclass, asdict
from typing import Optional, Literal

# A lock to ensure that file writes are thread-safe.
_log_lock = threading.Lock()

# The canonical structure for every entry in the session log, based on FSD v1.1, Section 11.4
@dataclass
class SessionLogEntry:
    timestamp: str  # ISO format — millisecond precision
    action_type: str  # e.g., 'REGIME_CHANGE', 'TRADE', 'MICS_CALCULATION', 'CDF_DECAY'
    triggering_module: str  # e.g., 'ARAS', 'PDS', 'MICS', 'SENTINEL'
    triggering_signal: str  # Human-readable rationale for the action
    
    # Optional fields for context
    correlation_id: Optional[str] = None
    ticker: Optional[str] = None
    mics_score: Optional[float] = None
    regime_at_action: Optional[str] = None
    gate3_score: Optional[float] = None
    source_category: Optional[Literal['Cat A', 'Cat B', 'Cat C', 'None']] = None
    
    # Fields for tracking overrides and outcomes
    pm_override: bool = False
    override_rationale: Optional[str] = None
    outcome_90d: Optional[float] = None  # To be filled in 90 days after entry for learning


def append_to_log(entry: SessionLogEntry, log_file_path: str = "achelion_arms/logs/session_log.jsonl"):
    """
    Appends a new entry to the session log file in a thread-safe manner.
    The log is stored in JSON Lines format (one JSON object per line).

    Args:
        entry: The SessionLogEntry object to record.
        log_file_path: The path to the log file.
    """
    try:
        entry_dict = asdict(entry)
        log_dir = os.path.dirname(log_file_path)
        
        with _log_lock:
            # Ensure the directory exists
            if not os.path.exists(log_dir):
                os.makedirs(log_dir)
            
            # Append the entry as a new line in the file
            with open(log_file_path, 'a') as f:
                f.write(json.dumps(entry_dict) + '\n')
                
    except Exception as e:
        # In a production system, this should go to a dedicated error logger
        print(f"Error writing to audit log: {e}")
