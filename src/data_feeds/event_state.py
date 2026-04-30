"""
ARMS Data Feeds: Event State / Dedupe

Persists fingerprints of previously seen events so automated feed polling does
not re-trigger the same CDM/TDC workflow endlessly.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Iterable, List

from engine.cdm import NewsItem

EVENT_STATE_PATH = "achelion_arms/state/event_state.json"
MAX_KEYS = 5000


def _fingerprint(item: NewsItem) -> str:
    raw = "|".join([
        item.source,
        item.headline,
        item.timestamp,
        item.event_type,
        ",".join(sorted(item.entities))
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _load_state() -> dict:
    if not os.path.exists(EVENT_STATE_PATH):
        return {"seen": {}, "updated_at": None}
    try:
        with open(EVENT_STATE_PATH, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if isinstance(payload, dict) and isinstance(payload.get('seen', {}), dict):
            return payload
    except Exception:
        pass
    return {"seen": {}, "updated_at": None}


def _save_state(state: dict):
    os.makedirs(os.path.dirname(EVENT_STATE_PATH), exist_ok=True)
    seen = state.get('seen', {})
    if len(seen) > MAX_KEYS:
        keys = list(seen.keys())[-MAX_KEYS:]
        state['seen'] = {k: seen[k] for k in keys}
    state['updated_at'] = datetime.now(timezone.utc).isoformat()
    with open(EVENT_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2)


def dedupe_news_items(items: Iterable[NewsItem]) -> List[NewsItem]:
    state = _load_state()
    seen = state.get('seen', {})
    fresh: List[NewsItem] = []
    changed = False

    for item in items:
        fp = _fingerprint(item)
        if fp in seen:
            continue
        fresh.append(item)
        seen[fp] = item.timestamp
        changed = True

    if changed:
        state['seen'] = seen
        _save_state(state)

    return fresh
