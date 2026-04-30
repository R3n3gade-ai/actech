"""
ARMS Engine: CDF Bridge

Truthful interim bridge for conviction decay inputs.
Reads per-ticker underperformance state from ARMS_CDF_JSON.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict

from engine.bridge_paths import bridge_path


@dataclass
class CDFInputRecord:
    ticker: str
    days_underperforming: int
    underperformance_pp: float


def load_cdf_inputs() -> Dict[str, CDFInputRecord]:
    path = bridge_path('ARMS_CDF_JSON', 'cdf_inputs.json')
    if not os.path.exists(path):
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        print(f'[CDFBridge] Failed to read CDF JSON: {e}')
        return {}

    if not isinstance(payload, list):
        print('[CDFBridge] CDF JSON must be a list of records.')
        return {}

    out: Dict[str, CDFInputRecord] = {}
    for row in payload:
        try:
            rec = CDFInputRecord(
                ticker=str(row['ticker']).upper(),
                days_underperforming=int(row['days_underperforming']),
                underperformance_pp=float(row['underperformance_pp'])
            )
            out[rec.ticker] = rec
        except Exception as e:
            print(f'[CDFBridge] Skipping invalid CDF row: {e}')
    return out
