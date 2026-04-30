"""
ARMS Engine: Margin Stress

ARAS Sub-Module evaluating system-wide margin stress.
Uses HY credit spread and VIX as real-time proxies for margin pressure,
since FINRA margin debt data has a ~2 month reporting lag.

Reference: THB v4.0, Section 10
"""
from dataclasses import dataclass
from typing import List, Optional
from data_feeds.interfaces import SignalRecord

@dataclass
class MarginStressSignal:
    status: str
    stress_level: float

def run_margin_stress_check(signals: Optional[List[SignalRecord]] = None) -> MarginStressSignal:
    if signals is None:
        return MarginStressSignal(status="NORMAL", stress_level=0.0)
    
    hy_spread = next((s.value for s in signals if s.signal_type == 'HY_CREDIT_SPREAD'), None)
    vix = next((s.value for s in signals if s.signal_type == 'VIX_INDEX'), None)
    
    if hy_spread is None or vix is None:
        return MarginStressSignal(status="NO_DATA", stress_level=0.0)
    
    # HY spread > 5.0% historically correlates with margin call cascades
    # VIX > 0.30 (raw, i.e. 30 on the index) signals acute dealer stress
    stress = 0.0
    if hy_spread > 5.0:
        stress += 0.4
    elif hy_spread > 4.0:
        stress += 0.2
    
    vix_level = vix * 100.0 if vix < 1.0 else vix
    if vix_level > 35:
        stress += 0.4
    elif vix_level > 25:
        stress += 0.2
    
    status = "NORMAL"
    if stress >= 0.6:
        status = "ACTIVE"
    elif stress >= 0.3:
        status = "ELEVATED"
    
    return MarginStressSignal(status=status, stress_level=round(stress, 2))
