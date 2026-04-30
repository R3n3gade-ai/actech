"""
ARMS Intelligence Layer: Earnings Language Velocity Tracker (ELVT)

Layer 2 anticipatory intelligence — tracks how language changes across
consecutive earnings calls for every named entity in the CDM map.
Not what they say — how they say it, and whether the direction is changing.

Three language categories scored on every transcript:
  1. Capex commitment — conditional vs definitive investment language
  2. Demand visibility — backlog growth/customer urgency vs normalization
  3. Competitive posture — pricing power/design wins vs competitive pressure

The ELVT score is the velocity of change across consecutive transcripts.
A company shifting from conditional to definitive capex language across
three consecutive quarters is a 2-3 quarter leading indicator.

Data source: SEC EDGAR full-text search — 8-K filings (free API)
Cadence: Quarterly after each earnings season + on-demand when CDM fires
Cost: ~$0.30 per transcript via Claude API

Reference: Addendum 3, Section 2.1 — ELVT Specification
"""

import datetime
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Literal

from intelligence.llm_wrapper import llm_wrapper
from data_feeds.sec_edgar_plugin import sec_edgar_plugin
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class LanguageCategoryScore:
    """Score for a single language category on a single transcript."""
    category: Literal['capex_commitment', 'demand_visibility', 'competitive_posture']
    score: float  # -1.0 (strongly bearish) to +1.0 (strongly bullish)
    evidence: str  # Key phrases that drove the score
    confidence: float  # 0.0-1.0 — how clear the language signal is


@dataclass
class TranscriptAnalysis:
    """Complete ELVT analysis of a single earnings transcript."""
    entity: str
    ticker: str  # The filing entity's ticker
    filing_date: str
    period: str  # e.g. "Q1 2026"
    capex_commitment: LanguageCategoryScore
    demand_visibility: LanguageCategoryScore
    competitive_posture: LanguageCategoryScore
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


