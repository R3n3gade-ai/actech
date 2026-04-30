"""
ARMS Engine: Portfolio Architecture Integrity Engine (PAIE)

Autonomous pre-execution integrity layer that ensures aggregate portfolio
architecture remains coherent at the moment of queue execution.

PAIE runs every required check, resolves every resolvable condition per
the governing rulebook, and executes or adjusts the queue without PM input.

PAIE never asks the PM a question. PAIE reports what it did.

Four functional components:
  1. Thesis Tag Registry (loaded from rulebook.json)
  2. Thesis Concentration Calculator
  3. Sizing Coherence Engine
  4. Deployment Pre-Flight (master orchestrator)

Three execution states: CLEARED | ADJUSTED | BLOCKED

Reference: PAIE FSD v1.0, SEM Addendum 5
"""
import json
import os
import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from reporting.audit_log import SessionLogEntry, append_to_log


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class PreflightAdjustment:
    """Record of a single auto-adjustment made by PAIE."""
    ticker: str
    original_weight: float
    adjusted_weight: float
    rule: str           # e.g. "AI_POWER_INFRA cap resolution"
    component: str      # 'CONCENTRATION' | 'COHERENCE'


@dataclass
class PreflightLog:
    """Complete pre-flight report produced by PAIE."""
    timestamp: str
    trigger_score: float
    status: str = 'PENDING'  # CLEARED | ADJUSTED | BLOCKED
    thesis_concentrations: Dict[str, float] = field(default_factory=dict)
    cap_breaches: List[str] = field(default_factory=list)
    adjustments: List[PreflightAdjustment] = field(default_factory=list)
    coherence_adjustments: List[PreflightAdjustment] = field(default_factory=list)
    cdf_alerts: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)

    def record_adjustments(self, adjs: List[PreflightAdjustment]):
        self.adjustments.extend(adjs)

    def record_coherence(self, adjs: List[PreflightAdjustment]):
        self.coherence_adjustments.extend(adjs)

    def record_cdf_status(self, alerts: List[str]):
        self.cdf_alerts.extend(alerts)

    def set_status(self, status: str):
        self.status = status

    @property
    def all_adjustments(self) -> List[PreflightAdjustment]:
        return self.adjustments + self.coherence_adjustments


# ---------------------------------------------------------------------------
# Rulebook Loader
# ---------------------------------------------------------------------------

_RULEBOOK_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'rulebook.json')


def _load_rulebook(path: str = _RULEBOOK_PATH) -> dict:
    """Load the canonical rulebook.json. Read-only — never written by PAIE."""
    resolved = os.path.normpath(path)
    if not os.path.exists(resolved):
        return {
            'thesis_caps': {'DEFAULT': 0.30},
            'thesis_tag_registry': {},
            'sizing_hierarchy': {},
            'conflict_resolution_protocol': 'engineering_ticket',
            'single_thesis_cap_default': 0.30,
        }
    with open(resolved, 'r', encoding='utf-8') as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. Thesis Tag Registry
# ---------------------------------------------------------------------------

def get_thesis_tags(ticker: str, rulebook: dict) -> List[str]:
    """Return the thesis anchor tags for a given ticker."""
    registry = rulebook.get('thesis_tag_registry', {})
    return registry.get(ticker, [])


# ---------------------------------------------------------------------------
# 2. Thesis Concentration Calculator
# ---------------------------------------------------------------------------

def calculate_thesis_concentration(
    book: Dict[str, float],
    rulebook: dict,
) -> Dict[str, float]:
    """
    Calculate real-time thesis exposure weight as percentage of equity NAV.

    Args:
        book: {ticker: nav_weight} for all equity positions
        rulebook: loaded rulebook dict (contains thesis_tag_registry)

    Returns:
        {thesis_tag: total_weight} across all positions sharing that tag
    """
    registry = rulebook.get('thesis_tag_registry', {})
    concentration: Dict[str, float] = {}
    for ticker, weight in book.items():
        tags = registry.get(ticker, [])
        for tag in tags:
            concentration[tag] = concentration.get(tag, 0.0) + weight
    return concentration


