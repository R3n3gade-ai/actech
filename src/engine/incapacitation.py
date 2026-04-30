"""
ARMS Engine: Incapacitation Protocol

This module provides the final safety backstop for ARMS. It implements strict 
fallback timers (4h, 24h, 72h) that monitor PM heartbeat/responsiveness. 
If the PM "goes dark" and fails to respond to Tier 1/2 prompts or system 
pings, the system automatically transitions the portfolio to a increasingly 
defensive posture to preserve capital until human oversight is restored.

"Silence is trust in the architecture, but prolonged silence is risk."

Reference: Codebase Game Plan v2.0, Step 5
Reference: Project Plan, Section 3.1
"""

import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional

# --- Internal Imports ---
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class IncapacitationStatus:
    """The current state of the Incapacitation safety layer."""
    is_pm_active: bool
    last_pm_heartbeat: str
    hours_since_heartbeat: float
    current_safety_tier: int # 0: Normal, 1: Watch, 2: Neutral, 3: Defensive
    forced_regime_override: Optional[str]
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- Configuration ---

# Timer thresholds in hours
T1_WATCH_HOURS = 4.0      # System enters heightened alert
T2_NEUTRAL_HOURS = 24.0   # Force portfolio to NEUTRAL regime ceiling
T3_DEFENSIVE_HOURS = 72.0 # Force portfolio to DEFENSIVE/CRASH ceilings

# --- Incapacitation Logic ---

def run_incapacitation_check(
    last_heartbeat: datetime.datetime,
    current_regime: str
) -> IncapacitationStatus:
    """
    Evaluates PM responsiveness and triggers safety fallbacks if timers expire.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    delta = now - last_heartbeat
    hours_inactive = delta.total_seconds() / 3600.0
    
    tier = 0
    forced_regime = None
    is_active = True
    
    # Tier 3: DEFENSIVE Fallback (72h)
    if hours_inactive >= T3_DEFENSIVE_HOURS:
        tier = 3
        forced_regime = "DEFENSIVE"
        is_active = False
        
    # Tier 2: NEUTRAL Fallback (24h)
    elif hours_inactive >= T2_NEUTRAL_HOURS:
        tier = 2
        forced_regime = "NEUTRAL"
        is_active = False
        
    # Tier 1: WATCH Tier (4h)
    elif hours_inactive >= T1_WATCH_HOURS:
        tier = 1
        is_active = True # Still active but flagged
        
    # Logic: Overrides only permitted to make portfolio MORE defensive
    # If the system is already in CRASH, we don't 'upgrade' to DEFENSIVE.
    if forced_regime:
        regime_hierarchy = ["RISK_ON", "WATCH", "NEUTRAL", "DEFENSIVE", "CRASH"]
        current_idx = regime_hierarchy.index(current_regime)
        forced_idx = regime_hierarchy.index(forced_regime)
        
        if current_idx >= forced_idx:
            # System is already safer than the fallback
            forced_regime = None 

    result = IncapacitationStatus(
        is_pm_active=is_active,
        last_pm_heartbeat=last_heartbeat.isoformat(),
        hours_since_heartbeat=hours_inactive,
        current_safety_tier=tier,
        forced_regime_override=forced_regime
    )

    # 4. Audit Logging for Tier Changes
    if tier > 0:
        append_to_log(SessionLogEntry(
            timestamp=result.timestamp,
            action_type='INCAPACITATION_ALERT',
            triggering_module='INCAPACITATION',
            triggering_signal=f"PM Inactive for {hours_inactive:.1f}h. Safety Tier {tier} Active.",
            regime_at_action=current_regime
        ))
        
        if forced_regime:
            print(f"[INCAPACITATION] PM DARK. FORCING {forced_regime} REGIME CEILING.")

    return result

if __name__ == '__main__':
    print("ARMS Incapacitation Protocol Active (Simulation Mode)")
    
    # 1. Simulate Normal Active PM (1 hour since heartbeat)
    heartbeat_1h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=1)
    res_1 = run_incapacitation_check(heartbeat_1h, "RISK_ON")
    print(f"Outcome (1h): Tier {res_1.current_safety_tier} - Override: {res_1.forced_regime_override}")

    # 2. Simulate PM Dark (30 hours since heartbeat)
    heartbeat_30h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=30)
    res_30 = run_incapacitation_check(heartbeat_30h, "RISK_ON")
    print(f"Outcome (30h): Tier {res_30.current_safety_tier} - Override: {res_30.forced_regime_override}")
    
    # 3. Simulate PM Dark (80 hours since heartbeat)
    heartbeat_80h = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=80)
    res_80 = run_incapacitation_check(heartbeat_80h, "NEUTRAL")
    print(f"Outcome (80h): Tier {res_80.current_safety_tier} - Override: {res_80.forced_regime_override}")
