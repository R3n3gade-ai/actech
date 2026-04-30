"""
ARMS Intelligence Layer: Patent Filing Velocity Tracker (PFVT)

Layer 2 anticipatory intelligence — patent applications are filed 12-18
months before products ship and 24-36 months before material revenue.
When a company begins filing in a specific technology category, it is
describing its competitive position 2-3 years from now.

Five patent categories tracked against CDM entities:
  1. HBM and advanced memory packaging (24-36 month lead)
  2. AI inference chip architecture (18-30 month lead)
  3. PCIe Gen 6 and CXL interconnect (18-24 month lead)
  4. Data center power density and cooling (24-36 month lead)
  5. Custom networking silicon (18-30 month lead)

Data source: USPTO PatentsView API (free bulk data, updated weekly)
Cadence: Monthly — lower frequency signal, patent velocity changes slowly
Cost: $0

Reference: Addendum 3, Section 2.3 — PFVT Specification
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
class PFVTSignal:
    """Velocity signal for patent filing activity in a technology category."""
    category: str
    entity: str
    direction: Literal['ACCELERATING', 'INCREASING', 'ELEVATED', 'DECLINING', 'STABLE']
    velocity: float
    severity: Literal['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
    current_period_count: int
    prior_period_avg: float
    affected_positions: List[str]
    lead_time_months: str
    explanation: str


@dataclass
class PFVTResult:
    """Complete PFVT run output for a single entity."""
    entity: str
    signals: List[PFVTSignal]
    total_patents_scanned: int
    analysis_timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


# --- Configuration (from Addendum 3, Section 2.3) ---

# Patent technology categories with CPC classification codes for USPTO queries
PATENT_CATEGORIES: Dict[str, Dict] = {
    'hbm_memory_packaging': {
        'cpc_codes': ['H01L25/065', 'H01L25/0657', 'H01L23/5384', 'G11C11/4091'],
        'keywords': ['high bandwidth memory', 'HBM', 'advanced memory packaging',
                     'stacked memory', '3D DRAM', 'memory interposer'],
        'lead_months': '24-36',
        'description': 'HBM demand cycle and next-gen spec requirements',
        'thesis_implication': 'MU demand cycle shape',
    },
    'ai_inference_chip': {
        'cpc_codes': ['G06N3/063', 'G06N3/08', 'G06F9/3885'],
        'keywords': ['AI accelerator', 'inference processor', 'neural network processor',
                     'machine learning chip', 'TPU', 'custom AI silicon', 'AI ASIC'],
        'lead_months': '18-30',
        'description': 'Custom ASIC vs GPU substitution risk',
        'thesis_implication': 'NVDA conviction score — substitution risk',
    },
    'pcie_cxl_interconnect': {
        'cpc_codes': ['G06F13/4282', 'G06F13/4291'],
        'keywords': ['PCIe Gen 6', 'CXL interconnect', 'Compute Express Link',
                     'PCIe 6.0', 'coherent interconnect', 'memory fabric'],
        'lead_months': '18-24',
        'description': 'PCIe Gen 6 adoption velocity',
        'thesis_implication': 'ALAB design win pipeline',
    },
    'datacenter_power_cooling': {
        'cpc_codes': ['H05K7/20727', 'H05K7/20763', 'F24F5/0007'],
        'keywords': ['data center cooling', 'liquid cooling server', 'immersion cooling',
                     'data center power distribution', 'rack power density',
                     'AI server cooling', 'direct-to-chip cooling'],
        'lead_months': '24-36',
        'description': 'Next-gen data center power requirements',
        'thesis_implication': 'ETN and VRT TAM expansion',
    },
    'custom_networking_silicon': {
        'cpc_codes': ['H04L49/00', 'H04L45/74'],
        'keywords': ['custom network chip', 'network ASIC', 'AI networking silicon',
                     'custom switch silicon', 'data center network processor',
                     'high radix switch'],
        'lead_months': '18-30',
        'description': 'Custom networking adoption vs merchant silicon',
        'thesis_implication': 'ANET, MRVL, AVGO competitive positioning',
    },
}

# Maps entities (patent assignees) to their positions and USPTO assignee names
ENTITY_CONFIG: Dict[str, Dict] = {
    'Google': {
        'assignee_names': ['Google LLC', 'Google Inc', 'Alphabet Inc'],
        'category_positions': {
            'hbm_memory_packaging': ['MU'],
            'ai_inference_chip': ['NVDA'],
            'pcie_cxl_interconnect': ['ALAB'],
            'datacenter_power_cooling': ['ETN', 'VRT'],
            'custom_networking_silicon': ['ANET', 'MRVL', 'AVGO'],
        },
    },
    'Microsoft': {
        'assignee_names': ['Microsoft Corporation', 'Microsoft Technology Licensing LLC'],
        'category_positions': {
            'hbm_memory_packaging': ['MU'],
            'ai_inference_chip': ['NVDA'],
            'pcie_cxl_interconnect': ['ALAB'],
            'datacenter_power_cooling': ['ETN', 'VRT'],
            'custom_networking_silicon': ['MRVL', 'AVGO'],
        },
    },
    'Amazon': {
        'assignee_names': ['Amazon Technologies Inc', 'Amazon.com Inc'],
        'category_positions': {
            'hbm_memory_packaging': ['MU'],
            'ai_inference_chip': ['NVDA'],
            'pcie_cxl_interconnect': ['ALAB'],
            'datacenter_power_cooling': ['ETN', 'VRT'],
            'custom_networking_silicon': ['MRVL', 'AVGO'],
        },
    },
    'Meta': {
        'assignee_names': ['Meta Platforms Inc', 'Meta Platforms Technologies LLC', 'Facebook Inc'],
        'category_positions': {
            'hbm_memory_packaging': ['MU'],
            'ai_inference_chip': ['NVDA', 'AVGO'],
            'pcie_cxl_interconnect': ['ALAB'],
            'datacenter_power_cooling': ['ETN', 'VRT'],
            'custom_networking_silicon': ['AVGO'],
        },
    },
    'Samsung': {
        'assignee_names': ['Samsung Electronics Co Ltd'],
        'category_positions': {
            'hbm_memory_packaging': ['MU'],
            'ai_inference_chip': [],
            'pcie_cxl_interconnect': [],
            'datacenter_power_cooling': [],
            'custom_networking_silicon': [],
        },
    },
    'SK Hynix': {
        'assignee_names': ['SK Hynix Inc'],
        'category_positions': {
            'hbm_memory_packaging': ['MU'],
            'ai_inference_chip': [],
            'pcie_cxl_interconnect': [],
            'datacenter_power_cooling': [],
            'custom_networking_silicon': [],
        },
    },
    'Intel': {
        'assignee_names': ['Intel Corporation'],
        'category_positions': {
            'hbm_memory_packaging': [],
            'ai_inference_chip': ['NVDA'],
            'pcie_cxl_interconnect': ['ALAB'],
            'datacenter_power_cooling': [],
            'custom_networking_silicon': [],
        },
    },
}

# --- State Persistence ---

_STATE_DIR = os.path.join('achelion_arms', 'state', 'pfvt')


def _ensure_state_dir():
    os.makedirs(_STATE_DIR, exist_ok=True)


def _load_history(entity: str) -> List[Dict]:
    """Load prior monthly patent counts for an entity."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{entity.lower().replace(" ", "_")}_history.json')
    if os.path.exists(path):
        with open(path, 'r') as f:
            return json.load(f)
    return []


