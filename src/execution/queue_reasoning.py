from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional

from engine.cdm import CdmAlert
from engine.tdc_state import TdcTickerState

logger = logging.getLogger(__name__)


@dataclass
class ThesisSignal:
    ticker: str
    thesis_status: str
    gate3_score: Optional[float]
    source_category: str


@dataclass
class QueueReasoningSignal:
    ticker: str
    reason: str
    action: str
    confidence: str
    note: str


KNOWN_RISK_ON_ONLY = {'NVDA'}


def _is_sentinel_unconfirmed(ticker: str, thesis: Optional[ThesisSignal]) -> bool:
    """
    Check if a ticker is SENTINEL-unconfirmed using live thesis data
    instead of a hardcoded set. A ticker is unconfirmed if:
    - No SENTINEL record exists at all
    - SENTINEL record exists but status is REJECTED or DRAFT with no gate passage
    """
    if thesis is None:
        return True
    if thesis.thesis_status in {'REJECTED', 'MISSING', 'RETIRED'}:
        return True
    if thesis.thesis_status == 'DRAFT' and thesis.gate3_score is None:
        return True
    return False


# Consensus-priced signals — loaded from PM-editable data file.
# Live TIS heuristic supplements explicit overrides.
from engine.bridge_paths import bridge_path as _bridge_path
CONSENSUS_OVERRIDES_PATH = _bridge_path('ARMS_CONSENSUS_JSON', 'consensus_overrides.json')


