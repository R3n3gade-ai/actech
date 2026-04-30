"""
ARMS Module: Thesis Dependency Checker (TDC)

This module provides the continuous thesis integrity auditing layer for ARMS.
It consumes alerts from the Customer Dependency Map (CDM) and uses the 
Claude API to evaluate if the original thesis for a position remains intact
after a new development.

"The system catches what the PM should not have to catch."

Reference: ARMS Module Specification — CDM + TDC | Addendum 2 to FSD v1.1
"""

import datetime
import json
from dataclasses import dataclass, field
from typing import List, Literal, Optional

# --- Internal Imports ---
from engine.cdm import CdmAlert, NewsItem
from intelligence.llm_wrapper import llm_wrapper
from execution.confirmation_queue import ConfirmationQueue, QueuedAction
from reporting.audit_log import SessionLogEntry, append_to_log
from engine.tdc_state import upsert_tdc_result
from engine.sentinel_workflow import sentinel_workflow
from data_feeds.sec_edgar_plugin import sec_edgar_plugin

# --- Canonical TIS Score Bands (Addendum 2 §2.5, lines 434-456) ---
# Score range -> label mapping. The LLM must return a tis_score and tis_label
# consistent with this table; downstream modules (SENTINEL, TRP) gate on the
# label, not the raw score.
TIS_BANDS = (
    (8.0, 10.0, 'INTACT'),    # No action. Log result.
    (6.0,  7.9, 'WATCH'),     # Position flag upgraded to WATCH.
    (4.0,  5.9, 'IMPAIRED'),  # SENTINEL adversarial review queued.
    (0.0,  3.9, 'BROKEN'),    # TRP candidate — thesis no longer valid.
)


def classify_tis(score: float) -> str:
    """Return the canonical TIS label for a numeric score per Addendum 2 §2.5."""
    for low, high, label in TIS_BANDS:
        if low <= score <= high:
            return label
    # Defensive: scores outside [0, 10] clamp to the nearest band edge.
    return 'BROKEN' if score < 0.0 else 'INTACT'


# --- Data Structures ---

@dataclass
class ThesisReviewResult:
    """
    Represents the output of a single TDC review.
    Based on ARMS Module Specification — Section 2.5.
    """
    position: str
    tis_score: float # 0.0 - 10.0
    tis_label: Literal['INTACT', 'WATCH', 'IMPAIRED', 'BROKEN']
    gates_affected: List[int] # SENTINEL gate numbers
    bear_case_evidence: Optional[str] = None
    bull_case_rebuttal: Optional[str] = None
    recommended_action: Literal['MONITOR', 'WATCH_FLAG', 'PM_REVIEW', 'URGENT_REVIEW'] = 'MONITOR'
    trigger_type: str = 'CDM_PROPAGATION' # 'CDM_PROPAGATION' | 'WEEKLY_AUDIT'
    trigger_entity: str = '' # named entity that triggered review
    reviewed_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    api_cost_usd: float = 0.40 # logged for monthly cost tracking (average)

@dataclass
class TDCStatus:
    """
    Represents the current global state of the TDC module.
    Based on ARMS Module Specification — Section 2.5.
    """
    last_weekly_audit: str # ISO date
    positions_at_watch: List[str] # tickers currently TIS WATCH
    positions_impaired: List[str] # tickers currently TIS IMPAIRED
    positions_broken: List[str] # tickers currently TIS BROKEN
    pending_pm_reviews: int # Tier 1 actions awaiting PM response
    last_cdm_propagation: str # ISO timestamp of last CDM alert

# --- TDC Engine Logic ---

def _load_live_sentinel_context(position: str) -> dict:
    rec = sentinel_workflow._records.get(position.upper())
    if not rec:
        return {
            'ticker': position.upper(),
            'status': 'MISSING',
            'gate1_pass': None,
            'gate1_rationale': None,
            'gate2_pass': None,
            'gate2_rationale': None,
            'gate3_raw_score': None,
            'gate3_rationale': None,
            'gate4_fem_impact': None,
            'gate5_regime_at_entry': None,
            'gate6_source_category': None,
            'mics_score': None,
            'mics_c_level': None,
        }
    return {
        'ticker': rec.ticker,
        'status': rec.status,
        'gate1_pass': rec.gate1_pass,
        'gate1_rationale': rec.gate1_rationale,
        'gate2_pass': rec.gate2_pass,
        'gate2_rationale': rec.gate2_rationale,
        'gate3_raw_score': rec.gate3_raw_score,
        'gate3_rationale': rec.gate3_rationale,
        'gate4_fem_impact': rec.gate4_fem_impact,
        'gate5_regime_at_entry': rec.gate5_regime_at_entry,
        'gate6_source_category': rec.gate6_source_category,
        'mics_score': rec.mics_score,
        'mics_c_level': rec.mics_c_level,
    }


