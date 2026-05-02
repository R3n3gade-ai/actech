"""
ARMS Engine: PDS State Persistence

Stores and retrieves the portfolio high-water mark so the live system uses
durable state across process boots.
"""

import json
import os
from datetime import datetime, timezone

PDS_STATE_PATH = "achelion_arms/state/pds_state.json"


def _read_state() -> dict:
    if not os.path.exists(PDS_STATE_PATH):
        return {}
    try:
        with open(PDS_STATE_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def load_high_water_mark(default_nav: float) -> float:
    payload = _read_state()
    try:
        return float(payload.get('high_water_mark', default_nav))
    except Exception:
        return default_nav


def _write_state(payload: dict) -> None:
    os.makedirs(os.path.dirname(PDS_STATE_PATH), exist_ok=True)
    payload['updated_at'] = datetime.now(timezone.utc).isoformat()
    with open(PDS_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)


def update_high_water_mark(current_nav: float) -> float:
    existing = _read_state()
    hwm = max(float(existing.get('high_water_mark', current_nav)), current_nav)
    existing['high_water_mark'] = hwm
    _write_state(existing)
    return hwm