@dataclass
class ELVTSignal:
    """Velocity signal derived from comparing consecutive transcript analyses."""
    entity: str
    category: str
    velocity: float  # Change rate across consecutive transcripts
    direction: Literal['ACCELERATING', 'IMPROVING', 'STABLE', 'SOFTENING', 'DETERIORATING']
    severity: Literal['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
    affected_positions: List[str]
    current_score: float
    prior_score: float
    quarters_analyzed: int
    explanation: str


@dataclass
class ELVTResult:
    """Complete ELVT run output for a single entity."""
    entity: str
    signals: List[ELVTSignal]
    transcript_count: int
    analysis_timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


# --- Entity-to-Position Mapping (from Addendum 3, Section 2.1) ---
# Maps CDM named entities to the positions whose thesis they affect

ENTITY_POSITION_MAP: Dict[str, List[str]] = {
    'Microsoft': ['MU', 'NVDA', 'ALAB', 'ANET', 'MRVL'],
    'Google': ['MU', 'NVDA', 'ALAB', 'AVGO', 'ANET', 'MRVL'],
    'Alphabet': ['MU', 'NVDA', 'ALAB', 'AVGO', 'ANET', 'MRVL'],
    'Amazon': ['MU', 'NVDA', 'ALAB', 'ANET', 'MRVL'],
    'Meta': ['MU', 'NVDA', 'ALAB', 'AVGO', 'ANET'],
    'Oracle': ['NVDA'],
    'Apple': ['AVGO', 'ARM'],
    'Samsung': ['MU'],
    'SK Hynix': ['MU'],
}

# Tickers for the CDM entities that file with SEC (maps entity name → SEC ticker)
ENTITY_SEC_TICKERS: Dict[str, str] = {
    'Microsoft': 'MSFT',
    'Google': 'GOOG',
    'Alphabet': 'GOOG',
    'Amazon': 'AMZN',
    'Meta': 'META',
    'Oracle': 'ORCL',
    'Apple': 'AAPL',
}

# --- State Persistence ---

_STATE_DIR = os.path.join('achelion_arms', 'state', 'elvt')


def _ensure_state_dir():
    os.makedirs(_STATE_DIR, exist_ok=True)


def _load_history(entity: str) -> List[Dict]:
    """Load prior transcript analyses for an entity."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{entity.lower().replace(" ", "_")}_history.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []


def _save_history(entity: str, history: List[Dict]):
    """Persist transcript analysis history for an entity."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{entity.lower().replace(" ", "_")}_history.json')
    with open(path, 'w') as f:
        json.dump(history, f, indent=2)


# --- Core ELVT Logic ---

def _analyze_transcript(entity: str, sec_ticker: str) -> Optional[TranscriptAnalysis]:
    """
    Fetch the most recent earnings-related filing for the entity and run
    Claude language velocity analysis on it.
    """
    # Fetch recent 8-K/10-Q filings (earnings transcripts embedded in 8-Ks)
    docs = sec_edgar_plugin.fetch_docs(sec_ticker, max_filings=1, max_chars_per_doc=15000)

    if not docs or len(docs.strip()) < 200:
        print(f"[ELVT] No substantial filing content found for {entity} ({sec_ticker})")
        return None

    prompt = f"""You are the ARMS Earnings Language Velocity Tracker (ELVT).
Analyze the following SEC filing content for {entity} ({sec_ticker}).

Score THREE language categories on a scale from -1.0 (strongly bearish) to +1.0 (strongly bullish):

1. **capex_commitment**: Is the company using definitive capex language ("we will invest", "we are building") or conditional/hedging language ("we are evaluating", "we may consider")? +1.0 = fully committed, -1.0 = retreating from prior commitments.

2. **demand_visibility**: Is the company describing backlog growth, customer urgency, lead time extensions, capacity constraints? Or inventory normalization, order push-outs, softening demand? +1.0 = accelerating demand, -1.0 = demand deterioration.

3. **competitive_posture**: Is the company describing pricing power, new design wins, sole-source positions, expanding TAM? Or pricing pressure, competitive alternatives, TAM contraction? +1.0 = strengthening competitive position, -1.0 = weakening.

For each category, provide:
- score: float -1.0 to +1.0
- evidence: key phrases from the filing that drove your score (max 100 words)
- confidence: float 0.0-1.0 — how clear and unambiguous the language signal is

Also identify the fiscal period covered (e.g., "Q1 2026").

Return ONLY valid JSON:
{{"period": "Q1 2026", "capex_commitment": {{"score": 0.0, "evidence": "...", "confidence": 0.0}}, "demand_visibility": {{"score": 0.0, "evidence": "...", "confidence": 0.0}}, "competitive_posture": {{"score": 0.0, "evidence": "...", "confidence": 0.0}}}}

FILING CONTENT:
{docs}"""

    response_text = llm_wrapper.call(
        task_type='elvt_analysis',
        prompt=prompt,
        knowledge_base_query=f'{entity} earnings language velocity'
    )

    # Strip markdown fencing if present
    if response_text.startswith("```json"):
        response_text = response_text[7:-3].strip()
    elif response_text.startswith("```"):
        response_text = response_text[3:-3].strip()

    try:
        res = json.loads(response_text)
    except json.JSONDecodeError as e:
        print(f"[ELVT] Failed to parse LLM response for {entity}: {e}")
        return None

    now_str = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d')

    return TranscriptAnalysis(
        entity=entity,
        ticker=sec_ticker,
        filing_date=now_str,
        period=res.get('period', 'UNKNOWN'),
        capex_commitment=LanguageCategoryScore(
            category='capex_commitment',
            score=float(res['capex_commitment']['score']),
            evidence=res['capex_commitment']['evidence'],
            confidence=float(res['capex_commitment']['confidence']),
        ),
        demand_visibility=LanguageCategoryScore(
            category='demand_visibility',
            score=float(res['demand_visibility']['score']),
            evidence=res['demand_visibility']['evidence'],
            confidence=float(res['demand_visibility']['confidence']),
        ),
        competitive_posture=LanguageCategoryScore(
            category='competitive_posture',
            score=float(res['competitive_posture']['score']),
            evidence=res['competitive_posture']['evidence'],
            confidence=float(res['competitive_posture']['confidence']),
        ),
    )


def _compute_velocity(current: float, prior: float) -> float:
    """Compute velocity as the change between consecutive scores."""
    return current - prior


def _classify_velocity(velocity: float) -> tuple:
    """
    Classify velocity into direction and severity.
    Returns (direction, severity).
    """
    if velocity > 0.40:
        return 'ACCELERATING', 'CRITICAL'
    elif velocity > 0.20:
        return 'IMPROVING', 'HIGH'
    elif velocity > 0.05:
        return 'IMPROVING', 'MEDIUM'
    elif velocity < -0.40:
        return 'DETERIORATING', 'CRITICAL'
    elif velocity < -0.20:
        return 'SOFTENING', 'HIGH'
    elif velocity < -0.05:
        return 'SOFTENING', 'MEDIUM'
    else:
        return 'STABLE', 'LOW'


def run_elvt_analysis(entity: str, on_demand: bool = False) -> Optional[ELVTResult]:
    """
    Run ELVT analysis for a single CDM named entity.

    Args:
        entity: The named entity to analyze (e.g., 'Microsoft', 'Google')
        on_demand: If True, runs regardless of quarterly cadence (CDM-triggered)

    Returns:
        ELVTResult with velocity signals, or None if no data available.
    """
    sec_ticker = ENTITY_SEC_TICKERS.get(entity)
    if not sec_ticker:
        print(f"[ELVT] No SEC ticker mapping for entity: {entity}")
        return None

    affected_positions = ENTITY_POSITION_MAP.get(entity, [])
    if not affected_positions:
        print(f"[ELVT] No position dependencies for entity: {entity}")
        return None

    print(f"[ELVT] Analyzing earnings language velocity for {entity} ({sec_ticker})...")

    # Fetch and analyze current transcript
    current_analysis = _analyze_transcript(entity, sec_ticker)
    if not current_analysis:
        return None

    # Load prior analyses
    history = _load_history(entity)

    # Compute velocity signals by comparing to most recent prior analysis
    signals: List[ELVTSignal] = []

    if history:
        prior = history[-1]  # Most recent prior analysis
        for category in ['capex_commitment', 'demand_visibility', 'competitive_posture']:
            current_score = getattr(current_analysis, category).score
            prior_score = prior.get(category, {}).get('score', 0.0)

            velocity = _compute_velocity(current_score, prior_score)
            direction, severity = _classify_velocity(velocity)

            signal = ELVTSignal(
                entity=entity,
                category=category,
                velocity=round(velocity, 4),
                direction=direction,
                severity=severity,
                affected_positions=affected_positions,
                current_score=round(current_score, 4),
                prior_score=round(prior_score, 4),
                quarters_analyzed=len(history) + 1,
                explanation=f"{entity} {category} shifted from {prior_score:.2f} to {current_score:.2f} "
                            f"(velocity: {velocity:+.2f}). Direction: {direction}."
            )
            signals.append(signal)

            # Log material signals
            if severity in ('CRITICAL', 'HIGH'):
                append_to_log(SessionLogEntry(
                    timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    action_type='ELVT_SIGNAL',
                    triggering_module='ELVT',
                    triggering_signal=f"{entity} {category}: {direction} (velocity {velocity:+.2f})",
                    ticker=','.join(affected_positions),
                ))
    else:
        # First analysis — establish baseline, no velocity yet
        for category in ['capex_commitment', 'demand_visibility', 'competitive_posture']:
            current_score = getattr(current_analysis, category).score
            signals.append(ELVTSignal(
                entity=entity,
                category=category,
                velocity=0.0,
                direction='STABLE',
                severity='LOW',
                affected_positions=affected_positions,
                current_score=round(current_score, 4),
                prior_score=0.0,
                quarters_analyzed=1,
                explanation=f"{entity} {category} baseline established at {current_score:.2f}. "
                            f"Velocity scoring begins next quarter."
            ))

    # Persist current analysis to history
    history_entry = {
        'filing_date': current_analysis.filing_date,
        'period': current_analysis.period,
        'capex_commitment': {
            'score': current_analysis.capex_commitment.score,
            'evidence': current_analysis.capex_commitment.evidence,
            'confidence': current_analysis.capex_commitment.confidence,
        },
        'demand_visibility': {
            'score': current_analysis.demand_visibility.score,
            'evidence': current_analysis.demand_visibility.evidence,
            'confidence': current_analysis.demand_visibility.confidence,
        },
        'competitive_posture': {
            'score': current_analysis.competitive_posture.score,
            'evidence': current_analysis.competitive_posture.evidence,
            'confidence': current_analysis.competitive_posture.confidence,
        },
    }
    history.append(history_entry)
    _save_history(entity, history)

    result = ELVTResult(
        entity=entity,
        signals=signals,
        transcript_count=len(history),
    )

    on_demand_label = " (CDM-triggered)" if on_demand else ""
    print(f"[ELVT] {entity}{on_demand_label}: {len(signals)} signals generated, "
          f"{len([s for s in signals if s.severity in ('CRITICAL', 'HIGH')])} material.")

    return result


def run_quarterly_elvt_sweep() -> List[ELVTResult]:
    """
    Run ELVT analysis for all CDM named entities with SEC filings.
    Called quarterly after earnings season.

    Returns:
        List of ELVTResult for all analyzed entities.
    """
    print("--- ARMS ELVT: Quarterly Earnings Language Velocity Sweep ---")

    results: List[ELVTResult] = []
    for entity, sec_ticker in ENTITY_SEC_TICKERS.items():
        if entity == 'Alphabet':
            continue  # Skip alias — Google/Alphabet share the same filing

        result = run_elvt_analysis(entity)
        if result:
            results.append(result)

    material_count = sum(
        1 for r in results
        for s in r.signals
        if s.severity in ('CRITICAL', 'HIGH')
    )

    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='ELVT_SWEEP_COMPLETE',
        triggering_module='ELVT',
        triggering_signal=f"Quarterly sweep complete. {len(results)} entities analyzed, {material_count} material signals.",
    ))

    print(f"--- ELVT Sweep Complete. {len(results)} entities, {material_count} material signals. ---")
    return results


