"""
ARMS Engine: Regime Probability Engine (RPE) v2.0

This module provides an anticipatory layer to ARMS. It calculates the 
probability distribution of the next regime transition based on the 
velocity and volatility of key macro, equity, and crypto signals.

"Silence is trust in the architecture."

Reference: arms_fsd_master_build_v1.1.md, Section 5.1 & 11.1
Reference: ARMS Intelligence Architecture Addendum 3
"""

import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional
from collections import deque

# --- Internal Imports ---
from data_feeds.interfaces import SignalRecord
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class RegimeProbabilitySignal:
    """The output of the RPE, providing a forward-looking view."""
    current_regime: str
    transition_probabilities: Dict[str, float] = field(default_factory=dict)
    highest_prob_transition: str = "STABLE"
    highest_prob_value: float = 1.0
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- Configuration & State ---

# Historical window for calculating velocity (momentum) and volatility
# 12 entries at 5-minute intervals = 1-hour rolling window
WINDOW_SIZE = 12

# Signals tracked by RPE for transition forecasting
TRACKED_SIGNALS = [
    "VIX_INDEX", "10Y_TREASURY_YIELD", "HY_CREDIT_SPREAD", "T10Y2Y",
    "PMI_NOWCAST", "BTC_FUNDING", "LIQ_VOL_24H"
]

# In-memory store for rolling signal history
SIGNAL_HISTORY: Dict[str, deque] = {sig: deque(maxlen=WINDOW_SIZE) for sig in TRACKED_SIGNALS}

# Regime adjacency map (where we can go from where we are)
REGIME_MAP = {
    "RISK_ON":  {"up": "WATCH",     "down": None},
    "WATCH":    {"up": "NEUTRAL",   "down": "RISK_ON"},
    "NEUTRAL":  {"up": "DEFENSIVE", "down": "WATCH"},
    "DEFENSIVE":{"up": "CRASH",     "down": "NEUTRAL"},
    "CRASH":    {"up": None,        "down": "DEFENSIVE"}
}

# --- RPE Logic ---

def _calculate_signal_momentum(history: deque) -> float:
    """Calculates the normalized velocity (1st derivative) of a signal."""
    if len(history) < 2:
        return 0.0
    # Simple endpoint velocity: (latest - earliest) / length
    return (history[-1] - history[0]) / len(history)