def run_thesis_review(alert: CdmAlert) -> ThesisReviewResult:
    """
    Runs a single thesis integrity review triggered by a CDM alert.
    Uses the live stored SENTINEL thesis record rather than placeholder gate context.
    """
    position = alert.ticker
    sentinel_context = _load_live_sentinel_context(position)
    
    # 2. Construct the prompt using live SENTINEL thesis context
    prompt = f"""
    You are running an ARMS Thesis Dependency Checker review for position {position}.

    ORIGINAL STORED SENTINEL THESIS RECORD:
    {json.dumps(sentinel_context, indent=2)}

    CURRENT DEVELOPMENT:
    Event Type: {alert.event_type}
    Severity: {alert.severity}
    Headline: {alert.headline}
    Triggered by entity: {alert.triggering_entity}
    Source: {alert.source_item.source if alert.source_item else 'UNKNOWN'}
    Content: {alert.source_item.content if alert.source_item else ''}

    Your job:
    1. evaluate whether the original thesis remains INTACT, WATCH, IMPAIRED, or BROKEN
    2. identify which SENTINEL gates are weakened by this new development
    3. recommend the minimum governance response required

    Scoring guidance:
    - INTACT: thesis still stands with no meaningful degradation
    - WATCH: thesis still stands but material uncertainty increased
    - IMPAIRED: thesis weakened enough that queue progression / size-up should not proceed without review
    - BROKEN: original thesis is no longer valid enough to remain an active candidate without retirement/removal

    Return ONLY valid JSON with this exact structure:
    {{
      "tis_score": <float 0.0-10.0>,
      "tis_label": <"INTACT"|"WATCH"|"IMPAIRED"|"BROKEN">,
      "gates_affected": [<list of gate numbers with weakened assumptions>],
      "bear_case_evidence": <specific evidence string or null>,
      "bull_case_rebuttal": <why thesis may still hold despite evidence>,
      "recommended_action": <"MONITOR"|"WATCH_FLAG"|"PM_REVIEW"|"URGENT_REVIEW">
    }}
    """

    # 3. Call the Universal LLM Wrapper
    # Note: llm_wrapper is a singleton instance that can be configured for Anthropic, Google, or OpenAI
    # FAIL-SAFE: any LLM/parse failure escalates to a conservative PM_REVIEW result rather than
    # crashing the governance pipeline. Spec: Addendum 2 §2.3 requires robust thesis review.
    try:
        response_json = llm_wrapper.call(
            task_type='thesis_review',
            prompt=prompt,
            knowledge_base_query=f'{position} thesis SENTINEL gates'
        )

        # 4. Parse the response and create the result object
        if response_json.startswith("```json"):
            response_json = response_json[7:-3].strip()
        elif response_json.startswith("```"):
            response_json = response_json[3:-3].strip()

        response_data = json.loads(response_json)

        result = ThesisReviewResult(
            position=position,
            trigger_entity=alert.triggering_entity,
            trigger_type='CDM_PROPAGATION',
            **response_data
        )
    except (RuntimeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        # Conservative fail-safe: escalate to PM_REVIEW with WATCH label.
        # Never silently approve; never crash the pipeline.
        print(f"[TDC] LLM/parse failure for {position}: {type(exc).__name__}: {exc}. Escalating to PM_REVIEW.")
        append_to_log(SessionLogEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            action_type='TDC_LLM_FAILURE',
            triggering_module='TDC',
            triggering_signal=f"LLM/parse failure for {position}: {type(exc).__name__}",
            ticker=position,
        ))
        result = ThesisReviewResult(
            position=position,
            tis_score=5.0,
            tis_label='WATCH',
            gates_affected=[],
            bear_case_evidence=f'TDC review unavailable ({type(exc).__name__}); manual review required.',
            bull_case_rebuttal=None,
            recommended_action='PM_REVIEW',
            trigger_entity=alert.triggering_entity,
            trigger_type='CDM_PROPAGATION',
        )
    
    # 5. Log the outcome to the audit trail
    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='TDC_REVIEW',
        triggering_module='TDC',
        triggering_signal=f"TIS: {result.tis_score} ({result.tis_label}) for {position}. Reason: {alert.event_type} at {alert.triggering_entity}.",
        ticker=position,
        gate3_score=sentinel_context.get('gate3_raw_score'),
        source_category=sentinel_context.get('gate6_source_category')
    ))
    
    upsert_tdc_result(
        ticker=position,
        tis_score=result.tis_score,
        tis_label=result.tis_label,
        recommended_action=result.recommended_action,
        trigger_entity=result.trigger_entity,
        trigger_type=result.trigger_type,
        reviewed_at=result.reviewed_at,
        bear_case_evidence=result.bear_case_evidence,
        bull_case_rebuttal=result.bull_case_rebuttal,
        last_event_type=alert.event_type,
    )

    print(f"[TDC] Review Complete for {position}. TIS: {result.tis_score} ({result.tis_label})")
    
    # 6. Queue Tier 1 Actions if required (Section 2.2)
    if result.recommended_action in ['PM_REVIEW', 'URGENT_REVIEW']:
        # This would be where we create a QueuedAction and add it to the ConfirmationQueue.
        # For simplicity, we just print the action for now.
        print(f"[TDC] Queuing Tier 1 PM Review for {position} due to TIS {result.tis_score}.")

    return result

