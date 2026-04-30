"""
ARMS Intelligence Layer: Job Posting Velocity Intelligence (JPVI)

Layer 2 anticipatory intelligence — tracks hiring velocity at CDM named
entities across job categories that are leading indicators of AI
infrastructure spend. Hiring decisions precede procurement decisions —
you hire the team before you buy the equipment.

Four job categories tracked:
  1. Data center infrastructure engineering (6-9 month lead on capex)
  2. AI/ML infrastructure and platform (4-6 month lead on procurement)
  3. Network engineering — AI cluster connectivity (4-6 month lead)
  4. Power and cooling engineering (6-12 month lead on power procurement)

Velocity scoring:
  ACCELERATING >0.40 (CRITICAL)
  INCREASING   >0.20 (HIGH)
  ELEVATED     >0.05 (MEDIUM)
  DECLINING    <-0.20 (HIGH)
  STABLE       default (LOW)

Data source: Adzuna API (free tier)
Cadence: Weekly — Monday cadence aligned with Systematic Scan Engine
Cost: $0

Reference: Addendum 3, Section 2.2 — JPVI Specification
"""

import datetime
import json
import os
import requests
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Literal

from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class JPVISignal:
    """Velocity signal for a single entity/category combination."""
    entity: str
    category: str
    direction: Literal['ACCELERATING', 'INCREASING', 'ELEVATED', 'DECLINING', 'STABLE']
    velocity: float
    severity: Literal['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
    current_30d: int
    prior_90d_avg: float
    affected_positions: List[str]
    explanation: str


@dataclass
class JPVIResult:
    """Complete JPVI run output for a single entity."""
    entity: str
    signals: List[JPVISignal]
    total_postings_scanned: int
    analysis_timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


# --- Configuration (from Addendum 3, Section 2.2) ---

# Job categories and their search keywords for Adzuna API
JOB_CATEGORIES: Dict[str, Dict] = {
    'data_center_infrastructure': {
        'keywords': ['data center engineer', 'data center infrastructure', 'datacenter construction',
                     'data center operations', 'data center design'],
        'lead_months': '6-9',
        'description': 'Physical layer — buys AI chips and memory',
    },
    'ai_ml_infrastructure': {
        'keywords': ['AI infrastructure engineer', 'ML platform engineer', 'AI systems engineer',
                     'machine learning infrastructure', 'GPU cluster engineer', 'AI training infrastructure'],
        'lead_months': '4-6',
        'description': 'Training/inference systems — consumes GPU and HBM',
    },
    'network_engineering': {
        'keywords': ['network engineer AI', 'AI cluster networking', 'data center network engineer',
                     'network infrastructure engineer', 'high performance networking'],
        'lead_months': '4-6',
        'description': 'Networking layer for AI clusters',
    },
    'power_cooling': {
        'keywords': ['power engineer data center', 'cooling engineer', 'electrical engineer data center',
                     'power systems engineer', 'thermal engineer data center', 'liquid cooling engineer'],
        'lead_months': '6-12',
        'description': 'Infrastructure enabling AI data center density',
    },
}

# Maps CDM entities to their Adzuna company search terms and affected positions
ENTITY_CONFIG: Dict[str, Dict] = {
    'Microsoft': {
        'company_search': 'Microsoft',
        'category_positions': {
            'data_center_infrastructure': ['MU', 'NVDA', 'ALAB', 'ANET'],
            'ai_ml_infrastructure': ['NVDA', 'MU', 'AVGO', 'MRVL'],
            'network_engineering': ['ANET', 'MRVL', 'AVGO'],
            'power_cooling': ['ETN', 'VRT'],
        },
    },
    'Google': {
        'company_search': 'Google',
        'category_positions': {
            'data_center_infrastructure': ['MU', 'NVDA', 'ALAB', 'ANET'],
            'ai_ml_infrastructure': ['NVDA', 'MU', 'AVGO', 'MRVL'],
            'network_engineering': ['ANET', 'MRVL', 'AVGO'],
            'power_cooling': ['ETN', 'VRT'],
        },
    },
    'Amazon': {
        'company_search': 'Amazon',
        'category_positions': {
            'data_center_infrastructure': ['MU', 'NVDA', 'ALAB', 'ANET'],
            'ai_ml_infrastructure': ['NVDA', 'MU', 'AVGO', 'MRVL'],
            'network_engineering': ['ANET', 'MRVL', 'AVGO'],
            'power_cooling': ['ETN', 'VRT'],
        },
    },
    'Meta': {
        'company_search': 'Meta',
        'category_positions': {
            'data_center_infrastructure': ['MU', 'NVDA', 'ALAB', 'ANET'],
            'ai_ml_infrastructure': ['NVDA', 'MU', 'AVGO'],
            'network_engineering': ['ANET', 'AVGO'],
            'power_cooling': ['ETN', 'VRT'],
        },
    },
    'Oracle': {
        'company_search': 'Oracle',
        'category_positions': {
            'data_center_infrastructure': ['NVDA'],
            'ai_ml_infrastructure': ['NVDA'],
            'network_engineering': [],
            'power_cooling': [],
        },
    },
}

# --- State Persistence ---

_STATE_DIR = os.path.join('achelion_arms', 'state', 'jpvi')


def _ensure_state_dir():
    os.makedirs(_STATE_DIR, exist_ok=True)


def _load_history(entity: str) -> List[Dict]:
    """Load prior weekly posting counts for an entity."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{entity.lower()}_history.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []


def _save_history(entity: str, history: List[Dict]):
    """Persist weekly posting count history."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{entity.lower()}_history.json')
    with open(path, 'w') as f:
        json.dump(history, f, indent=2)


# --- Adzuna API Integration ---

def _fetch_posting_count(company: str, keywords: List[str], days: int = 30) -> int:
    """
    Fetch job posting count from Adzuna API for a company/keyword combination.

    Requires ADZUNA_APP_ID and ADZUNA_APP_KEY environment variables.
    Uses the US jobs endpoint (api.adzuna.com/v1/api/jobs/us/search/1).
    """
    app_id = os.environ.get('ADZUNA_APP_ID', '')
    app_key = os.environ.get('ADZUNA_APP_KEY', '')

    if not app_id or not app_key:
        print(f"[JPVI] Adzuna API credentials not configured. Set ADZUNA_APP_ID and ADZUNA_APP_KEY.")
        return 0

    total_count = 0
    # Search with each keyword variant, deduplicate by taking the max
    # (Adzuna doesn't support complex boolean queries well on free tier)
    keyword_counts = []

    for kw in keywords:
        try:
            params = {
                'app_id': app_id,
                'app_key': app_key,
                'what': kw,
                'what_and': company,
                'max_days_old': days,
                'results_per_page': 0,  # We only need the count
                'content-type': 'application/json',
            }
            r = requests.get(
                'https://api.adzuna.com/v1/api/jobs/us/search/1',
                params=params,
                timeout=10,
            )
            r.raise_for_status()
            data = r.json()
            count = data.get('count', 0)
            keyword_counts.append(count)
        except Exception as e:
            print(f"[JPVI] Adzuna API error for '{company}' + '{kw}': {e}")
            keyword_counts.append(0)

    # Use max across keyword variants to avoid double-counting overlapping queries
    total_count = max(keyword_counts) if keyword_counts else 0
    return total_count


# --- Core JPVI Logic ---

def score_jpvi_velocity(entity: str, category: str,
                        current_30d: int, prior_90d_avg: float,
                        affected_positions: List[str]) -> JPVISignal:
    """
    Score velocity per Addendum 3 specification:
      ACCELERATING >0.40 → CRITICAL
      INCREASING   >0.20 → HIGH
      ELEVATED     >0.05 → MEDIUM
      DECLINING    <-0.20 → HIGH
      STABLE       default → LOW
    """
    if prior_90d_avg > 0:
        velocity = (current_30d - prior_90d_avg) / prior_90d_avg
    elif current_30d > 0:
        velocity = 1.0  # New postings where there were none — significant
    else:
        velocity = 0.0

    if velocity > 0.40:
        direction, severity = 'ACCELERATING', 'CRITICAL'
    elif velocity > 0.20:
        direction, severity = 'INCREASING', 'HIGH'
    elif velocity > 0.05:
        direction, severity = 'ELEVATED', 'MEDIUM'
    elif velocity < -0.20:
        direction, severity = 'DECLINING', 'HIGH'
    else:
        direction, severity = 'STABLE', 'LOW'

    cat_desc = JOB_CATEGORIES.get(category, {}).get('description', category)
    lead_time = JOB_CATEGORIES.get(category, {}).get('lead_months', '?')

    return JPVISignal(
        entity=entity,
        category=category,
        direction=direction,
        velocity=round(velocity, 4),
        severity=severity,
        current_30d=current_30d,
        prior_90d_avg=round(prior_90d_avg, 2),
        affected_positions=affected_positions,
        explanation=f"{entity} {cat_desc}: {current_30d} postings (30d) vs {prior_90d_avg:.0f} avg (90d). "
                    f"Velocity {velocity:+.1%}. {direction}. Lead time: {lead_time} months."
    )


def run_jpvi_analysis(entity: str) -> Optional[JPVIResult]:
    """
    Run JPVI analysis for a single CDM named entity across all job categories.

    Returns:
        JPVIResult with velocity signals per category, or None if entity not configured.
    """
    config = ENTITY_CONFIG.get(entity)
    if not config:
        print(f"[JPVI] No configuration for entity: {entity}")
        return None

    company_search = config['company_search']
    print(f"[JPVI] Analyzing job posting velocity for {entity}...")

    signals: List[JPVISignal] = []
    total_postings = 0
    current_week_data: Dict[str, int] = {}

    # Load history for prior 90-day averages
    history = _load_history(entity)

    for category, cat_config in JOB_CATEGORIES.items():
        affected_positions = config['category_positions'].get(category, [])
        if not affected_positions:
            continue  # Skip categories with no position mappings for this entity

        # Fetch current 30-day posting count
        current_30d = _fetch_posting_count(company_search, cat_config['keywords'], days=30)
        total_postings += current_30d
        current_week_data[category] = current_30d

        # Compute prior 90-day average from history (last 12 weeks of data)
        prior_counts = []
        for entry in history[-12:]:
            count = entry.get('categories', {}).get(category, 0)
            if count > 0:
                prior_counts.append(count)

        prior_90d_avg = sum(prior_counts) / len(prior_counts) if prior_counts else 0.0

        signal = score_jpvi_velocity(entity, category, current_30d, prior_90d_avg, affected_positions)
        signals.append(signal)

        # Log material signals
        if signal.severity in ('CRITICAL', 'HIGH'):
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='JPVI_SIGNAL',
                triggering_module='JPVI',
                triggering_signal=f"{entity} {category}: {signal.direction} "
                                  f"(velocity {signal.velocity:+.1%}, {current_30d} postings)",
                ticker=','.join(affected_positions),
            ))

    # Persist current week to history
    history.append({
        'date': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
        'categories': current_week_data,
    })
    # Keep last 52 weeks of history (1 year)
    if len(history) > 52:
        history = history[-52:]
    _save_history(entity, history)

    result = JPVIResult(
        entity=entity,
        signals=signals,
        total_postings_scanned=total_postings,
    )

    material_count = len([s for s in signals if s.severity in ('CRITICAL', 'HIGH')])
    print(f"[JPVI] {entity}: {len(signals)} categories scanned, "
          f"{total_postings} total postings, {material_count} material signals.")

    return result


