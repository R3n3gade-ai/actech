"""
ARMS Engine: Conviction Decay Function (CDF)

This module prevents "thesis drift" by automatically decaying the weights
of positions that underperform the QQQ for extended periods.

"Silence is trust in the architecture."

Reference: ARMS v4.0 Briefing, Section 2.3
Reference: FSD v1.1, Section 2 (Tier 0)
"""

import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional

# --- Internal Imports ---
from reporting.audit_log import SessionLogEntry, append_to_log

from reporting.audit_log import SessionLogEntry, append_to_log
from execution.order_request import OrderRequest
from engine.cdf_analytics import compute_live_underperformance, RelativePerformanceSnapshot

# --- Data Structures ---

@dataclass
class CDFStatus:
    """Represents the decay state of a specific position."""
    ticker: str
    days_underperforming: int
    underperformance_pp: float # Percentage points vs QQQ
    current_multiplier: float
    next_decay_at: Optional[int]
    is_orderly_exit_due: bool
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- Configuration ---

# Threshold for underperformance (Percentage Points)
UNDERPERFORMANCE_THRESHOLD = 10.0 

# Decay Milestones
DECAY_MILESTONES = {
    45: 0.80, # 20% reduction
    90: 0.60  # 40% total reduction
}

# Final Milestone
EXIT_MILESTONE = 135

# --- CDF Logic ---

def evaluate_position_decay(ticker: str, days_underperforming_override: Optional[int] = None) -> Optional[CDFStatus]:
    """
    Evaluates the live underperformance of a position and returns its decay state.
    Uses Yahoo Finance historical data to calculate actual vs QQQ performance.
    """
    # Default to checking the 45-day milestone
    days_to_check = days_underperforming_override or 45
    
    snapshot = compute_live_underperformance(ticker, days_to_check)
    if not snapshot:
        return None
        
    return calculate_position_decay(
        ticker=ticker,
        days_underperforming=snapshot.days_back,
        underperformance_pp=snapshot.underperformance_pp
    )

def calculate_position_decay(
    ticker: str,
    days_underperforming: int,
    underperformance_pp: float
) -> CDFStatus:
    """
    Calculates the current weight multiplier and exit status for a position.
    """
    current_multiplier = 1.0
    next_decay = None
    is_exit_due = False
    
    # Check if threshold is met
    if underperformance_pp >= UNDERPERFORMANCE_THRESHOLD:
        # Determine multiplier based on milestones
        if days_underperforming >= EXIT_MILESTONE:
            current_multiplier = 0.60  # THB §7.1: Hold at 0.60 — mandatory PM review
            is_exit_due = False  # Hold, not exit. TRP handles orderly exit (180d + CDF 0.60)
        elif days_underperforming >= 90:
            current_multiplier = 0.60
            next_decay = EXIT_MILESTONE
        elif days_underperforming >= 45:
            current_multiplier = 0.80
            next_decay = 90
        else:
            next_decay = 45
            
    status = CDFStatus(
        ticker=ticker,
        days_underperforming=days_underperforming,
        underperformance_pp=underperformance_pp,
        current_multiplier=current_multiplier,
        next_decay_at=next_decay,
        is_orderly_exit_due=is_exit_due
    )
    
    # Audit Logging for Decay Events
    if days_underperforming in [45, 90, EXIT_MILESTONE] and underperformance_pp >= UNDERPERFORMANCE_THRESHOLD:
        append_to_log(SessionLogEntry(
            timestamp=status.timestamp,
            action_type='CDF_DECAY',
            triggering_module='CDF',
            triggering_signal=f"Day {days_underperforming} Milestone: multiplier set to {current_multiplier:.2x}",
            ticker=ticker
        ))
        print(f"[CDF] Decay Applied: {ticker} (Day {days_underperforming}) -> {current_multiplier:.2x}")

    return status

if __name__ == '__main__':
    print("ARMS CDF Module Active (Simulation Mode)")
    
    # Test Day 45 Decay
    res_45 = calculate_position_decay("NVDA", 45, 12.5)
    print(f"Status (Day 45): {res_45}")
    
    # Test Day 135 Exit
    res_135 = calculate_position_decay("TSLA", 135, 15.0)
    print(f"Status (Day 135): {res_135}")
