"""
ARMS Data Feeds: Event Bridge

Temporary but truthful bridge for CDM/TDC event ingestion.
Reads a local JSON file specified by ARMS_EVENT_JSON and converts entries
into NewsItem records for the live cycle.

This is preferable to mock alerts because it allows real manually-curated or
external-exported events to enter the system immediately.
"""

import json
import os
from typing import List

from engine.cdm import NewsItem
from engine.bridge_paths import bridge_path


def load_event_bridge() -> List[NewsItem]:
    path = bridge_path('ARMS_EVENT_JSON', 'event_bridge.json')

    if not os.path.exists(path):
        return []

    try:
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        print(f"[EventBridge] Failed to read event JSON: {e}")
        return []

    if not isinstance(payload, list):
        print("[EventBridge] Event JSON must be a list of event objects.")
        return []

    items: List[NewsItem] = []
    for row in payload:
        try:
            items.append(NewsItem(
                source=str(row['source']),
                headline=str(row['headline']),
                content=str(row.get('content', '')),
                timestamp=str(row['timestamp']),
                entities=list(row.get('entities', [])),
                event_type=str(row['event_type'])
            ))
        except Exception as e:
            print(f"[EventBridge] Skipping invalid row: {e}")

    print(f"[EventBridge] Loaded {len(items)} event(s) from bridge file.")
    return items