def get_elvt_signals_for_position(ticker: str) -> List[ELVTSignal]:
    """
    Retrieve the most recent ELVT signals relevant to a specific portfolio position.
    Used by MICS Gate 3 supplementary scoring.

    Args:
        ticker: Portfolio position ticker (e.g., 'MU', 'NVDA')

    Returns:
        List of ELVTSignal objects from all entities affecting this position.
    """
    signals: List[ELVTSignal] = []

    for entity in ENTITY_SEC_TICKERS:
        if entity == 'Alphabet':
            continue

        affected = ENTITY_POSITION_MAP.get(entity, [])
        if ticker not in affected:
            continue

        history = _load_history(entity)
        if len(history) < 2:
            continue  # Need at least 2 data points for velocity

        current = history[-1]
        prior = history[-2]

        for category in ['capex_commitment', 'demand_visibility', 'competitive_posture']:
            current_score = current.get(category, {}).get('score', 0.0)
            prior_score = prior.get(category, {}).get('score', 0.0)
            velocity = _compute_velocity(current_score, prior_score)
            direction, severity = _classify_velocity(velocity)

            signals.append(ELVTSignal(
                entity=entity,
                category=category,
                velocity=round(velocity, 4),
                direction=direction,
                severity=severity,
                affected_positions=[ticker],
                current_score=round(current_score, 4),
                prior_score=round(prior_score, 4),
                quarters_analyzed=len(history),
                explanation=f"{entity} {category}: {direction} ({velocity:+.2f})"
            ))

    return signals
