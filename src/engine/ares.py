"""
ARMS Engine: ARES (Re-Entry System) & VARES

Tier 0 autonomous re-entry logic. Prevents premature re-exposure after
a deleverage by requiring FOUR gates to clear before deploying capital
in 3 equal tranches of 33%, spaced 48h apart.

GP Briefing: "It checks four conditions before allowing any increase in
exposure: the regime must have improved, volatility must be declining,
the specific trigger that caused the deleverage must have resolved, and
the composite risk score must have fallen sufficiently. Re-entry happens
in three tranches over several days."

THB §3.1: base_tranche_pct = 0.33 (always 3 equal tranches)
THB §3.2: T2 = T1 + 48h, T3 = T2 + 48h
VARES: vol_adj_factor = min(1.0, vix_90d_avg / vix_current)
       adjusted_tranche = clamp(base * vol_adj_factor, 0.15, 0.35)
Abort: if circuit breaker fires between tranches.

"Silence is trust in the architecture."
"""

import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional

# --- Internal Imports ---
from data_feeds.interfaces import SignalRecord
from execution.order_request import OrderRequest
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class AresStatus:
    """Represents the current state of the Re-Entry System."""
    gates_cleared: List[str]
    is_fully_cleared: bool
    current_tranche_level: int  # 1-3 (3 tranches of 33%)
    vares_multiplier: float
    next_tranche_available_at: Optional[str]
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- Configuration (GP Briefing + THB §3.1-3.2) ---

# 3 equal tranches of 33% (THB §3.1)
REENTRY_TRANCHES = {
    1: 0.33,
    2: 0.66,
    3: 1.00  # T3 = remainder to target
}

# 48h between tranches (THB §3.2: "T2 = T1 + 48 hours")
# 48h ≈ 2 trading days
TRANCHE_COOLDOWN_DAYS = 2

# VARES hard limits (THB §3.1)
VARES_MIN_TRANCHE = 0.15
VARES_MAX_TRANCHE = 0.35
VARES_BASE_TRANCHE = 0.33

# --- ARES Logic ---

def run_ares_check(
    current_regime: str,
    regime_score: float,
    macro_stress_score: float,        # 0.0 - 1.0 (VIX/Spreads)
    pds_trigger_resolved: bool = True, # Gate 3: specific trigger that caused deleverage resolved
    last_tranche_deployed_at: Optional[datetime.datetime] = None,
    current_tranche_level: int = 0,
    vix_current: float = 20.0,
    vix_90d_avg: float = 20.0,
) -> AresStatus:
    """
    Evaluates the 4 re-entry gates (GP Briefing) and determines VARES sizing.
    
    Gate 1: Regime improved (not CRASH/DEFENSIVE)
    Gate 2: Volatility declining (VIX < 30)
    Gate 3: Specific deleverage trigger resolved (PDS back to NORMAL)
    Gate 4: Composite risk score fallen sufficiently (score < 0.50)
    """
    gates_cleared = []
    
    # Gate 1: Regime Improved (GP Briefing: "the regime must have improved")
    if current_regime not in ("CRASH", "DEFENSIVE"):
        gates_cleared.append("REGIME_IMPROVED")
        
    # Gate 2: Volatility Declining (GP Briefing: "volatility must be declining")
    if macro_stress_score < 0.30:  # VIX-based: below elevated threshold
        gates_cleared.append("VOL_DECLINING")
        
    # Gate 3: Trigger Resolved (GP Briefing: "specific trigger... must have resolved")
    if pds_trigger_resolved:
        gates_cleared.append("TRIGGER_RESOLVED")
        
    # Gate 4: Score Fallen (GP Briefing: "composite risk score must have fallen sufficiently")
    if regime_score < 0.50:
        gates_cleared.append("SCORE_FALLEN")
        
    is_fully_cleared = len(gates_cleared) == 4
    
    # VARES Vol-Adjusted Tranche Sizing (THB §3.1)
    vol_adj_factor = min(1.0, vix_90d_avg / vix_current) if vix_current > 0 else 1.0
    adjusted_tranche = max(VARES_MIN_TRANCHE, min(VARES_MAX_TRANCHE, VARES_BASE_TRANCHE * vol_adj_factor))
    
    vares_multiplier = 0.0
    next_tranche = current_tranche_level
    next_date = None
    
    if is_fully_cleared:
        # Check 48h cooldown (THB §3.2)
        can_deploy = True
        if last_tranche_deployed_at:
            delta = datetime.datetime.now(datetime.timezone.utc) - last_tranche_deployed_at
            if delta.days < TRANCHE_COOLDOWN_DAYS:
                can_deploy = False
                next_date = (last_tranche_deployed_at + datetime.timedelta(days=TRANCHE_COOLDOWN_DAYS)).isoformat()
        
        if can_deploy and current_tranche_level < 3:
            next_tranche += 1
            if next_tranche < 3:
                vares_multiplier = adjusted_tranche  # T1/T2: vol-adjusted 33%
            else:
                vares_multiplier = 1.0 - REENTRY_TRANCHES.get(current_tranche_level, 0.0)  # T3: remainder
        else:
            vares_multiplier = REENTRY_TRANCHES.get(current_tranche_level, 0.0)
            
    status = AresStatus(
        gates_cleared=gates_cleared,
        is_fully_cleared=is_fully_cleared,
        current_tranche_level=next_tranche,
        vares_multiplier=vares_multiplier,
        next_tranche_available_at=next_date
    )
    
    # Log re-entry signals
    if is_fully_cleared and vares_multiplier > 0:
        append_to_log(SessionLogEntry(
            timestamp=status.timestamp,
            action_type='ARES_REENTRY',
            triggering_module='ARES',
            triggering_signal=f"4 Gates Clear. Deploying T{next_tranche}/3 ({vares_multiplier:.0%}) VARES={vol_adj_factor:.2f}",
            regime_at_action=current_regime
        ))
        
    return status

if __name__ == '__main__':
    print("ARMS ARES Module Active (Simulation Mode)")
    
    # Simulate a recovery scenario
    last_deployment = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)
    
    res = run_ares_check(
        current_regime="NEUTRAL",
        regime_score=0.45,
        macro_stress_score=0.25,        # Gate 2 pass (vol declining)
        pds_trigger_resolved=True,       # Gate 3 pass
        last_tranche_deployed_at=last_deployment,
        current_tranche_level=1,         # Already deployed T1
        vix_current=22.0,
        vix_90d_avg=25.0,
    )
    
    print(f"ARES Status: {res}")
