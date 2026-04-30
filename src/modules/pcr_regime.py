"""
ARMS Engine: Put/Call Ratio Regime

ARAS Sub-Module evaluating equity put/call ratios for extreme
fear or greed structural imbalances. Uses CBOE equity put/call ratio
data from the data pipeline.

PCR interpretation:
  < 0.60 → GREED (complacency / potential reversal risk)
  0.60 - 0.80 → NORMAL (balanced options flow)
  0.80 - 1.00 → CAUTIOUS (mild hedging activity)
  > 1.00 → FEAR (heavy put buying / potential capitulation)
  > 1.30 → EXTREME_FEAR (contrarian bullish signal — panic exhaustion)

Reference: THB v4.0, Section 10
"""
import os
import json
from dataclasses import dataclass
from typing import List, Optional
from data_feeds.interfaces import SignalRecord

@dataclass
class PcrRegimeSignal:
    status: str   # EXTREME_FEAR | FEAR | CAUTIOUS | NORMAL | GREED
    pcr_value: float
    detail: str

# State file for tracking PCR trend (rising/falling over 5 sessions)
_STATE_PATH = os.path.join('achelion_arms', 'state', 'pcr_history.json')

def _load_pcr_history() -> List[float]:
    if os.path.exists(_STATE_PATH):
        try:
            with open(_STATE_PATH, 'r') as f:
                return json.load(f).get('values', [])
        except Exception:
            pass
    return []

def _save_pcr_history(values: List[float]):
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, 'w') as f:
        json.dump({'values': values[-30:]}, f)

def run_pcr_regime_check(
    signals: Optional[List[SignalRecord]] = None,
    pcr_override: Optional[float] = None,
) -> PcrRegimeSignal:
    """
    Evaluate put/call ratio regime.
    
    Args:
        signals: Data pipeline signals (looks for 'EQUITY_PCR' signal type).
        pcr_override: Direct PCR value override (for testing or manual input).
    
    Returns:
        PcrRegimeSignal with regime classification and trend context.
    """
    pcr = pcr_override
    
    if pcr is None and signals:
        pcr_sig = next((s for s in signals if s.signal_type == 'EQUITY_PCR'), None)
        if pcr_sig is not None:
            pcr = pcr_sig.value
    
    if pcr is None:
        # No PCR data available — read last known value from history
        history = _load_pcr_history()
        pcr = history[-1] if history else 0.85  # Default neutral
    
    # Track history for trend analysis
    history = _load_pcr_history()
    history.append(pcr)
    _save_pcr_history(history)
    
    # 5-session trend
    recent = history[-5:]
    if len(recent) >= 3:
        trend = "rising" if recent[-1] > recent[0] else "falling" if recent[-1] < recent[0] else "flat"
    else:
        trend = "insufficient_data"
    
    # Classify regime
    if pcr > 1.30:
        status = "EXTREME_FEAR"
        detail = f"PCR {pcr:.2f} — extreme put buying, potential capitulation/contrarian bullish. Trend: {trend}."
    elif pcr > 1.00:
        status = "FEAR"
        detail = f"PCR {pcr:.2f} — heavy hedging activity, elevated fear. Trend: {trend}."
    elif pcr > 0.80:
        status = "CAUTIOUS"
        detail = f"PCR {pcr:.2f} — mild hedging above norm. Trend: {trend}."
    elif pcr >= 0.60:
        status = "NORMAL"
        detail = f"PCR {pcr:.2f} — balanced options flow. Trend: {trend}."
    else:
        status = "GREED"
        detail = f"PCR {pcr:.2f} — complacency, low hedging. Reversal risk elevated. Trend: {trend}."
    
    return PcrRegimeSignal(
        status=status,
        pcr_value=round(pcr, 3),
        detail=detail,
    )