def _build_weekly_audit_alert(ticker: str) -> CdmAlert:
    sec_context = sec_edgar_plugin.fetch_docs(ticker, max_filings=2, max_chars_per_doc=8000)
    source_item = NewsItem(
        source='TDC_WEEKLY_AUDIT',
        headline=f'Weekly thesis audit for {ticker}',
        content=sec_context,
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        entities=[ticker],
        event_type='UNKNOWN'
    )
    return CdmAlert(
        ticker=ticker,
        triggering_entity='WEEKLY_TDC_AUDIT',
        event_type='UNKNOWN',
        severity='LOW',
        headline=f'Weekly thesis audit context compiled for {ticker}',
        source_item=source_item
    )


def run_weekly_tdc_audit(all_positions: List[str]) -> List[ThesisReviewResult]:
    """
    Runs the proactive weekly audit on all equity positions in the book.
    Gated to run only on the configured audit day (default: Sunday) to avoid
    burning LLM credits on every daily cycle.
    """
    import os
    audit_day = int(os.environ.get('TDC_WEEKLY_AUDIT_DAY', '6'))  # 0=Mon ... 6=Sun
    today_weekday = datetime.datetime.now(datetime.timezone.utc).weekday()
    if today_weekday != audit_day:
        day_names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        print(f"[TDC] Weekly audit skipped — runs on {day_names[audit_day]}s (today is {day_names[today_weekday]}).")
        return []

    print("\n--- Running Proactive Weekly TDC Audit ---")
    results: List[ThesisReviewResult] = []

    unique_positions = sorted({p.upper() for p in all_positions if p and p.isalpha()})
    for ticker in unique_positions:
        alert = _build_weekly_audit_alert(ticker)
        result = run_thesis_review(alert)
        result.trigger_type = 'WEEKLY_AUDIT'
        results.append(result)

        append_to_log(SessionLogEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            action_type='TDC_WEEKLY_AUDIT',
            triggering_module='TDC',
            triggering_signal=f"Weekly audit completed for {ticker}: {result.tis_label} ({result.tis_score})",
            ticker=ticker,
        ))

    return results

if __name__ == '__main__':
    print("ARMS TDC Module Active (Simulation Mode)")
    
    # Simulate a CDM alert for MU
    mock_alert = CdmAlert(
        ticker='MU',
        triggering_entity='Google',
        event_type='LEGAL_RULING',
        severity='CRITICAL',
        headline='DOJ Pursuing Structural Remedies Against Google',
        source_item=None # type: ignore
    )
    
    res = run_thesis_review(mock_alert)
    print(f"Outcome: {res.tis_label} (Score: {res.tis_score})")
