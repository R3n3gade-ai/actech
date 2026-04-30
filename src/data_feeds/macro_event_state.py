from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from engine.cdm import NewsItem


# ─── Event Taxonomy ────────────────────────────────────────────────────────────

class EventCategory(str, Enum):
    """Top-level macro event classification."""
    OIL_ENERGY       = 'OIL_ENERGY'
    DIPLOMACY        = 'DIPLOMACY'
    MILITARY         = 'MILITARY'
    REGULATORY       = 'REGULATORY'
    CREDIT           = 'CREDIT'
    TRADE_TARIFF     = 'TRADE_TARIFF'
    CENTRAL_BANK     = 'CENTRAL_BANK'
    ELECTION         = 'ELECTION'
    PANDEMIC_HEALTH  = 'PANDEMIC_HEALTH'
    UNKNOWN          = 'UNKNOWN'


class EventSeverity(str, Enum):
    CRITICAL = 'CRITICAL'   # immediate regime-shift potential
    HIGH     = 'HIGH'       # material impact, needs monitoring
    MEDIUM   = 'MEDIUM'     # notable but contained
    LOW      = 'LOW'        # background noise / watch-list


class EventDirection(str, Enum):
    """Direction of macro impact — does this escalate or de-escalate stress?"""
    ESCALATION    = 'ESCALATION'
    DE_ESCALATION = 'DE_ESCALATION'
    NEUTRAL       = 'NEUTRAL'


# ─── EventCatalogItem ──────────────────────────────────────────────────────────

@dataclass
class EventCatalogItem:
    """
    A typed, auditable, classified macro event.

    Every incoming NewsItem that touches macro-level risk is converted into one
    or more EventCatalogItems via classify_event(). These items carry an explicit
    category, severity, directionality, and time-decay TTL so that downstream
    consumers (Macro Compass, Daily Monitor narrative, Catalyst Calendar) get
    structured data instead of opaque keyword matches.
    """
    event_id: str                         # deterministic hash from source+headline+timestamp
    category: EventCategory
    severity: EventSeverity
    direction: EventDirection
    headline: str
    source: str
    timestamp: str                        # ISO-8601
    stress_contribution: float            # 0.0–1.0 raw contribution to its category score
    ttl_hours: float = 72.0              # how long this event stays active before decay
    affected_sectors: List[str] = field(default_factory=list)
    rationale: str = ''                   # human-readable why this classification was chosen
    source_event_type: str = 'UNKNOWN'    # original NewsItem.event_type passthrough
    expired: bool = False


# ─── Classification Engine ─────────────────────────────────────────────────────

