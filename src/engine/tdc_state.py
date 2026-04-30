from __future__ import annotations

import datetime
import json
import os
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional

from engine.bridge_paths import bridge_path


@dataclass
class TdcTickerState:
    ticker: str
    tis_score: float
    tis_label: str
    recommended_action: str
    trigger_entity: str
    trigger_type: str
    reviewed_at: str
    bear_case_evidence: Optional[str] = None
    bull_case_rebuttal: Optional[str] = None
    last_event_type: Optional[str] = None


TDC_STATE_PATH = bridge_path('ARMS_TDC_STATE_JSON', 'tdc_state.json')


def _default_state() -> dict:
    return {
        'updated_at': None,
        'tickers': {},
    }


def load_tdc_state() -> Dict[str, TdcTickerState]:
    if not os.path.exists(TDC_STATE_PATH):
        return {}
    try:
        with open(TDC_STATE_PATH, 'r', encoding='utf-8') as f:
            payload = json.load(f)
        tickers = payload.get('tickers', {})
        out: Dict[str, TdcTickerState] = {}
        for ticker, raw in tickers.items():
            out[ticker.upper()] = TdcTickerState(**raw)
        return out
    except Exception as e:
        print(f"[TDCState] Failed to load state: {e}")
        return {}


def save_tdc_state(state: Dict[str, TdcTickerState]):
    payload = _default_state()
    payload['updated_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    payload['tickers'] = {ticker.upper(): asdict(rec) for ticker, rec in state.items()}
    os.makedirs(os.path.dirname(TDC_STATE_PATH), exist_ok=True)
    with open(TDC_STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(payload, f, indent=2)


def upsert_tdc_result(
    ticker: str,
    tis_score: float,
    tis_label: str,
    recommended_action: str,
    trigger_entity: str,
    trigger_type: str,
    reviewed_at: str,
    bear_case_evidence: Optional[str] = None,
    bull_case_rebuttal: Optional[str] = None,
    last_event_type: Optional[str] = None,
):
    current = load_tdc_state()
    current[ticker.upper()] = TdcTickerState(
        ticker=ticker.upper(),
        tis_score=tis_score,
        tis_label=tis_label,
        recommended_action=recommended_action,
        trigger_entity=trigger_entity,
        trigger_type=trigger_type,
        reviewed_at=reviewed_at,
        bear_case_evidence=bear_case_evidence,
        bull_case_rebuttal=bull_case_rebuttal,
        last_event_type=last_event_type,
    )
    save_tdc_state(current)


def summarize_tdc_status() -> dict:
    current = load_tdc_state()
    watch = sorted([t for t, s in current.items() if s.tis_label == 'WATCH'])
    impaired = sorted([t for t, s in current.items() if s.tis_label == 'IMPAIRED'])
    broken = sorted([t for t, s in current.items() if s.tis_label == 'BROKEN'])
    pm_reviews = sum(1 for s in current.values() if s.recommended_action in {'PM_REVIEW', 'URGENT_REVIEW'})
    return {
        'positions_at_watch': watch,
        'positions_impaired': impaired,
        'positions_broken': broken,
        'pending_pm_reviews': pm_reviews,
    }
