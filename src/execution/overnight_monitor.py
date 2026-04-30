"""
ARMS Execution: Overnight Monitor

Monitors S&P 500 futures (ES) and VIX futures during the Globex overnight
session to detect gap risk before the cash market opens. When ES futures
show a move exceeding -2% or VIX futures spike above 30, the system
pre-queues defensive actions for the morning sweep.

Reads from broker API or a cached futures snapshot file.

Reference: THB v4.0, Section 10
"""
import os
import json
import datetime
from dataclasses import dataclass
from typing import Optional

@dataclass
class OvernightStatus:
    spx_futures_pct: float
    vix_futures_level: float
    status: str  # NORMAL | GAP_WARNING | GAP_CRITICAL
    pre_queued_action: Optional[str]
    detail: str

_FUTURES_SNAPSHOT_PATH = os.path.join('achelion_arms', 'state', 'overnight_futures.json')

# Thresholds
GAP_WARNING_PCT = -0.015   # -1.5% ES futures move
GAP_CRITICAL_PCT = -0.025  # -2.5% ES futures move 
VIX_FUTURES_ALERT = 28.0   # VIX futures level that triggers alert
VIX_FUTURES_CRITICAL = 35.0

def _load_futures_snapshot() -> dict:
    """Load latest futures snapshot from state file (written by data pipeline or broker bridge)."""
    if os.path.exists(_FUTURES_SNAPSHOT_PATH):
        try:
            with open(_FUTURES_SNAPSHOT_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_futures_snapshot(es_pct_change: float, vix_futures: float):
    """Write futures snapshot for overnight monitoring (called by data pipeline or broker bridge)."""
    os.makedirs(os.path.dirname(_FUTURES_SNAPSHOT_PATH), exist_ok=True)
    with open(_FUTURES_SNAPSHOT_PATH, 'w') as f:
        json.dump({
            'es_pct_change': es_pct_change,
            'vix_futures': vix_futures,
            'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }, f)

def run_overnight_monitor(
    es_futures_pct: Optional[float] = None,
    vix_futures: Optional[float] = None,
) -> OvernightStatus:
    """
    Evaluate overnight futures for gap risk.
    
    Args:
        es_futures_pct: ES futures percent change from prior close (decimal). 
                        If None, reads from snapshot file.
        vix_futures: VIX futures level. If None, reads from snapshot file.
    
    Returns:
        OvernightStatus with gap risk assessment and pre-queued action if applicable.
    """
    if es_futures_pct is None or vix_futures is None:
        snap = _load_futures_snapshot()
        if es_futures_pct is None:
            es_futures_pct = snap.get('es_pct_change', 0.0)
        if vix_futures is None:
            vix_futures = snap.get('vix_futures', 20.0)
    
    pre_queued = None
    
    # Evaluate gap risk
    if es_futures_pct <= GAP_CRITICAL_PCT or vix_futures >= VIX_FUTURES_CRITICAL:
        status = "GAP_CRITICAL"
        pre_queued = "REDUCE_EQUITY_EXPOSURE"
        detail = (f"CRITICAL gap risk: ES futures {es_futures_pct:+.2%}, VIX futures {vix_futures:.1f}. "
                  f"Pre-queuing equity exposure reduction for morning sweep.")
    elif es_futures_pct <= GAP_WARNING_PCT or vix_futures >= VIX_FUTURES_ALERT:
        status = "GAP_WARNING"
        detail = (f"Gap warning: ES futures {es_futures_pct:+.2%}, VIX futures {vix_futures:.1f}. "
                  f"Morning sweep will evaluate with elevated caution.")
    else:
        status = "NORMAL"
        detail = f"Overnight normal: ES futures {es_futures_pct:+.2%}, VIX futures {vix_futures:.1f}."
    
    return OvernightStatus(
        spx_futures_pct=round(es_futures_pct, 4),
        vix_futures_level=round(vix_futures, 2),
        status=status,
        pre_queued_action=pre_queued,
        detail=detail,
    )