# Keyword → (category, severity, direction, stress_contribution, ttl_hours)
_CLASSIFICATION_RULES = [
    # OIL / ENERGY
    (['oil embargo', 'opec cut', 'hormuz blockade', 'pipeline explosion', 'energy crisis'],
     EventCategory.OIL_ENERGY, EventSeverity.CRITICAL, EventDirection.ESCALATION, 0.80, 96),
    (['oil', 'brent', 'wti', 'hormuz', 'shipping lane', 'tanker', 'refinery',
      'lng', 'natural gas', 'opec'],
     EventCategory.OIL_ENERGY, EventSeverity.HIGH, EventDirection.ESCALATION, 0.45, 72),
    (['surplus', 'oversupply', 'oil price drop', 'demand destruction'],
     EventCategory.OIL_ENERGY, EventSeverity.MEDIUM, EventDirection.DE_ESCALATION, 0.15, 48),

    # DIPLOMACY
    (['talks collapse', 'ceasefire fracture', 'iran walks', 'negotiation breakdown',
      'ambassador recalled', 'diplomatic expulsion', 'treaty withdrawn'],
     EventCategory.DIPLOMACY, EventSeverity.CRITICAL, EventDirection.ESCALATION, 0.70, 96),
    (['sanctions imposed', 'trade restrictions', 'diplomatic warning'],
     EventCategory.DIPLOMACY, EventSeverity.HIGH, EventDirection.ESCALATION, 0.50, 72),
    (['framework agreed', 'productive talks', 'ceasefire holds', 'peace deal',
      'diplomatic progress', 'talks resume', 'confidence building'],
     EventCategory.DIPLOMACY, EventSeverity.MEDIUM, EventDirection.DE_ESCALATION, 0.10, 48),
    (['talks', 'delegation', 'ceasefire', 'envoy'],
     EventCategory.DIPLOMACY, EventSeverity.LOW, EventDirection.NEUTRAL, 0.15, 48),

    # MILITARY
    (['nuclear', 'icbm', 'invasion', 'full-scale war', 'mobilization'],
     EventCategory.MILITARY, EventSeverity.CRITICAL, EventDirection.ESCALATION, 0.90, 120),
    (['missile', 'strike', 'attack', 'retaliation', 'drone', 'airstrike',
      'military operation', 'bombardment', 'naval confrontation'],
     EventCategory.MILITARY, EventSeverity.HIGH, EventDirection.ESCALATION, 0.60, 72),
    (['military exercise', 'troop movement', 'base construction'],
     EventCategory.MILITARY, EventSeverity.MEDIUM, EventDirection.ESCALATION, 0.35, 48),
    (['withdrawal', 'de-escalation', 'troop pullback', 'ceasefire agreement'],
     EventCategory.MILITARY, EventSeverity.MEDIUM, EventDirection.DE_ESCALATION, 0.10, 48),

    # REGULATORY
    (['antitrust ruling', 'regulatory crackdown', 'ban imposed', 'license revoked'],
     EventCategory.REGULATORY, EventSeverity.HIGH, EventDirection.ESCALATION, 0.50, 72),
    (['regulatory filing', 'regulatory', 'compliance', 'sec investigation'],
     EventCategory.REGULATORY, EventSeverity.MEDIUM, EventDirection.NEUTRAL, 0.25, 48),

    # CREDIT
    (['sovereign default', 'debt crisis', 'credit downgrade', 'bank failure'],
     EventCategory.CREDIT, EventSeverity.CRITICAL, EventDirection.ESCALATION, 0.75, 96),
    (['credit event', 'credit warning', 'debt ceiling', 'yield inversion'],
     EventCategory.CREDIT, EventSeverity.HIGH, EventDirection.ESCALATION, 0.45, 72),

    # TRADE / TARIFF
    (['tariff imposed', 'trade war', 'export ban', 'import restriction'],
     EventCategory.TRADE_TARIFF, EventSeverity.HIGH, EventDirection.ESCALATION, 0.55, 72),
    (['tariff', 'trade agreement', 'trade deal'],
     EventCategory.TRADE_TARIFF, EventSeverity.LOW, EventDirection.NEUTRAL, 0.15, 48),

    # CENTRAL BANK
    (['emergency rate', 'rate hike', 'surprise cut', 'quantitative tightening'],
     EventCategory.CENTRAL_BANK, EventSeverity.HIGH, EventDirection.ESCALATION, 0.50, 48),
    (['rate decision', 'fed minutes', 'ecb', 'boj', 'central bank'],
     EventCategory.CENTRAL_BANK, EventSeverity.LOW, EventDirection.NEUTRAL, 0.10, 24),

    # ELECTION
    (['contested election', 'election crisis', 'coup attempt'],
     EventCategory.ELECTION, EventSeverity.HIGH, EventDirection.ESCALATION, 0.55, 96),
    (['election', 'polling', 'primary', 'inauguration'],
     EventCategory.ELECTION, EventSeverity.LOW, EventDirection.NEUTRAL, 0.10, 48),

    # PANDEMIC / HEALTH
    (['pandemic declaration', 'lockdown', 'quarantine', 'health emergency'],
     EventCategory.PANDEMIC_HEALTH, EventSeverity.CRITICAL, EventDirection.ESCALATION, 0.70, 120),
    (['outbreak', 'variant', 'who alert', 'epidemic'],
     EventCategory.PANDEMIC_HEALTH, EventSeverity.MEDIUM, EventDirection.ESCALATION, 0.30, 72),
]

