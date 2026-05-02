"""
ARMS Engine: Portfolio Drawdown Sentinel (PDS)

CRITICAL — INDEPENDENT MODULE
Operates independently of ARAS. Monitors NAV against High-Water Mark.
Sets an absolute ceiling overriding ARAS if drawdowns become severe.

Reference: THB v4.0 §4 + CLAUDE_updated_with_queue.md (canonical thresholds).

Spec thresholds (entry — stateless threshold check, no hysteresis specified):
    DD <= -8%   -> WARNING        (ceiling 1.00, advisory)
    DD <= -12%  -> DELEVERAGE_1   (ceiling 0.60)
    DD <= -18%  -> DELEVERAGE_2   (ceiling 0.30)
"""
from dataclasses import dataclass

# Entry thresholds (drawdown <= value -> state engages)
PDS_WARN_ENTRY = -0.08
PDS_D1_ENTRY = -0.12
PDS_D2_ENTRY = -0.18

# Ceilings
PDS_CEIL_NORMAL = 1.00
PDS_CEIL_WARN = 1.00      # advisory only
PDS_CEIL_D1 = 0.60
PDS_CEIL_D2 = 0.30


@dataclass
class DrawdownSentinelSignal:
    current_nav: float
    high_water_mark: float
    drawdown_pct: float
    status: str
    pds_ceiling: float


def run_pds_check(
    current_nav: float,
    high_water_mark: float,
) -> DrawdownSentinelSignal:
    """
    Stateless PDS check against canonical entry thresholds.

    Spec is silent on exit thresholds / hysteresis; this implementation
    therefore evaluates the raw drawdown against the spec breakpoints on
    every call.
    """
    if high_water_mark <= 0:
        drawdown = 0.0
    else:
        drawdown = (current_nav / high_water_mark) - 1.0

    if drawdown <= PDS_D2_ENTRY:
        status = "DELEVERAGE_2"
        ceiling = PDS_CEIL_D2
    elif drawdown <= PDS_D1_ENTRY:
        status = "DELEVERAGE_1"
        ceiling = PDS_CEIL_D1
    elif drawdown <= PDS_WARN_ENTRY:
        status = "WARNING"
        ceiling = PDS_CEIL_WARN
    else:
        status = "NORMAL"
        ceiling = PDS_CEIL_NORMAL

    return DrawdownSentinelSignal(
        current_nav=current_nav,
        high_water_mark=high_water_mark,
        drawdown_pct=drawdown,
        status=status,
        pds_ceiling=ceiling,
    )