def check_cap_breaches(
    concentration: Dict[str, float],
    rulebook: dict,
) -> List[Tuple[str, float, float]]:
    """
    Compare thesis concentrations against caps defined in rulebook.

    Returns list of (thesis_tag, current_weight, cap) for breaches.
    """
    caps = rulebook.get('thesis_caps', {})
    default_cap = rulebook.get('single_thesis_cap_default', 0.30)
    breaches = []
    for tag, weight in concentration.items():
        cap = caps.get(tag, default_cap)
        if weight > cap:
            breaches.append((tag, weight, cap))
    return breaches


# ---------------------------------------------------------------------------
# 3. Sizing Coherence Engine
# ---------------------------------------------------------------------------

def resolve_cap_breaches(
    queue: Dict[str, float],
    breaches: List[Tuple[str, float, float]],
    book: Dict[str, float],
    rulebook: dict,
) -> Tuple[Dict[str, float], List[PreflightAdjustment]]:
    """
    Auto-resolve cap breaches by trimming positions per sizing hierarchy.

    Trims positions in the order defined by the hierarchy's trim_order,
    reducing their weight proportionally until the thesis tag is at or
    below its cap.

    Args:
        queue: {ticker: target_weight} — mutable copy of deployment targets
        breaches: list of (tag, current_weight, cap) from check_cap_breaches
        book: {ticker: current_nav_weight} of existing portfolio
        rulebook: loaded rulebook dict

    Returns:
        (adjusted_queue, list_of_adjustments)
    """
    hierarchy = rulebook.get('sizing_hierarchy', {})
    registry = rulebook.get('thesis_tag_registry', {})
    adjustments: List[PreflightAdjustment] = []

    for tag, current_weight, cap in breaches:
        excess = current_weight - cap
        if excess <= 0:
            continue

        # Get trim order for this tag (or fall back to alphabetical)
        tag_hierarchy = hierarchy.get(tag, {})
        trim_order = tag_hierarchy.get('trim_order', [])

        # Only trim tickers that are in the queue AND share this tag
        trimmable = [t for t in trim_order if t in queue and tag in registry.get(t, [])]
        if not trimmable:
            # Fall back: trim any queue ticker with this tag, smallest first
            trimmable = sorted(
                [t for t in queue if tag in registry.get(t, [])],
                key=lambda t: queue[t]
            )

        remaining_excess = excess
        for ticker in trimmable:
            if remaining_excess <= 0:
                break
            original = queue[ticker]
            # Don't trim below the coherence floor ratio of original
            floor_ratio = tag_hierarchy.get('coherence_floor_ratio', 0.65)
            min_weight = original * floor_ratio
            max_trim = original - min_weight
            trim_amount = min(remaining_excess, max_trim)
            if trim_amount > 0:
                new_weight = original - trim_amount
                queue[ticker] = new_weight
                remaining_excess -= trim_amount
                adjustments.append(PreflightAdjustment(
                    ticker=ticker,
                    original_weight=original,
                    adjusted_weight=new_weight,
                    rule=f"{tag} cap resolution, cap={cap:.0%}",
                    component='CONCENTRATION',
                ))

    return queue, adjustments


def check_sizing_coherence(
    queue: Dict[str, float],
    rulebook: dict,
) -> Tuple[Dict[str, float], List[PreflightAdjustment]]:
    """
    Validate sizing coherence for positions sharing thesis tags.

    When two positions share a tag and are in the deployment queue,
    ensures their relative sizing respects hierarchy rules. If the
    higher-trim-priority position is sized larger than the lower
    priority one (which should be the anchor), auto-adjust.

    Returns:
        (adjusted_queue, list_of_coherence_adjustments)
    """
    hierarchy = rulebook.get('sizing_hierarchy', {})
    registry = rulebook.get('thesis_tag_registry', {})
    adjustments: List[PreflightAdjustment] = []

    for tag, tag_rules in hierarchy.items():
        trim_order = tag_rules.get('trim_order', [])
        if len(trim_order) < 2:
            continue

        # Find queue tickers that share this tag
        queue_tickers_in_tag = [t for t in trim_order if t in queue]
        if len(queue_tickers_in_tag) < 2:
            continue

        # The last ticker in trim_order is the anchor (trimmed last = highest priority)
        # Earlier tickers should not be sized larger than later ones
        for i in range(len(queue_tickers_in_tag) - 1):
            earlier = queue_tickers_in_tag[i]      # trim first = lower anchor priority
            later = queue_tickers_in_tag[i + 1]    # trim last = higher anchor priority
            if queue[earlier] > queue[later]:
                # Earlier ticker is sized larger than the anchor — reduce it
                original = queue[earlier]
                adjusted = queue[later]  # Cap at the anchor's size
                queue[earlier] = adjusted
                adjustments.append(PreflightAdjustment(
                    ticker=earlier,
                    original_weight=original,
                    adjusted_weight=adjusted,
                    rule=f"coherence alignment, shared thesis {earlier}/{later} ({tag})",
                    component='COHERENCE',
                ))

    return queue, adjustments