def _save_history(entity: str, history: List[Dict]):
    """Persist monthly patent count history."""
    _ensure_state_dir()
    path = os.path.join(_STATE_DIR, f'{entity.lower().replace(" ", "_")}_history.json')
    with open(path, 'w') as f:
        json.dump(history, f, indent=2)


# --- USPTO PatentsView API Integration ---

def _fetch_patent_count(assignee_names: List[str], keywords: List[str],
                        cpc_codes: List[str], months_back: int = 3) -> int:
    """
    Query USPTO PatentsView API for patent application counts.

    Uses the PatentsView v1 API:
    https://search.patentsview.org/api/v1/patent/

    Args:
        assignee_names: Company names to search as patent assignees
        keywords: Technology keywords to search in patent abstracts
        cpc_codes: CPC classification codes to filter by
        months_back: How many months of history to query

    Returns:
        Count of matching patent applications
    """
    cutoff_date = (datetime.datetime.now() - datetime.timedelta(days=months_back * 30)).strftime('%Y-%m-%d')

    # Build the query filter for PatentsView API
    # Use keyword search in abstract/title combined with assignee filter
    assignee_conditions = [{"assignees.assignee_organization": name} for name in assignee_names]
    keyword_text = ' OR '.join(keywords[:3])  # Limit to avoid overly broad queries

    query_filter = {
        "_and": [
            {"_or": assignee_conditions},
            {"_gte": {"patent_date": cutoff_date}},
            {"_text_any": {"patent_abstract": keyword_text}},
        ]
    }

    try:
        params = {
            'q': json.dumps(query_filter),
            'f': json.dumps(["patent_id"]),
            'o': json.dumps({"per_page": 1}),  # We only need the total count
        }

        r = requests.get(
            'https://search.patentsview.org/api/v1/patent/',
            params=params,
            timeout=15,
            headers={'Accept': 'application/json'},
        )
        r.raise_for_status()
        data = r.json()
        total_count = data.get('total_patent_count', 0)
        return total_count

    except Exception as e:
        print(f"[PFVT] USPTO PatentsView API error for {assignee_names[0]}: {e}")
        return 0


