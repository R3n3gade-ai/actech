"""
ARMS Engine: MC-RSS Bridge

Truthful interim bridge for retail sentiment inputs.
Reads a local JSON file specified by ARMS_RSS_JSON.
"""

import json
import os
from dataclasses import dataclass
from typing import List

from engine.bridge_paths import bridge_path


@dataclass
class RSSInputs:
    retail_net_buying_usd: float
    retail_history_30d: List[float]
    naaim_exposure_index: float
    aaii_bull_bear_spread: float


DEFAULT_RSS_INPUTS = RSSInputs(
    retail_net_buying_usd=0.0,
    retail_history_30d=[0.0, 0.0, 0.0],
    naaim_exposure_index=50.0,
    aaii_bull_bear_spread=0.0,
)


def load_rss_inputs() -> RSSInputs:
    path = bridge_path('ARMS_RSS_JSON', 'rss_inputs.json')
    if not os.path.exists(path):
        print(f'[RSSBridge] RSS bridge file not found: {path}; using neutral fallback inputs.')
        return DEFAULT_RSS_INPUTS

    try:
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        return RSSInputs(
            retail_net_buying_usd=float(payload.get('retail_net_buying_usd', 0.0)),
            retail_history_30d=[float(x) for x in payload.get('retail_history_30d', [0.0, 0.0, 0.0])],
            naaim_exposure_index=float(payload.get('naaim_exposure_index', 50.0)),
            aaii_bull_bear_spread=float(payload.get('aaii_bull_bear_spread', 0.0)),
        )
    except Exception as e:
        print(f'[RSSBridge] Failed to load RSS JSON: {e}. Using neutral fallback inputs.')
        return DEFAULT_RSS_INPUTS
