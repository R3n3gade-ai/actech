"""
ARMS Engine: Asymmetric Upside Protocol (AUP)

This module provides the "Offensive" counterpart to ARMS' defensive systems.
It identifies periods of high-conviction, low-stress stability to unlock 
additional growth via the Synthetic Leverage Overlay Facility (SLOF).

"Precision at compounding capital."

Reference: ARMS FSD v1.1, Section 5.5
Reference: ARMS Intelligence Architecture Addendum 3, Section 5.1
"""

import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional

# --- Internal Imports ---
from engine.regime_probability import RegimeProbabilitySignal
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class AupStatus:
    """The result of an AUP evaluation run."""
    is_expansion_eligible: bool
    conditions_met: List[str]
    slof_multiplier: float # 1.0 (Normal) to 1.25 (Expanded)
    risk_limit_buffer: float # Extra room for winners
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- AUP Logic ---

def run_aup_check(
    current_regime: str,
    avg_mics_score: float, # Average conviction of the top 5 positions
    is_fem_clean: bool,    # No ALERT or WATCH concentrations
    rpe_watch_prob: float, # Probability of moving to WATCH/DEFENSIVE
    current_drawdown: float # Current % from High Water Mark
) -> AupStatus:
    """
    Evaluates the 5 "Golden Gates" for asymmetric capital deployment.
    """
    conditions = []
    
    # Condition 1: RISK_ON Regime
    if current_regime == "RISK_ON":
        conditions.append("REGIME_STRENGTH")
        
    # Condition 2: High Average Conviction (>7.5)
    if avg_mics_score > 7.5:
        conditions.append("CONVICTION_STRENGTH")
        
    # Condition 3: Clean FEM (No concentrations)
    if is_fem_clean:
        conditions.append("PORTFOLIO_CLEANLINESS")
        
    # Condition 4: Low Transition Probability (RPE < 20% toward WATCH)
    if rpe_watch_prob < 0.20:
        conditions.append("MACRO_STABILITY")
        
    # Condition 5: Minimal Drawdown (<12%)
    if current_drawdown < 0.12:
        conditions.append("NAV_RESILIENCE")
        
    is_eligible = len(conditions) == 5
    
    # Calculate SLOF Multiplier
    # Tier 1 action: System recommends expansion; PM has 4-hour veto.
    slof_mult = 1.25 if is_eligible else 1.00
    risk_buffer = 0.05 if is_eligible else 0.00 # Extra 5% cap room
    
    status = AupStatus(
        is_expansion_eligible=is_eligible,
        conditions_met=conditions,
        slof_multiplier=slof_mult,
        risk_limit_buffer=risk_buffer
    )
    
    # Audit Logging for AUP Activation
    if is_eligible:
        append_to_log(SessionLogEntry(
            timestamp=status.timestamp,
            action_type='AUP_ACTIVATION',
            triggering_module='AUP',
            triggering_signal="All 5 Golden Gates clear. Recommending SLOF expansion.",
            regime_at_action=current_regime
        ))
        print(f"[AUP] ASYMMETRIC UPSIDE ACTIVE. Recommending SLOF x{slof_mult}.")

    return status

if __name__ == '__main__':
    print("ARMS AUP Module Active (Simulation Mode)")
    
    # Simulate a "Golden Gate" Scenario
    res = run_aup_check(
        current_regime="RISK_ON",
        avg_mics_score=8.2,
        is_fem_clean=True,
        rpe_watch_prob=0.12,
        current_drawdown=0.04
    )
    print(f"AUP Status: {res}")