def run_weekly_jpvi_sweep() -> List[JPVIResult]:
    """
    Run JPVI analysis for all configured CDM entities.
    Called every Monday aligned with Systematic Scan Engine cadence.

    Returns:
        List of JPVIResult for all analyzed entities.
    """
    print("--- ARMS JPVI: Weekly Job Posting Velocity Sweep ---")

    results: List[JPVIResult] = []
    for entity in ENTITY_CONFIG:
        result = run_jpvi_analysis(entity)
        if result:
            results.append(result)

    material_count = sum(
        1 for r in results
        for s in r.signals
        if s.severity in ('CRITICAL', 'HIGH')
    )

    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='JPVI_SWEEP_COMPLETE',
        triggering_module='JPVI',
        triggering_signal=f"Weekly sweep complete. {len(results)} entities analyzed, {material_count} material signals.",
    ))

    print(f"--- JPVI Sweep Complete. {len(results)} entities, {material_count} material signals. ---")
    return results


def get_jpvi_signals_for_position(ticker: str) -> List[JPVISignal]:
    """
    Retrieve the most recent JPVI signals relevant to a specific portfolio position.
    Used by MICS Gate 3 supplementary scoring.

    Args:
        ticker: Portfolio position ticker (e.g., 'MU', 'NVDA', 'ETN')

    Returns:
        List of JPVISignal objects from all entities/categories affecting this position.
    """
    signals: List[JPVISignal] = []

    for entity, config in ENTITY_CONFIG.items():
        history = _load_history(entity)
        if len(history) < 4:
            continue  # Need at least 4 weeks for meaningful velocity

        current = history[-1]
        # Use prior 12 weeks for 90-day average
        prior_entries = history[-13:-1] if len(history) > 13 else history[:-1]

        for category, affected_positions in config['category_positions'].items():
            if ticker not in affected_positions:
                continue

            current_count = current.get('categories', {}).get(category, 0)
            prior_counts = [e.get('categories', {}).get(category, 0) for e in prior_entries]
            prior_avg = sum(prior_counts) / len(prior_counts) if prior_counts else 0.0

            signal = score_jpvi_velocity(entity, category, current_count, prior_avg, [ticker])
            signals.append(signal)

    return signals