# ---------------------------------------------------------------------------
# 4. CDF Signal Validation (read-only)
# ---------------------------------------------------------------------------

def validate_cdf_signals(
    queue_tickers: List[str],
    cdf_multipliers: Dict[str, float],
) -> List[str]:
    """
    Read-only check: flag any queue tickers with CDF decay below 1.0.
    PAIE does not override CDF — just reports the status.
    """
    alerts = []
    for ticker in queue_tickers:
        mult = cdf_multipliers.get(ticker, 1.0)
        if mult < 1.0:
            alerts.append(f"{ticker}: CDF decay active ({mult:.2f})")
    return alerts


# ---------------------------------------------------------------------------
# 5. Rule Conflict Detection (Resolution C)
# ---------------------------------------------------------------------------

def detect_rule_conflicts(
    queue: Dict[str, float],
    breaches_remaining: List[Tuple[str, float, float]],
) -> List[str]:
    """
    Detect unresolvable rule conflicts — cases where cap breaches remain
    after all auto-resolution attempts. These are Resolution C conditions
    that require engineering intervention.
    """
    conflicts = []
    for tag, weight, cap in breaches_remaining:
        if weight > cap:
            conflicts.append(
                f"UNRESOLVED: {tag} at {weight:.1%} exceeds cap {cap:.1%}. "
                f"No hierarchy rule could fully resolve. Engineering amendment required."
            )
    return conflicts


# ---------------------------------------------------------------------------
# Master Orchestrator: deployment_preflight()
# ---------------------------------------------------------------------------

