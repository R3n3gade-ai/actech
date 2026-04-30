"""
ARMS Engine: PDS State Persistence

Stores and retrieves the portfolio high-water mark so PDS uses durable state
instead of a demo constant or same-session placeholder.
"""

import json
import os
from datetime import datetime, timezone

PDS_STATE_PATH = "achelion_arms/state/pds_state.json"


def load_high_water_mark(default_nav: float) -> float:
    if not os.path.exists(PDS_STATE_PATH):
        return default_nav
    try:
        with open(PDS_STATE_PATH, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return float(payload.get('high_water_mark', default_nav))
    except Exception:
        return default_nav


def update_high_water_mark(current_nav: float) -> float:
    existing = load_high_water_mark(current_nav)
    hwm = max(existing, current_nav)
    os.makedirs(os.path.dirname(PDS_STATE_PATH), exist_ok=True)
    with open(PDS_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump({
            'high_water_mark': hwm,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }, f, indent=2)
    return hwm
