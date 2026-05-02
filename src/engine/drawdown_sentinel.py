"""
ARMS Engine: Portfolio Drawdown Sentinel (PDS)

CRITICAL — INDEPENDENT MODULE
Operates independently of ARAS. Monitors NAV against High-Water Mark.
Sets an absolute ceiling overriding ARAS if drawdowns become severe.

Reference: THB v4.0 §4 + CLAUDE_updated_with_queue.md (canonical thresholds).
PM Playbook v4.0 TABLE 5:
    WARNING     — None (advisory only). No automatic action.
    DELEVERAGE_1 — 60% ceiling enforced. Kill chain executes.
    DELEVERAGE_2 — 30% ceiling enforced. LP narrative triggered.

Entry thresholds (canonical spec):
    DD <= -8%   -> WARNING        (ceiling 1.00, advisory)
    DD <= -12%  -> DELEVERAGE_1   (ceiling 0.60)
    DD <= -18%  -> DELEVERAGE_2   (ceiling 0.30)

Exit hysteresis (spec is silent; added to prevent D1/D2 oscillation):
    DELEVERAGE_1 exits only when DD recovers above WARNING threshold (-8%).
    DELEVERAGE_2 exits only when DD recovers above D1 threshold (-12%).
    This prevents the kill chain from firing, slightly recovering, and
    immediately re-firing because NAV hovers near the entry threshold.
"""
from dataclasses import dataclass

# Entry thresholds (drawdown <= value -> state engages)
PDS_WARN_ENTRY = -0.08
PDS_D1_ENTRY = -0.12
PDS_D2_ENTRY = -0.18

# Ceilings
PDS_CEIL_NORMAL = 1.00
PDS_CEIL_WARN = 1.00      # advisory only — no ceiling reduction (PM Playbook TABLE 5)
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
    last_status: str = "NORMAL",
) -> DrawdownSentinelSignal:
    """
    PDS check with entry thresholds (canonical spec) and exit hysteresis
    (engineering addition to prevent D1/D2 oscillation near threshold).

    Parameters
    ----------
    current_nav   : Current portfolio NAV.
    high_water_mark : All-time NAV high-water mark.
    last_status   : PDS status from the previous evaluation (for hysteresis).
                    Defaults to "NORMAL" for initial call.
    """
    if high_water_mark <= 0:
        drawdown = 0.0
    else:
        drawdown = (current_nav / high_water_mark) - 1.0

    # ── Raw entry state (canonical spec thresholds) ───────────────────────
    if drawdown <= PDS_D2_ENTRY:
        raw_status = "DELEVERAGE_2"
    elif drawdown <= PDS_D1_ENTRY:
        raw_status = "DELEVERAGE_1"
    elif drawdown <= PDS_WARN_ENTRY:
        raw_status = "WARNING"
    else:
        raw_status = "NORMAL"

    # ── Exit hysteresis ───────────────────────────────────────────────────
    # D1 exits only when drawdown recovers above the WARNING entry threshold.
    # D2 exits only when drawdown recovers above the D1 entry threshold.
    # This prevents whipsaw: D1 fires → kill chain executes → NAV recovers
    # 0.1% → D1 exits → ARES re-buys → NAV dips 0.1% → D1 fires again.
    if last_status == "DELEVERAGE_2" and raw_status in ("DELEVERAGE_1", "WARNING", "NORMAL"):
        # D2 exit requires recovery above D1 entry threshold (-12%)
        if drawdown > PDS_D1_ENTRY:
            status = raw_status       # recovered enough — apply raw entry state
        else:
            status = "DELEVERAGE_2"   # still below D1 threshold — stay in D2
    elif last_status == "DELEVERAGE_1" and raw_status in ("WARNING", "NORMAL"):
        # D1 exit requires recovery above WARNING entry threshold (-8%)
        if drawdown > PDS_WARN_ENTRY:
            status = raw_status       # recovered enough — apply raw entry state
        else:
            status = "DELEVERAGE_1"   # still below WARNING threshold — stay in D1
    else:
        status = raw_status

    # ── Ceiling ───────────────────────────────────────────────────────────
    if status == "DELEVERAGE_2":
        ceiling = PDS_CEIL_D2
    elif status == "DELEVERAGE_1":
        ceiling = PDS_CEIL_D1
    elif status == "WARNING":
        ceiling = PDS_CEIL_WARN
    else:
        ceiling = PDS_CEIL_NORMAL

    return DrawdownSentinelSignal(
        current_nav=current_nav,
        high_water_mark=high_water_mark,
        drawdown_pct=drawdown,
        status=status,
        pds_ceiling=ceiling,
    )