def _load_consensus_overrides() -> dict:
    """Load PM-editable consensus/preconsensus overrides from JSON data file."""
    if not os.path.exists(CONSENSUS_OVERRIDES_PATH):
        logger.warning("consensus_overrides.json not found; using empty overrides")
        return {}
    try:
        with open(CONSENSUS_OVERRIDES_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.error("Failed to load consensus_overrides.json: %s", e)
        return {}


def _detect_tis_consensus(ticker: str, thesis: Optional[ThesisSignal],
                          tdc_state: Optional[TdcTickerState]) -> Optional[QueueReasoningSignal]:
    """
    Live TIS-derived consensus detection:
    - Low gate3_score (10-14) with INTACT TIS = thesis valid but asymmetry eroding
    - tis_score < 0.4 with non-BROKEN label = valuation compression / consensus convergence
    """
    if tdc_state and tdc_state.tis_label == 'INTACT' and tdc_state.tis_score < 0.4:
        return QueueReasoningSignal(
            ticker=ticker,
            reason='CONSENSUS_CONVERGING',
            action='MONITOR',
            confidence='MEDIUM',
            note=f'TIS score {tdc_state.tis_score:.2f} (INTACT) suggests thesis valid but asymmetry eroding.'
        )
    if thesis and thesis.gate3_score is not None and 10 <= thesis.gate3_score < 14:
        return QueueReasoningSignal(
            ticker=ticker,
            reason='CONSENSUS_CONVERGING',
            action='MONITOR',
            confidence='LOW',
            note=f'Gate 3 score {thesis.gate3_score} approaching consensus threshold (14); asymmetry weakening.'
        )
    return None


def build_thesis_signal_map(sentinel_records: Dict[str, object]) -> Dict[str, ThesisSignal]:
    out: Dict[str, ThesisSignal] = {}
    for ticker, rec in sentinel_records.items():
        out[ticker.upper()] = ThesisSignal(
            ticker=ticker.upper(),
            thesis_status=getattr(rec, 'status', 'MISSING'),
            gate3_score=getattr(rec, 'gate3_raw_score', None),
            source_category=getattr(rec, 'gate6_source_category', 'None') or 'None',
        )
    return out


def _summarize_alerts_by_ticker(cdm_alerts: List[CdmAlert]) -> Dict[str, List[CdmAlert]]:
    out: Dict[str, List[CdmAlert]] = {}
    for alert in cdm_alerts:
        out.setdefault(alert.ticker.upper(), []).append(alert)
    return out


def _infer_consensus_vs_asymmetry(ticker: str, thesis: Optional[ThesisSignal],
                                  tdc_state: Optional[TdcTickerState] = None) -> Optional[QueueReasoningSignal]:
    t = ticker.upper()
    overrides = _load_consensus_overrides()
    consensus_hints = overrides.get('consensus_priced', {})
    preconsensus_hints = overrides.get('preconsensus', {})
    action_overrides = overrides.get('consensus_action_overrides', {})

    if t in consensus_hints:
        action = action_overrides.get(t, 'REMOVE')
        return QueueReasoningSignal(
            ticker=t,
            reason='CONSENSUS_PRICED',
            action=action,
            confidence='MEDIUM',
            note=consensus_hints[t]
        )

    if t in preconsensus_hints:
        return QueueReasoningSignal(
            ticker=t,
            reason='ASYMMETRY_RETAINED',
            action='KEEP',
            confidence='MEDIUM',
            note=preconsensus_hints[t]
        )

    # Live TIS-derived consensus convergence detection
    tis_signal = _detect_tis_consensus(t, thesis, tdc_state)
    if tis_signal:
        return tis_signal

    if thesis and thesis.gate3_score is not None:
        if thesis.gate3_score < 14:
            return QueueReasoningSignal(
                ticker=t,
                reason='SENTINEL_UNCONFIRMED',
                action='REMOVE',
                confidence='MEDIUM',
                note=f'Gate 3 score {thesis.gate3_score} is below Cat A/B threshold 14.'
            )
        return QueueReasoningSignal(
            ticker=t,
            reason='ASYMMETRY_RETAINED',
            action='KEEP',
            confidence='LOW',
            note=f'Gate 3 score {thesis.gate3_score} remains above minimum threshold; asymmetry retained until contradicted by richer valuation logic.'
        )

    return None


def derive_queue_reasoning_signals(
    queue_tickers: List[str],
    thesis_map: Dict[str, ThesisSignal],
    cdm_alerts: List[CdmAlert],
    tdc_state_by_ticker: Optional[Dict[str, TdcTickerState]] = None,
) -> Dict[str, QueueReasoningSignal]:
    """
    Transitional queue-reasoning layer.

    Goal: move queue reasons away from hardcoded report assumptions and toward
    structured thesis/event-derived signals.

    This is still interim. It uses:
    - live thesis status if present
    - propagated CDM alerts if present
    - explicit known queue policies already surfaced by the user's canonical comparison
    """
    alerts_by_ticker = _summarize_alerts_by_ticker(cdm_alerts)
    tdc_state_by_ticker = tdc_state_by_ticker or {}
    out: Dict[str, QueueReasoningSignal] = {}

    for ticker in queue_tickers:
        t = ticker.upper()
        thesis = thesis_map.get(t)
        ticker_alerts = alerts_by_ticker.get(t, [])
        tdc_state = tdc_state_by_ticker.get(t)

        # Live SENTINEL-unconfirmed check (replaces hardcoded set)
        if _is_sentinel_unconfirmed(t, thesis):
            out[t] = QueueReasoningSignal(
                ticker=t,
                reason='SENTINEL_UNCONFIRMED',
                action='REMOVE',
                confidence='HIGH',
                note=f'Ticker has no confirmed SENTINEL passage (status={thesis.thesis_status if thesis else "MISSING"}).'
            )
            continue

        if t in KNOWN_RISK_ON_ONLY:
            out[t] = QueueReasoningSignal(
                ticker=t,
                reason='RISK_ON_EVAL_ONLY',
                action='EVAL_ONLY',
                confidence='HIGH',
                note='Ticker is treated as eval-only in current governance posture.'
            )
            continue

        if tdc_state:
            if tdc_state.tis_label == 'BROKEN':
                out[t] = QueueReasoningSignal(
                    ticker=t,
                    reason='THESIS_MONITOR_ONLY',
                    action='REMOVE',
                    confidence='HIGH',
                    note=f"TDC state is BROKEN due to {tdc_state.trigger_entity}; recommended_action={tdc_state.recommended_action}."
                )
                continue
            if tdc_state.tis_label in {'WATCH', 'IMPAIRED'}:
                out[t] = QueueReasoningSignal(
                    ticker=t,
                    reason='THESIS_MONITOR_ONLY',
                    action='MONITOR',
                    confidence='HIGH',
                    note=f"TDC state is {tdc_state.tis_label} due to {tdc_state.trigger_entity}; recommended_action={tdc_state.recommended_action}."
                )
                continue

        if ticker_alerts:
            strongest = sorted(ticker_alerts, key=lambda a: ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'].index(a.severity))[-1]
            out[t] = QueueReasoningSignal(
                ticker=t,
                reason='THESIS_MONITOR_ONLY',
                action='MONITOR',
                confidence='MEDIUM',
                note=f'Active CDM pressure from {strongest.triggering_entity} ({strongest.event_type}, {strongest.severity}).'
            )
            continue

        asymmetry_signal = _infer_consensus_vs_asymmetry(t, thesis, tdc_state)
        if asymmetry_signal:
            out[t] = asymmetry_signal
            continue

        out[t] = QueueReasoningSignal(
            ticker=t,
            reason='REGIME_NOT_CLEARED',
            action='KEEP',
            confidence='LOW',
            note='No definitive thesis/event removal signal yet; keep transitional queue posture.'
        )

    return out
