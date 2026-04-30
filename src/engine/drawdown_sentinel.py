"""
ARMS Engine: Portfolio Drawdown Sentinel (PDS)

CRITICAL — INDEPENDENT MODULE
Operates independently of ARAS. Monitors NAV against High-Water Mark.
Sets an absolute ceiling overriding ARAS if drawdowns become severe.

Reference: THB v4.0, Section 4
"""
from dataclasses import dataclass

@dataclass
class DrawdownSentinelSignal:
    current_nav: float
    high_water_mark: float
    drawdown_pct: float
    status: str
    pds_ceiling: float

def run_pds_check(current_nav: float, high_water_mark: float) -> DrawdownSentinelSignal:
    if high_water_mark <= 0:
        drawdown = 0.0
    else:
        drawdown = (current_nav / high_water_mark) - 1.0
        
    status = "NORMAL"
    ceiling = 1.0 # 100%
    
    if drawdown <= -0.18:
        status = "DELEVERAGE_2"
        ceiling = 0.30 # 30% gross
    elif drawdown <= -0.12:
        status = "DELEVERAGE_1"
        ceiling = 0.60 # 60% gross
    elif drawdown <= -0.08:
        status = "WARNING"
        ceiling = 1.00 # Advisory only
        
    return DrawdownSentinelSignal(
        current_nav=current_nav,
        high_water_mark=high_water_mark,
        drawdown_pct=drawdown,
        status=status,
        pds_ceiling=ceiling
    )