def calculate_rpe(current_regime: str, 
                  latest_signals: List[SignalRecord]) -> RegimeProbabilitySignal:
    """
    Calculates regime transition probabilities based on signal velocity.
    
    A positive risk-off velocity (rising VIX, widening spreads) increases 
    the probability of an 'UP' transition (towards CRASH).
    """
    
    # 1. Update internal signal history
    for record in latest_signals:
        if record.signal_type in SIGNAL_HISTORY:
            SIGNAL_HISTORY[record.signal_type].append(record.value)

    # 2. Calculate momentum for risk-off and risk-on groups
    # Note: Values are already normalized 0-1 by the plugins.
    
    # Risk-Off Group (Rising is bad)
    vix_vel = _calculate_signal_momentum(SIGNAL_HISTORY["VIX_INDEX"])
    spread_vel = _calculate_signal_momentum(SIGNAL_HISTORY["HY_CREDIT_SPREAD"])
    liq_vel = _calculate_signal_momentum(SIGNAL_HISTORY["LIQ_VOL_24H"])
    
    # Risk-On Group (Falling is bad)
    pmi_vel = _calculate_signal_momentum(SIGNAL_HISTORY["PMI_NOWCAST"])
    btc_funding_vel = _calculate_signal_momentum(SIGNAL_HISTORY["BTC_FUNDING"])
    curve_vel = _calculate_signal_momentum(SIGNAL_HISTORY["T10Y2Y"])
    
    # Aggregate Risk-Off Pressure (Velocity)
    # Weights sum to 1.0
    risk_off_pressure = (
        (vix_vel * 0.3) + 
        (spread_vel * 0.3) + 
        (liq_vel * 0.1) - 
        (pmi_vel * 0.1) - 
        (btc_funding_vel * 0.1) - 
        (curve_vel * 0.1)
    )

    # 3. Map pressure to transition probabilities
    # 0.0 pressure = 100% STABLE. 
    # Positive pressure = probability of transition 'UP' (e.g., NEUTRAL -> DEFENSIVE)
    # Negative pressure = probability of transition 'DOWN' (e.g., NEUTRAL -> WATCH)
    
    probs = {r: 0.0 for r in ["RISK_ON", "WATCH", "NEUTRAL", "DEFENSIVE", "CRASH"]}
    probs["STABLE"] = 1.0
    
    adjacencies = REGIME_MAP.get(current_regime, {"up": None, "down": None})
    
    if risk_off_pressure > 0.01: # Threshold for meaningful move
        target = adjacencies["up"]
        if target:
            prob = min(0.45, risk_off_pressure * 50) # Scale and cap at 45%
            probs[target] = prob
            probs["STABLE"] -= prob
            
    elif risk_off_pressure < -0.01:
        target = adjacencies["down"]
        if target:
            prob = min(0.45, abs(risk_off_pressure) * 50)
            probs[target] = prob
            probs["STABLE"] -= prob

    # 4. Finalize result
    highest_prob_transition = max(probs, key=probs.get) # type: ignore
    
    result = RegimeProbabilitySignal(
        current_regime=current_regime,
        transition_probabilities=probs,
        highest_prob_transition=highest_prob_transition,
        highest_prob_value=probs[highest_prob_transition]
    )
    
    # 5. Audit Logging (Only log if transition probability > 20% or transition changed)
    if result.highest_prob_value > 0.20 and result.highest_prob_transition != "STABLE":
        append_to_log(SessionLogEntry(
            timestamp=result.timestamp,
            action_type='RPE_SIGNAL',
            triggering_module='RPE',
            triggering_signal=f"Elevated {result.highest_prob_transition} probability: {result.highest_prob_value:.1%}",
            regime_at_action=current_regime
        ))
        print(f"[RPE] Anticipatory signal: {result.highest_prob_transition} ({result.highest_prob_value:.1%})")

    return result

if __name__ == '__main__':
    print("ARMS RPE Module Active (Simulation Mode)")
    
    # Mock data showing rising stress
    mock_signals = [
        SignalRecord('MACRO', 'VIX_INDEX', 0.15, 15.0, 'MOCK', '', 'FREE'),
        SignalRecord('MACRO', 'HY_CREDIT_SPREAD', 0.40, 4.0, 'MOCK', '', 'FREE'),
        SignalRecord('MACRO', 'PMI_NOWCAST', 0.50, 50.0, 'MOCK', '', 'FREE'),
    ]
    
    # Fill history to simulate velocity
    for i in range(12):
        # Slightly increasing VIX and Spreads
        calculate_rpe("NEUTRAL", [
            SignalRecord('MACRO', 'VIX_INDEX', 0.15 + (i*0.01), 0, 'MOCK', '', 'FREE'),
            SignalRecord('MACRO', 'HY_CREDIT_SPREAD', 0.40 + (i*0.01), 0, 'MOCK', '', 'FREE'),
            SignalRecord('MACRO', 'PMI_NOWCAST', 0.50, 0, 'MOCK', '', 'FREE'),
            SignalRecord('MACRO', 'T10Y2Y', 0.20, 0, 'MOCK', '', 'FREE'),
            SignalRecord('MACRO', 'BTC_FUNDING', 0.50, 0, 'MOCK', '', 'FREE'),
            SignalRecord('MACRO', 'LIQ_VOL_24H', 0.10, 0, 'MOCK', '', 'FREE')
        ])

    print("RPE Simulation complete. Check logs or print output.")