# --- Core PFVT Logic ---

def _score_velocity(current: int, prior_avg: float) -> tuple:
    """
    Score patent velocity using same thresholds as JPVI for consistency.
    Returns (direction, severity).
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


def run_pfvt_analysis(entity: str) -> Optional[PFVTResult]:
    """
    Run PFVT analysis for a single entity across all patent categories.

    Returns:
        PFVTResult with velocity signals, or None if entity not configured.
    """
    config = ENTITY_CONFIG.get(entity)
    if not config:
        print(f"[PFVT] No configuration for entity: {entity}")
        return None

    assignee_names = config['assignee_names']
    print(f"[PFVT] Analyzing patent filing velocity for {entity}...")

    signals: List[PFVTSignal] = []
    total_patents = 0
    current_period_data: Dict[str, int] = {}

    # Load history for prior period averages
    history = _load_history(entity)

    for category, cat_config in PATENT_CATEGORIES.items():
        affected_positions = config['category_positions'].get(category, [])
        if not affected_positions:
            continue

        # Fetch current 3-month patent filing count
        current_count = _fetch_patent_count(
            assignee_names,
            cat_config['keywords'],
            cat_config['cpc_codes'],
            months_back=3,
        )
        total_patents += current_count
        current_period_data[category] = current_count

        # Compute prior period average from history (last 6 monthly entries)
        prior_counts = []
        for entry in history[-6:]:
            count = entry.get('categories', {}).get(category, 0)
            if count >= 0:
                prior_counts.append(count)

        prior_avg = sum(prior_counts) / len(prior_counts) if prior_counts else 0.0

        direction, severity, velocity = _score_velocity(current_count, prior_avg)

        signal = PFVTSignal(
            category=category,
            entity=entity,
            direction=direction,
            velocity=round(velocity, 4),
            severity=severity,
            current_period_count=current_count,
            prior_period_avg=round(prior_avg, 2),
            affected_positions=affected_positions,
            lead_time_months=cat_config['lead_months'],
            explanation=f"{entity} {cat_config['description']}: {current_count} patents (3mo) vs "
                        f"{prior_avg:.0f} avg. Velocity {velocity:+.1%}. {direction}. "
                        f"Lead time: {cat_config['lead_months']} months. "
                        f"Implication: {cat_config['thesis_implication']}.",
        )
        signals.append(signal)

        # Log material signals
        if severity in ('CRITICAL', 'HIGH'):
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='PFVT_SIGNAL',
                triggering_module='PFVT',
                triggering_signal=f"{entity} {category}: {direction} "
                                  f"(velocity {velocity:+.1%}, {current_count} patents)",
                ticker=','.join(affected_positions),
            ))

    # Persist current period to history
    history.append({
        'date': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
        'categories': current_period_data,
    })
    # Keep last 24 months of history
    if len(history) > 24:
        history = history[-24:]
    _save_history(entity, history)

    result = PFVTResult(
        entity=entity,
        signals=signals,
        total_patents_scanned=total_patents,
    )

    material_count = len([s for s in signals if s.severity in ('CRITICAL', 'HIGH')])
    print(f"[PFVT] {entity}: {len(signals)} categories scanned, "
          f"{total_patents} total patents, {material_count} material signals.")

    return result


def run_monthly_pfvt_sweep() -> List[PFVTResult]:
    """
    Run PFVT analysis for all configured entities.
    Called monthly — patent velocity changes slowly but leads by 12-36 months.

    Returns:
        List of PFVTResult for all analyzed entities.
    """
    print("--- ARMS PFVT: Monthly Patent Filing Velocity Sweep ---")

    results: List[PFVTResult] = []
    for entity in ENTITY_CONFIG:
        result = run_pfvt_analysis(entity)
        if result:
            results.append(result)

    material_count = sum(
        1 for r in results
        for s in r.signals
        if s.severity in ('CRITICAL', 'HIGH')
    )

    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='PFVT_SWEEP_COMPLETE',
        triggering_module='PFVT',
        triggering_signal=f"Monthly sweep complete. {len(results)} entities analyzed, {material_count} material signals.",
    ))

    print(f"--- PFVT Sweep Complete. {len(results)} entities, {material_count} material signals. ---")
    return results


def get_pfvt_signals_for_position(ticker: str) -> List[PFVTSignal]:
    """
    Retrieve the most recent PFVT signals relevant to a specific portfolio position.
    Used by MICS Gate 3 supplementary scoring.

    Args:
        ticker: Portfolio position ticker (e.g., 'MU', 'NVDA', 'ALAB')

    Returns:
        List of PFVTSignal objects from all entities/categories affecting this position.
    """
    signals: List[PFVTSignal] = []

    for entity, config in ENTITY_CONFIG.items():
        history = _load_history(entity)
        if len(history) < 2:
            continue  # Need at least 2 periods for velocity

        current = history[-1]
        prior_entries = history[-7:-1] if len(history) > 7 else history[:-1]

        for category, affected_positions in config['category_positions'].items():
            if ticker not in affected_positions:
                continue

            current_count = current.get('categories', {}).get(category, 0)
            prior_counts = [e.get('categories', {}).get(category, 0) for e in prior_entries]
            prior_avg = sum(prior_counts) / len(prior_counts) if prior_counts else 0.0

            direction, severity, velocity = _score_velocity(current_count, prior_avg)
            cat_config = PATENT_CATEGORIES[category]

            signals.append(PFVTSignal(
                category=category,
                entity=entity,
                direction=direction,
                velocity=round(velocity, 4),
                severity=severity,
                current_period_count=current_count,
                prior_period_avg=round(prior_avg, 2),
                affected_positions=[ticker],
                lead_time_months=cat_config['lead_months'],
                explanation=f"{entity} {category}: {direction} ({velocity:+.1%}). "
                            f"Thesis impact: {cat_config['thesis_implication']}.",
            ))

    return signals