# Map NewsItem.event_type to a fallback category when keywords don't match
_EVENT_TYPE_FALLBACK = {
    'LEGAL_RULING': (EventCategory.REGULATORY, EventSeverity.MEDIUM, EventDirection.NEUTRAL, 0.35),
    'REGULATORY_FILING': (EventCategory.REGULATORY, EventSeverity.LOW, EventDirection.NEUTRAL, 0.20),
    'CREDIT_EVENT': (EventCategory.CREDIT, EventSeverity.HIGH, EventDirection.ESCALATION, 0.45),
    'EARNINGS_WARNING': (EventCategory.UNKNOWN, EventSeverity.MEDIUM, EventDirection.ESCALATION, 0.20),
    'MA_ANNOUNCEMENT': (EventCategory.UNKNOWN, EventSeverity.MEDIUM, EventDirection.NEUTRAL, 0.15),
    'LEADERSHIP_CHANGE': (EventCategory.UNKNOWN, EventSeverity.LOW, EventDirection.NEUTRAL, 0.10),
}


def _make_event_id(source: str, headline: str, timestamp: str) -> str:
    """Deterministic event ID from source material."""
    import hashlib
    raw = f"{source}|{headline}|{timestamp}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def classify_event(item: NewsItem) -> EventCatalogItem:
    """
    Convert a raw NewsItem into a typed EventCatalogItem.

    Rules are evaluated top-down; the first matching rule wins. If no keyword
    rule matches, a fallback based on NewsItem.event_type is used.
    """
    text = f"{item.headline} {item.content}".lower()
    event_id = _make_event_id(item.source, item.headline, item.timestamp)

    # Try keyword-based classification (first match wins)
    for keywords, category, severity, direction, stress, ttl in _CLASSIFICATION_RULES:
        if any(kw in text for kw in keywords):
            return EventCatalogItem(
                event_id=event_id,
                category=category,
                severity=severity,
                direction=direction,
                headline=item.headline,
                source=item.source,
                timestamp=item.timestamp,
                stress_contribution=stress,
                ttl_hours=ttl,
                source_event_type=item.event_type,
                rationale=f"Keyword match in [{category.value}] rule set",
            )

    # Fallback: use NewsItem.event_type if it has a known mapping
    ev = item.event_type.upper()
    if ev in _EVENT_TYPE_FALLBACK:
        cat, sev, dirn, stress = _EVENT_TYPE_FALLBACK[ev]
        return EventCatalogItem(
            event_id=event_id,
            category=cat,
            severity=sev,
            direction=dirn,
            headline=item.headline,
            source=item.source,
            timestamp=item.timestamp,
            stress_contribution=stress,
            ttl_hours=48,
            source_event_type=item.event_type,
            rationale=f"Fallback from event_type={ev}",
        )

    # Unknown — still catalog it so nothing is silently dropped
    return EventCatalogItem(
        event_id=event_id,
        category=EventCategory.UNKNOWN,
        severity=EventSeverity.LOW,
        direction=EventDirection.NEUTRAL,
        headline=item.headline,
        source=item.source,
        timestamp=item.timestamp,
        stress_contribution=0.05,
        ttl_hours=24,
        source_event_type=item.event_type,
        rationale="No classification rule matched; cataloged as UNKNOWN",
    )


def classify_events(items: List[NewsItem]) -> List[EventCatalogItem]:
    """Classify a batch of NewsItems into typed catalog entries."""
    return [classify_event(item) for item in items]


