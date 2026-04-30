"""
ARMS Engine: SENTINEL Bridge

Truthful interim bridge for live MICS inputs.
Loads per-ticker SENTINEL gate records from a local JSON file specified by
ARMS_SENTINEL_JSON.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional

from engine.mics import SentinelGateInputs
from engine.bridge_paths import bridge_path


@dataclass
class SentinelRecord:
    ticker: str
    gate3_raw_score: float
    source_category: str
    fem_impact: str
    regime_at_entry: str


def load_sentinel_records() -> Dict[str, SentinelRecord]:
    path = bridge_path('ARMS_SENTINEL_JSON', 'sentinel_records.json')
    if not os.path.exists(path):
        print(f"[SentinelBridge] SENTINEL bridge file not found: {path}")
        return {}

    try:
        with open(path, 'r', encoding='utf-8') as f:
            payload = json.load(f)
    except Exception as e:
        print(f"[SentinelBridge] Failed to read SENTINEL JSON: {e}")
        return {}

    if not isinstance(payload, list):
        print("[SentinelBridge] SENTINEL JSON must be a list of records.")
        return {}

    records: Dict[str, SentinelRecord] = {}
    for row in payload:
        try:
            rec = SentinelRecord(
                ticker=str(row['ticker']).upper(),
                gate3_raw_score=float(row['gate3_raw_score']),
                source_category=str(row['source_category']),
                fem_impact=str(row['fem_impact']),
                regime_at_entry=str(row['regime_at_entry'])
            )
            records[rec.ticker] = rec
        except Exception as e:
            print(f"[SentinelBridge] Skipping invalid SENTINEL row: {e}")
    return records


from engine.sentinel_workflow import sentinel_workflow

def _apply_gate3_supplementary(ticker: str, base_gate3: float) -> float:
    """
    Apply Phase 2 anticipatory signal adjustment to the Gate 3 score.
    The supplementary adjustment range is [-5, +5], clamped to [0, 30].
    """
    try:
        from intelligence.gate3_supplementary import calculate_gate3_supplementary
        result = calculate_gate3_supplementary(ticker)
        if result.signal_count > 0:
            adjusted = max(0.0, min(30.0, base_gate3 + result.adjustment))
            return adjusted
    except Exception:
        pass  # Phase 2 signals are supplementary — never block MICS
    return base_gate3

def build_mics_input_for_ticker(ticker: str, current_regime: str, fallback_fem_impact: str = 'NORMAL->NORMAL') -> SentinelGateInputs:
    """
    Truthful fallback that reads from the new durable SENTINEL v2.0 workflow state.
    If no active thesis exists, it provides the required structured fallback, 
    low-conviction inputs so the system can still operate without crashing.
    """
    active_thesis = sentinel_workflow.get_active_thesis(ticker.upper())
    
    if active_thesis:
        adjusted_gate3 = _apply_gate3_supplementary(ticker.upper(), active_thesis.gate3_raw_score)
        return SentinelGateInputs(
            gate3_raw_score=adjusted_gate3,
            source_category=active_thesis.gate6_source_category,  # type: ignore[arg-type]
            fem_impact=active_thesis.gate4_fem_impact,
            regime_at_entry=active_thesis.gate5_regime_at_entry
        )

    # Truthful fallback: still explicit and deterministic, but lower-conviction than a true SENTINEL record.
    adjusted_fallback = _apply_gate3_supplementary(ticker.upper(), 14.0)
    return SentinelGateInputs(
        gate3_raw_score=adjusted_fallback,
        source_category='None',
        fem_impact=fallback_fem_impact,
        regime_at_entry=current_regime if current_regime in {'RISK_ON', 'WATCH', 'NEUTRAL'} else 'NEUTRAL'
    )