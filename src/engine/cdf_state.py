"""
ARMS Engine: CDF State Persistence

Tracks consecutive underperformance duration by ticker so CDF can evolve from
snapshot logic toward true persistent autonomy.
"""

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict

STATE_PATH = 'achelion_arms/state/cdf_state.json'


@dataclass
class CDFPersistentState:
    ticker: str
    underperforming_days: int
    last_underperformance_pp: float
    updated_at: str


def load_cdf_state() -> Dict[str, CDFPersistentState]:
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception:
        return {}

    out: Dict[str, CDFPersistentState] = {}
    if not isinstance(payload, dict):
        return out
    for ticker, row in payload.items():
        try:
            out[ticker] = CDFPersistentState(
                ticker=ticker,
                underperforming_days=int(row['underperforming_days']),
                last_underperformance_pp=float(row['last_underperformance_pp']),
                updated_at=str(row['updated_at'])
            )
        except Exception:
            continue
    return out


def save_cdf_state(state: Dict[str, CDFPersistentState]):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    payload = {
        ticker: {
            'underperforming_days': rec.underperforming_days,
            'last_underperformance_pp': rec.last_underperformance_pp,
            'updated_at': rec.updated_at,
        }
        for ticker, rec in state.items()
    }
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)


def update_cdf_state(ticker: str, underperformance_pp: float, threshold_pp: float = 10.0) -> CDFPersistentState:
    state = load_cdf_state()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today_date = now.date()
    existing = state.get(ticker)

    if underperformance_pp >= threshold_pp:
        if existing:
            # Only increment day counter on a new calendar day
            try:
                last_date = datetime.fromisoformat(existing.updated_at).date()
            except (ValueError, TypeError):
                last_date = None
            if last_date == today_date:
                # Same day — keep existing count, just update timestamp
                new_days = existing.underperforming_days
            else:
                new_days = existing.underperforming_days + 1
        else:
            new_days = 1
    else:
        new_days = 0

    rec = CDFPersistentState(
        ticker=ticker,
        underperforming_days=new_days,
        last_underperformance_pp=underperformance_pp,
        updated_at=now_iso,
    )
    state[ticker] = rec
    save_cdf_state(state)
    return rec
