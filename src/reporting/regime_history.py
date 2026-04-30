from __future__ import annotations

import datetime
import json
import os
from dataclasses import dataclass, asdict
from typing import List, Optional

from engine.bridge_paths import bridge_path


REGIME_HISTORY_PATH = bridge_path('ARMS_REGIME_HISTORY_JSON', 'regime_history.json')


@dataclass
class RegimeHistoryEntry:
    timestamp: str
    score: float
    regime: str
    equity_ceiling_pct: float
    queue_status: str
    catalyst: str
    note: str = ''


def load_regime_history() -> List[RegimeHistoryEntry]:
    if not os.path.exists(REGIME_HISTORY_PATH):
        return []
    try:
        with open(REGIME_HISTORY_PATH, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        if not isinstance(payload, list):
            return []
        out = []
        for row in payload:
            out.append(RegimeHistoryEntry(**row))
        return out
    except Exception as e:
        print(f"[RegimeHistory] Failed to load history: {e}")
        return []


def save_regime_history(entries: List[RegimeHistoryEntry]):
    os.makedirs(os.path.dirname(REGIME_HISTORY_PATH), exist_ok=True)
    with open(REGIME_HISTORY_PATH, 'w', encoding='utf-8') as f:
        json.dump([asdict(e) for e in entries], f, indent=2)


def append_regime_history(entry: RegimeHistoryEntry, keep_last: int = 50) -> List[RegimeHistoryEntry]:
    entries = load_regime_history()
    entries.append(entry)
    entries = entries[-keep_last:]
    save_regime_history(entries)
    return entries


def prior_score(entries: List[RegimeHistoryEntry]) -> Optional[float]:
    if len(entries) < 2:
        return None
    return entries[-2].score