def deployment_preflight(
    queue: Dict[str, float],
    book: Dict[str, float],
    nav: float,
    regime_score: float,
    cdf_multipliers: Optional[Dict[str, float]] = None,
    rulebook_path: str = _RULEBOOK_PATH,
) -> PreflightLog:
    """
    Master pre-flight check. Called before any deployment queue executes.

    Args:
        queue: {ticker: target_weight} of pending deployment targets
        book: {ticker: current_nav_weight} of existing portfolio
        nav: current portfolio NAV
        regime_score: current ARAS composite score
        cdf_multipliers: {ticker: cdf_decay_multiplier} (optional, for signal check)
        rulebook_path: path to rulebook.json

    Returns:
        PreflightLog with status CLEARED | ADJUSTED | BLOCKED
    """
    if cdf_multipliers is None:
        cdf_multipliers = {}

    rulebook = _load_rulebook(rulebook_path)
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    log = PreflightLog(timestamp=now, trigger_score=regime_score)

    # Work on a mutable copy of the queue
    adjusted_queue = dict(queue)

    # Step 1: Project post-deployment thesis concentration
    projected_book = dict(book)
    for ticker, weight in adjusted_queue.items():
        projected_book[ticker] = projected_book.get(ticker, 0.0) + weight

    concentration = calculate_thesis_concentration(projected_book, rulebook)
    log.thesis_concentrations = concentration

    # Step 2: Check for cap breaches
    breaches = check_cap_breaches(concentration, rulebook)
    log.cap_breaches = [f"{tag}: {w:.1%} > {c:.1%}" for tag, w, c in breaches]

    # Step 3: Auto-resolve cap breaches per rulebook
    cap_adjustments: List[PreflightAdjustment] = []
    if breaches:
        adjusted_queue, cap_adjustments = resolve_cap_breaches(
            adjusted_queue, breaches, book, rulebook
        )
        log.record_adjustments(cap_adjustments)

    # Step 4: Sizing coherence check and auto-resolution
    adjusted_queue, coherence_adjs = check_sizing_coherence(adjusted_queue, rulebook)
    log.record_coherence(coherence_adjs)

    # Step 5: CDF signal validation (read-only)
    cdf_alerts = validate_cdf_signals(list(adjusted_queue.keys()), cdf_multipliers)
    log.record_cdf_status(cdf_alerts)

    # Step 6: Re-check for unresolvable conflicts after adjustments
    post_adjust_book = dict(book)
    for ticker, weight in adjusted_queue.items():
        post_adjust_book[ticker] = post_adjust_book.get(ticker, 0.0) + weight
    post_concentration = calculate_thesis_concentration(post_adjust_book, rulebook)
    post_breaches = check_cap_breaches(post_concentration, rulebook)
    conflicts = detect_rule_conflicts(adjusted_queue, post_breaches)

    if conflicts:
        log.conflicts = conflicts
        log.set_status('BLOCKED')
        # Log the block
        append_to_log(SessionLogEntry(
            timestamp=now,
            action_type='PAIE_BLOCKED',
            triggering_module='PAIE',
            triggering_signal=f"Unresolvable rule conflicts: {'; '.join(conflicts)}",
            ticker='PORTFOLIO',
        ))
        return log

    # Determine final status
    all_adjs = log.all_adjustments
    if all_adjs:
        log.set_status('ADJUSTED')
        for adj in all_adjs:
            append_to_log(SessionLogEntry(
                timestamp=now,
                action_type='PAIE_ADJUSTED',
                triggering_module='PAIE',
                triggering_signal=f"{adj.ticker}: {adj.original_weight:.2%} -> {adj.adjusted_weight:.2%} [{adj.rule}]",
                ticker=adj.ticker,
            ))
    else:
        log.set_status('CLEARED')
        append_to_log(SessionLogEntry(
            timestamp=now,
            action_type='PAIE_CLEARED',
            triggering_module='PAIE',
            triggering_signal=f"All checks passed. Score={regime_score:.2f}",
            ticker='PORTFOLIO',
        ))

    # Update the queue in-place with adjusted weights
    queue.clear()
    queue.update(adjusted_queue)

    return log


# ---------------------------------------------------------------------------
# EOD Snapshot Formatter
# ---------------------------------------------------------------------------

def format_paie_section(log: PreflightLog) -> str:
    """
    Format PAIE pre-flight log for EOD snapshot inclusion.
    Per PAIE FSD v1.0 §5 — fixed format, read-only for PM.
    """
    lines = [
        "══════════════════════════════════════════════════════",
        "PAIE — PRE-FLIGHT EXECUTION LOG",
        f"Trigger:        COMPOSITE = {log.trigger_score:.2f}",
        f"Status:         {log.status}",
        f"Executed:       {log.timestamp}",
        "══════════════════════════════════════════════════════",
        "",
        "THESIS CONCENTRATION (POST-DEPLOYMENT)",
    ]

    for tag, weight in sorted(log.thesis_concentrations.items(), key=lambda x: -x[1]):
        # Find cap from any breach info or use default
        cap_str = ""
        for breach in log.cap_breaches:
            if tag in breach:
                cap_str = f"  BREACH"
                break
        if not cap_str:
            cap_str = "  OK"
        lines.append(f"  {tag:<25} {weight:>6.1%}{cap_str}")

    if log.all_adjustments:
        lines.append("")
        lines.append("SIZING ADJUSTMENTS")
        for adj in log.all_adjustments:
            lines.append(
                f"  {adj.ticker}: {adj.original_weight:.2%} → {adj.adjusted_weight:.2%}  [{adj.rule}]"
            )

    if log.cdf_alerts:
        lines.append("")
        lines.append("CDF SIGNAL STATUS")
        for alert in log.cdf_alerts:
            lines.append(f"  {alert}")
    else:
        lines.append("")
        lines.append("CDF SIGNAL STATUS")
        lines.append("  All positions CLEAN")

    if log.conflicts:
        lines.append("")
        lines.append("CONFLICTS (BLOCKED)")
        for conflict in log.conflicts:
            lines.append(f"  {conflict}")

    lines.append("══════════════════════════════════════════════════════")
    return "\n".join(lines)