def _is_active(item: EventCatalogItem, now: Optional[datetime.datetime] = None) -> bool:
    """Check if an event is still within its TTL window."""
    if item.expired:
        return False
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    try:
        ts = datetime.datetime.fromisoformat(item.timestamp.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return True  # Can't parse → assume active
    age_hours = (now - ts).total_seconds() / 3600
    return age_hours <= item.ttl_hours


# ─── MacroEventState (unchanged output interface) ─────────────────────────────

@dataclass
class MacroEventState:
    macro_stress_score: float = 0.0
    oil_stress_score: float = 0.0
    diplomacy_breakdown_score: float = 0.0
    military_escalation_score: float = 0.0
    override_floor: float = 0.0
    rationale: List[str] = None
    active_events: List[EventCatalogItem] = field(default_factory=list)

    def __post_init__(self):
        if self.rationale is None:
            self.rationale = []


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


# Category → MacroEventState field mapping
_CATEGORY_SCORE_MAP = {
    EventCategory.OIL_ENERGY:      'oil_stress_score',
    EventCategory.DIPLOMACY:       'diplomacy_breakdown_score',
    EventCategory.MILITARY:        'military_escalation_score',
    EventCategory.REGULATORY:      'macro_stress_score',
    EventCategory.CREDIT:          'macro_stress_score',
    EventCategory.TRADE_TARIFF:    'macro_stress_score',
    EventCategory.CENTRAL_BANK:    'macro_stress_score',
    EventCategory.ELECTION:        'macro_stress_score',
    EventCategory.PANDEMIC_HEALTH: 'macro_stress_score',
    EventCategory.UNKNOWN:         'macro_stress_score',
}


def derive_macro_event_state(events: List[NewsItem]) -> MacroEventState:
    """
    Typed event-state derivation via EventCatalogItem classification.

    Each incoming NewsItem is classified into a typed catalog entry with explicit
    category, severity, direction, stress contribution, and TTL. Only active
    (non-expired) escalation/neutral events contribute to stress scores.
    De-escalation events dampen their category score.

    Output interface (MacroEventState) is unchanged — all downstream consumers
    (Macro Compass, Daily Monitor, EOD Snapshot) work without modification.
    """
    state = MacroEventState()

    if not events:
        return state

    catalog = classify_events(events)
    now = datetime.datetime.now(datetime.timezone.utc)
    active = [e for e in catalog if _is_active(e, now)]
    state.active_events = active

    for item in active:
        target_field = _CATEGORY_SCORE_MAP.get(item.category, 'macro_stress_score')
        current = getattr(state, target_field)

        if item.direction == EventDirection.DE_ESCALATION:
            # De-escalation dampens: reduce current score by half of its contribution
            new_val = max(0.0, current - item.stress_contribution * 0.5)
            setattr(state, target_field, new_val)
            state.rationale.append(
                f"[{item.category.value}] DE-ESCALATION: {item.headline[:80]} "
                f"(-{item.stress_contribution * 0.5:.2f} on {target_field})"
            )
        else:
            # Escalation or neutral: take the max
            new_val = max(current, item.stress_contribution)
            setattr(state, target_field, new_val)
            state.rationale.append(
                f"[{item.category.value}] {item.severity.value}: {item.headline[:80]} "
                f"(+{item.stress_contribution:.2f} on {target_field})"
            )

    # Composite override floor (same weights as before)
    weighted = (
        state.macro_stress_score * 0.35 +
        state.oil_stress_score * 0.25 +
        state.diplomacy_breakdown_score * 0.20 +
        state.military_escalation_score * 0.20
    )
    state.override_floor = _clip(weighted)
    state.macro_stress_score = _clip(state.macro_stress_score)
    state.oil_stress_score = _clip(state.oil_stress_score)
    state.diplomacy_breakdown_score = _clip(state.diplomacy_breakdown_score)
    state.military_escalation_score = _clip(state.military_escalation_score)
    return state
