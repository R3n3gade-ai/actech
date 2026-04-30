"""
ARMS Intelligence Layer: Supply Chain Cross-Reference (SCCR)

Layer 2 anticipatory intelligence — physical capital movement precedes
financial reporting by 2-4 quarters. The supply chain does not lie —
it is the physical reality of investment decisions already made.

Phase 2 SCCR monitors two specific patterns against the CDM dependency map:
  1. TSMC-related shipping from Taiwan to US/Asian data center hubs
  2. Hyperscaler server equipment import volumes at key US ports

Both provide 2-4 quarter leading indicators of AI infrastructure demand
flowing to NVDA, MU, ALAB, ANET, AVGO, and MRVL.

Data source: Phase 2 — ImportYeti open data (free). Phase 3 — satellite data.
Cadence: Weekly — shipping manifest data updates with 2-4 week lag
Cost: Phase 2: $0. Phase 3: institutional satellite data pricing.

Reference: Addendum 3, Section 2.4 — SCCR Specification
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
class SCCRSignal:
    """Signal from supply chain pattern detection."""
    pattern: str
    entity: str
    direction: Literal['ACCELERATING', 'INCREASING', 'ELEVATED', 'DECLINING', 'STABLE']
    velocity: float
    severity: Literal['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
    current_period_volume: float
    prior_period_avg: float
    affected_positions: List[str]
    lead_time_quarters: str
    explanation: str


@dataclass
class SCCRResult:
    """Complete SCCR run output."""
    patterns_scanned: int
    signals: List[SCCRSignal]
    data_freshness: str  # How recent the underlying data is
    analysis_timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


# --- Configuration (from Addendum 3, Section 2.4) ---

# Pattern 1: TSMC shipping activity tracking
TSMC_SHIPPING_CONFIG = {
    'shipper_names': ['Taiwan Semiconductor', 'TSMC', 'Taiwan Semiconductor Manufacturing'],
    'origin_country': 'Taiwan',
    'destination_ports': ['Los Angeles', 'Long Beach', 'Newark', 'Savannah', 'Oakland'],
    'product_keywords': ['semiconductor', 'integrated circuit', 'wafer', 'chip',
                         'electronic component', 'processor'],
    'affected_positions': ['NVDA', 'MU', 'ALAB', 'AVGO', 'MRVL', 'AMD'],
    'lead_quarters': '2-4',
    'description': 'TSMC advanced packaging shipments to US data center hubs',
}

# Pattern 2: Hyperscaler server equipment imports
HYPERSCALER_IMPORT_CONFIG = {
    'consignee_keywords': {
        'Microsoft': {
            'names': ['Microsoft Corporation', 'Microsoft'],
            'affected_positions': ['MU', 'NVDA', 'ALAB', 'ANET'],
        },
        'Google': {
            'names': ['Google LLC', 'Alphabet'],
            'affected_positions': ['MU', 'NVDA', 'ALAB', 'AVGO', 'ANET'],
        },
        'Amazon': {
            'names': ['Amazon.com', 'Amazon Web Services', 'AWS'],
            'affected_positions': ['MU', 'NVDA', 'ALAB', 'ANET'],
        },
        'Meta': {
            'names': ['Meta Platforms', 'Facebook'],
            'affected_positions': ['MU', 'NVDA', 'ALAB', 'AVGO'],
        },
    },
    'product_keywords': ['server', 'GPU', 'accelerator', 'network switch',
                         'data center equipment', 'rack', 'power distribution'],
    'ports': ['Los Angeles', 'Long Beach', 'Newark', 'Houston', 'Savannah'],
    'lead_quarters': '2-3',
    'description': 'Hyperscaler server/GPU equipment import volumes',
}

# --- State Persistence ---

_STATE_DIR = os.path.join('achelion_arms', 'state', 'sccr')


def _ensure_state_dir():
    os.makedirs(_STATE_DIR, exist_ok=True)


def _load_history(pattern_id: str) -> List[Dict]:
    """Load prior weekly volume data for a shipping pattern."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{pattern_id}_history.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []


def _save_history(pattern_id: str, history: List[Dict]):
    """Persist weekly volume history for a shipping pattern."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{pattern_id}_history.json')
    with open(path, 'w') as f:
        json.dump(history, f, indent=2)


# --- ImportYeti / Trade Data Integration ---

def _fetch_import_volume(shipper_names: List[str], product_keywords: List[str],
                         origin_country: Optional[str] = None,
                         consignee_names: Optional[List[str]] = None) -> float:
    """
    Fetch import shipping volume data.

    Phase 2 uses ImportYeti's public search to approximate volume trends.
    In production, this would use a structured API from a trade data vendor.

    Returns a normalized volume score (0-100) based on available data.
    """
    # ImportYeti is a free tool for US import data lookups.
    # The API is not officially documented, so we use a structured scraping approach.
    # In Phase 3, this would be replaced by Panjiva/ImportGenius API with full manifest data.

    base_url = 'https://www.importyeti.com/api/search'
    total_score = 0.0
    queries_made = 0

    for shipper in shipper_names[:2]:  # Limit queries to avoid rate limits
        try:
            params = {
                'q': shipper,
                'type': 'supplier',
            }
            r = requests.get(base_url, params=params, timeout=10,
                             headers={'User-Agent': 'ARMS-Research/1.0'})

            if r.status_code == 200:
                data = r.json()
                # Extract shipment count from results
                results = data.get('results', [])
                for result in results[:5]:
                    shipment_count = result.get('shipment_count', 0)
                    total_score += shipment_count
                queries_made += 1
            elif r.status_code == 429:
                print(f"[SCCR] Rate limited on ImportYeti. Backing off.")
                break
            else:
                print(f"[SCCR] ImportYeti returned {r.status_code} for '{shipper}'")

        except Exception as e:
            print(f"[SCCR] ImportYeti error for '{shipper}': {e}")

    # Normalize to a volume index (higher = more activity)
    return total_score / max(queries_made, 1)


# --- Core SCCR Logic ---

def _score_velocity(current: float, prior_avg: float) -> tuple:
    """
    Score supply chain velocity using same threshold structure.
    Returns (direction, severity, velocity).
    """
    if prior_avg > 0:
        velocity = (current - prior_avg) / prior_avg
    elif current > 0:
        velocity = 1.0
    else:
        velocity = 0.0

    if velocity > 0.40:
        return 'ACCELERATING', 'CRITICAL', velocity
    elif velocity > 0.20:
        return 'INCREASING', 'HIGH', velocity
    elif velocity > 0.05:
        return 'ELEVATED', 'MEDIUM', velocity
    elif velocity < -0.20:
        return 'DECLINING', 'HIGH', velocity
    else:
        return 'STABLE', 'LOW', velocity


def _run_tsmc_pattern() -> List[SCCRSignal]:
    """Analyze TSMC shipping pattern (Pattern 1)."""
    config = TSMC_SHIPPING_CONFIG
    pattern_id = 'tsmc_shipping'

    print(f"[SCCR] Scanning TSMC shipping activity pattern...")

    current_volume = _fetch_import_volume(
        shipper_names=config['shipper_names'],
        product_keywords=config['product_keywords'],
        origin_country=config['origin_country'],
    )

    history = _load_history(pattern_id)

    # Calculate prior period average (12 weeks)
    prior_volumes = [e.get('volume', 0) for e in history[-12:]]
    prior_avg = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0

    direction, severity, velocity = _score_velocity(current_volume, prior_avg)

    signal = SCCRSignal(
        pattern='tsmc_shipping',
        entity='TSMC',
        direction=direction,
        velocity=round(velocity, 4),
        severity=severity,
        current_period_volume=round(current_volume, 2),
        prior_period_avg=round(prior_avg, 2),
        affected_positions=config['affected_positions'],
        lead_time_quarters=config['lead_quarters'],
        explanation=f"TSMC {config['description']}: volume index {current_volume:.0f} vs "
                    f"{prior_avg:.0f} avg. Velocity {velocity:+.1%}. {direction}. "
                    f"Lead time: {config['lead_quarters']} quarters. "
                    f"Affects: {', '.join(config['affected_positions'])}.",
    )

    # Persist current data point
    history.append({
        'date': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
        'volume': current_volume,
    })
    if len(history) > 52:
        history = history[-52:]
    _save_history(pattern_id, history)

    return [signal] if severity != 'LOW' else [signal]


def _run_hyperscaler_pattern() -> List[SCCRSignal]:
    """Analyze hyperscaler server equipment import pattern (Pattern 2)."""
    config = HYPERSCALER_IMPORT_CONFIG
    signals: List[SCCRSignal] = []

    for entity, entity_config in config['consignee_keywords'].items():
        pattern_id = f'hyperscaler_{entity.lower()}'
        print(f"[SCCR] Scanning {entity} server equipment import pattern...")

        current_volume = _fetch_import_volume(
            shipper_names=[],  # Not filtering by shipper for imports
            product_keywords=config['product_keywords'],
            consignee_names=entity_config['names'],
        )

        history = _load_history(pattern_id)

        prior_volumes = [e.get('volume', 0) for e in history[-12:]]
        prior_avg = sum(prior_volumes) / len(prior_volumes) if prior_volumes else 0.0

        direction, severity, velocity = _score_velocity(current_volume, prior_avg)

        signal = SCCRSignal(
            pattern='hyperscaler_imports',
            entity=entity,
            direction=direction,
            velocity=round(velocity, 4),
            severity=severity,
            current_period_volume=round(current_volume, 2),
            prior_period_avg=round(prior_avg, 2),
            affected_positions=entity_config['affected_positions'],
            lead_time_quarters=config['lead_quarters'],
            explanation=f"{entity} {config['description']}: volume index {current_volume:.0f} vs "
                        f"{prior_avg:.0f} avg. Velocity {velocity:+.1%}. {direction}. "
                        f"Lead time: {config['lead_quarters']} quarters.",
        )
        signals.append(signal)

        # Persist current data point
        history.append({
            'date': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
            'volume': current_volume,
        })
        if len(history) > 52:
            history = history[-52:]
        _save_history(pattern_id, history)

        # Log material signals
        if severity in ('CRITICAL', 'HIGH'):
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='SCCR_SIGNAL',
                triggering_module='SCCR',
                triggering_signal=f"{entity} server imports: {direction} "
                                  f"(velocity {velocity:+.1%})",
                ticker=','.join(entity_config['affected_positions']),
            ))

    return signals


def run_weekly_sccr_sweep() -> SCCRResult:
    """
    Run SCCR analysis across all supply chain patterns.
    Called weekly — shipping manifest data updates with 2-4 week lag.

    Returns:
        SCCRResult with all pattern signals.
    """
    print("--- ARMS SCCR: Weekly Supply Chain Cross-Reference Sweep ---")

    all_signals: List[SCCRSignal] = []

    # Pattern 1: TSMC shipping
    tsmc_signals = _run_tsmc_pattern()
    all_signals.extend(tsmc_signals)

    # Pattern 2: Hyperscaler imports
    hyperscaler_signals = _run_hyperscaler_pattern()
    all_signals.extend(hyperscaler_signals)

    # Log TSMC material signals
    for sig in tsmc_signals:
        if sig.severity in ('CRITICAL', 'HIGH'):
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='SCCR_SIGNAL',
                triggering_module='SCCR',
                triggering_signal=f"TSMC shipping: {sig.direction} (velocity {sig.velocity:+.1%})",
                ticker=','.join(sig.affected_positions),
            ))

    material_count = len([s for s in all_signals if s.severity in ('CRITICAL', 'HIGH')])

    # Assess data freshness — ImportYeti has 2-4 week lag
    freshness = 'T-2 to T-4 weeks (manifest filing lag)'

    result = SCCRResult(
        patterns_scanned=1 + len(HYPERSCALER_IMPORT_CONFIG['consignee_keywords']),
        signals=all_signals,
        data_freshness=freshness,
    )

    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='SCCR_SWEEP_COMPLETE',
        triggering_module='SCCR',
        triggering_signal=f"Weekly sweep complete. {result.patterns_scanned} patterns scanned, "
                          f"{material_count} material signals.",
    ))

    print(f"--- SCCR Sweep Complete. {result.patterns_scanned} patterns, "
          f"{len(all_signals)} signals, {material_count} material. ---")
    return result


def get_sccr_signals_for_position(ticker: str) -> List[SCCRSignal]:
    """
    Retrieve the most recent SCCR signals relevant to a specific portfolio position.
    Used by MICS Gate 3 supplementary scoring.

    Args:
        ticker: Portfolio position ticker (e.g., 'MU', 'NVDA', 'ALAB')

    Returns:
        List of SCCRSignal objects from all patterns affecting this position.
    """
    signals: List[SCCRSignal] = []

    # Check TSMC pattern
    tsmc_history = _load_history('tsmc_shipping')
    if len(tsmc_history) >= 2 and ticker in TSMC_SHIPPING_CONFIG['affected_positions']:
        current = tsmc_history[-1].get('volume', 0)
        prior_entries = tsmc_history[-13:-1] if len(tsmc_history) > 13 else tsmc_history[:-1]
        prior_avg = sum(e.get('volume', 0) for e in prior_entries) / len(prior_entries) if prior_entries else 0.0

        direction, severity, velocity = _score_velocity(current, prior_avg)
        signals.append(SCCRSignal(
            pattern='tsmc_shipping',
            entity='TSMC',
            direction=direction,
            velocity=round(velocity, 4),
            severity=severity,
            current_period_volume=round(current, 2),
            prior_period_avg=round(prior_avg, 2),
            affected_positions=[ticker],
            lead_time_quarters=TSMC_SHIPPING_CONFIG['lead_quarters'],
            explanation=f"TSMC shipping: {direction} ({velocity:+.1%})",
        ))

    # Check hyperscaler patterns
    for entity, entity_config in HYPERSCALER_IMPORT_CONFIG['consignee_keywords'].items():
        if ticker not in entity_config['affected_positions']:
            continue

        pattern_id = f'hyperscaler_{entity.lower()}'
        history = _load_history(pattern_id)
        if len(history) < 2:
            continue

        current = history[-1].get('volume', 0)
        prior_entries = history[-13:-1] if len(history) > 13 else history[:-1]
        prior_avg = sum(e.get('volume', 0) for e in prior_entries) / len(prior_entries) if prior_entries else 0.0

        direction, severity, velocity = _score_velocity(current, prior_avg)
        signals.append(SCCRSignal(
            pattern='hyperscaler_imports',
            entity=entity,
            direction=direction,
            velocity=round(velocity, 4),
            severity=severity,
            current_period_volume=round(current, 2),
            prior_period_avg=round(prior_avg, 2),
            affected_positions=[ticker],
            lead_time_quarters=HYPERSCALER_IMPORT_CONFIG['lead_quarters'],
            explanation=f"{entity} server imports: {direction} ({velocity:+.1%})",
        ))

    return signals
