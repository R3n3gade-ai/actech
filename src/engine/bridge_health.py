"""
ARMS Engine: Bridge Health

Reports presence/staleness of interim bridge-backed inputs so the live cycle
can surface operating readiness instead of quietly degrading.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List

from engine.bridge_paths import bridge_path


@dataclass
class BridgeHealthRecord:
    name: str
    path: str
    exists: bool
    age_hours: float | None
    status: str


def _record(name: str, env_var: str, default_filename: str, stale_after_hours: float) -> BridgeHealthRecord:
    path = bridge_path(env_var, default_filename)
    exists = os.path.exists(path)
    if not exists:
        return BridgeHealthRecord(name, path, False, None, 'MISSING')

    mtime = datetime.fromtimestamp(os.path.getmtime(path), tz=timezone.utc)
    age_h = max(0.0, (datetime.now(timezone.utc) - mtime).total_seconds() / 3600.0)
    status = 'STALE' if age_h > stale_after_hours else 'OK'
    return BridgeHealthRecord(name, path, True, age_h, status)


def collect_bridge_health() -> List[BridgeHealthRecord]:
    return [
        _record('SENTINEL', 'ARMS_SENTINEL_JSON', 'sentinel_records.json', 168.0),
        _record('RSS', 'ARMS_RSS_JSON', 'rss_inputs.json', 72.0),
        _record('CDF', 'ARMS_CDF_JSON', 'cdf_inputs.json', 72.0),
        _record('PMI', 'ARMS_PMI_CSV', 'pmi_latest.csv', 168.0),
        _record('SEC_WATCHLIST', 'ARMS_SEC_WATCHLIST_JSON', 'sec_watchlist.json', 720.0),
        _record('EVENT_BRIDGE', 'ARMS_EVENT_JSON', 'event_bridge.json', 24.0),
        _record('CONSENSUS_OVERRIDES', 'ARMS_CONSENSUS_JSON', 'consensus_overrides.json', 720.0),
    ]
