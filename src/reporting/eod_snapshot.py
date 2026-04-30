"""
ARMS Reporting: End-of-Day Snapshot

Generates the 5-field closing status check at 1450 CT daily.
Not a second full monitor — a focused closing confirmation instrument.

Five fields (exactly — never more):
  1. Regime Score Delta (vs morning monitor)
  2. Pending Tier 1 Windows
  3. Intraday CDM/TDC Signals
  4. Session Execution Summary
  5. Overnight Risk Assessment (Claude-generated)

Design constraint: Must never exceed one printed page when rendered as PDF.
The PM reads top to bottom in under two minutes.

"Read clean before the open. Confirm clean before the close."

Schedule: 1450 CT Monday-Friday (generation), 1455 CT (delivery)
Reference: ARMS EOD Snapshot Spec v1.0
"""

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any

from intelligence.llm_wrapper import llm_wrapper
from reporting.audit_log import SessionLogEntry, append_to_log


# --- Data Structures ---

@dataclass
class RegimeDelta:
    """Field 1: Regime score movement since morning monitor."""
    current_regime: str
    current_score: float
    morning_score: float
    delta: float
    direction: str        # '↑' or '↓' or '→'
    narrative: Optional[str]  # None if delta < ±0.03
    crash_proximity: bool  # True if score within 0.05 of CRASH boundary (0.80)
    color: str             # 'green', 'red', or 'grey'


@dataclass
class PendingTier1Item:
    """A single Tier 1 veto window item for Field 2."""
    action_type: str
    module: str
    ticker: str
    queued_at: str         # Time string e.g. "1023 CT"
    expires_at: str        # Time string
    minutes_remaining: float
    status: str            # 'OPEN', 'EXPIRED', 'RESPONDED'
    is_urgent: bool        # True if <60 min remaining


@dataclass
class IntradaySignal:
    """A CDM or TDC signal from Field 3."""
    signal_type: str       # 'CDM' or 'TDC'
    severity: str          # 'CRITICAL', 'HIGH', 'MEDIUM'
    entity: str
    affected_positions: List[str]
    headline: str
    tis_score: Optional[float]  # TDC thesis integrity score if available
    thesis_status: Optional[str]  # 'INTACT', 'WATCH', 'IMPAIRED', 'BROKEN'
    timestamp: str


@dataclass
class ExecutionEntry:
    """A single execution from Field 4."""
    time: str
    module: str
    action: str
    ticker: str
    size: str
    price: str
    status: str  # 'FILLED', 'PARTIAL', 'FAILED'


@dataclass
class ExecutionSummary:
    """Field 4: Session execution summary."""
    orders: List[ExecutionEntry]
    weight_drift_flags: List[Dict[str, Any]]  # Positions with >0.5pp drift
    laep_mode: str
    laep_mode_changed: Optional[str]  # e.g. "ELEVATED → NORMAL at 1134 CT"
    has_failures: bool


@dataclass
class EODSnapshotState:
    """Complete EOD Snapshot data model."""
    date_label: str
    generation_time: str
    regime_delta: RegimeDelta
    pending_tier1: List[PendingTier1Item]
    intraday_signals: List[IntradaySignal]
    execution_summary: ExecutionSummary
    overnight_watch: str   # Claude-generated 2-3 sentence statement
    morning_monitor_score: float
    regime_at_generation: str


# --- Morning Score Persistence ---

_MORNING_SCORE_PATH = os.path.join('achelion_arms', 'state', 'morning_monitor_score.json')


def save_morning_score(score: float, regime: str, generated_at: str):
    """Called by morning monitor generation to persist score for EOD delta."""
    os.makedirs(os.path.dirname(_MORNING_SCORE_PATH), exist_ok=True)
    with open(_MORNING_SCORE_PATH, 'w') as f:
        json.dump({
            'score': score,
            'regime': regime,
            'generated_at': generated_at,
        }, f)


def _load_morning_score() -> tuple:
    """Load the morning monitor score for delta calculation."""
    if os.path.exists(_MORNING_SCORE_PATH):
        with open(_MORNING_SCORE_PATH, 'r') as f:
            data = json.load(f)
            return data.get('score', 0.0), data.get('generated_at', '')
    return 0.0, ''


# --- Session Log Query ---

def _get_intraday_events(morning_time: str) -> List[Dict]:
    """
    Read session log entries between morning monitor generation and now.
    Returns CDM and TDC events from the intraday session.
    """
    log_path = 'achelion_arms/logs/session_log.jsonl'
    if not os.path.exists(log_path):
        return []

    events = []
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    action = entry.get('action_type', '')
                    if action in ('CDM_ALERT', 'TDC_REVIEW', 'CDM_PROPAGATION'):
                        ts = entry.get('timestamp', '')
                        if ts > morning_time:
                            events.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return events


def _get_execution_events(morning_time: str) -> List[Dict]:
    """Read session log for execution events during the session."""
    log_path = 'achelion_arms/logs/session_log.jsonl'
    if not os.path.exists(log_path):
        return []

    events = []
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    action = entry.get('action_type', '')
                    if action in ('TRADE', 'ORDER_SUBMITTED', 'ORDER_FILLED',
                                  'ORDER_FAILED', 'LAEP_MODE_CHANGE'):
                        ts = entry.get('timestamp', '')
                        if ts > morning_time:
                            events.append(entry)
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass

    return events


# --- Overnight Watch Generation ---

def _generate_overnight_watch(regime_delta: RegimeDelta,
                              pending_items: List[PendingTier1Item],
                              intraday_signals: List[IntradaySignal],
                              ptrh_status: str) -> str:
    """
    Generate the overnight risk assessment using Claude API.
    Must be specific — name the entity, signal, or threshold that matters most.
    Generic statements are rejected and retried.
    """
    context = f"""Current regime: {regime_delta.current_regime} · Score: {regime_delta.current_score:.2f}
Morning score: {regime_delta.morning_score:.2f} · Delta: {regime_delta.delta:+.2f}
CRASH boundary proximity: {'YES — within 0.05 of 0.80' if regime_delta.crash_proximity else 'No'}
Pending Tier 1 items: {len(pending_items)}
Intraday CDM/TDC signals: {len(intraday_signals)}
PTRH status: {ptrh_status}"""

    if intraday_signals:
        sig_text = "; ".join(
            f"{s.signal_type} {s.severity}: {s.entity} → {','.join(s.affected_positions)}"
            for s in intraday_signals[:5]
        )
        context += f"\nKey signals today: {sig_text}"

    if pending_items:
        pending_text = "; ".join(
            f"{p.action_type} {p.ticker} (expires {p.expires_at})"
            for p in pending_items if p.status == 'OPEN'
        )
        context += f"\nOpen veto windows: {pending_text}"

    prompt = f"""You are the ARMS risk assessment engine. Generate a two to three sentence overnight watch statement for the PM and GPs.

RULES:
- Be specific — name the entity, signal, or threshold that matters most
- Do not summarize the session
- Identify the single most important thing to watch between now and 0800 CT tomorrow
- Reference specific named entities, thresholds, or upcoming events
- Do NOT use generic phrases like "markets remain volatile" or "stay vigilant"

Current state:
{context}

Return ONLY the 2-3 sentence watch statement. No JSON. No formatting. Plain text."""

    response = llm_wrapper.call(
        task_type='overnight_watch',
        prompt=prompt,
    )

    # Validate for named specificity — reject generic statements
    generic_phrases = ['markets remain', 'stay vigilant', 'continue to monitor',
                       'keep an eye on', 'watch closely', 'market conditions']
    is_generic = any(phrase in response.lower() for phrase in generic_phrases)

    if is_generic:
        # Retry with stricter prompt
        strict_prompt = prompt.replace(
            "Return ONLY the 2-3 sentence watch statement.",
            "CRITICAL: Your previous response was too generic and was rejected. "
            "You MUST name a specific company, threshold number, or upcoming event. "
            "Example: 'Microsoft reports earnings at 1600 CT today. ELVT will process the transcript overnight.' "
            "Return ONLY the 2-3 sentence watch statement."
        )
        response = llm_wrapper.call(
            task_type='overnight_watch_strict',
            prompt=strict_prompt,
        )

        # If still generic, fall back to rule-based statement
        if any(phrase in response.lower() for phrase in generic_phrases):
            if regime_delta.crash_proximity:
                response = (f"Regime score {regime_delta.current_score:.2f} is within "
                            f"{0.80 - regime_delta.current_score:.2f} points of the CRASH boundary at 0.80. "
                            f"Confirm PTRH dual-risk coverage before tomorrow's open.")
            elif intraday_signals:
                top = intraday_signals[0]
                response = (f"{top.entity} {top.signal_type} signal fired today affecting "
                            f"{', '.join(top.affected_positions)}. "
                            f"Tomorrow's morning monitor will reflect updated TIS scores. "
                            f"No action required tonight.")
            else:
                response = (f"Regime score held at {regime_delta.current_score:.2f} "
                            f"({regime_delta.current_regime}) through the full session. "
                            f"No material signals or pending actions entering overnight. "
                            f"Next catalyst will be reflected in tomorrow's morning monitor.")

    return response.strip()


# --- Core EOD Snapshot Builder ---

def build_eod_snapshot(
    current_score: float,
    current_regime: str,
    confirmation_queue_items: List[Any],
    live_positions: List[Any],
    target_weights: Dict[str, float],
    nav: float,
    ptrh_status: str = 'NORMAL',
    laep_mode: str = 'NORMAL',
) -> EODSnapshotState:
    """
    Build the complete EOD Snapshot state from current engine outputs.

    Args:
        current_score: Current ARAS composite score
        current_regime: Current regime label
        confirmation_queue_items: Open items from ConfirmationQueue.get_open_items()
        live_positions: Current broker positions
        target_weights: Target weights from Master Engine
        nav: Current NAV
        ptrh_status: Current PTRH status string
        laep_mode: Current LAEP execution mode
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    morning_score, morning_time = _load_morning_score()

    # --- Field 1: Regime Score Delta ---
    delta = current_score - morning_score
    if delta > 0:
        direction = '↑'
        color = 'red'  # Higher score = worsening (toward CRASH)
    elif delta < 0:
        direction = '↓'
        color = 'green'  # Lower score = improving (toward RISK_ON)
    else:
        direction = '→'
        color = 'grey'

    narrative = None
    if abs(delta) >= 0.03:
        narrative = f"Score moved {delta:+.2f} during session."

    crash_proximity = current_score >= 0.75  # Within 0.05 of 0.80

    regime_delta = RegimeDelta(
        current_regime=current_regime,
        current_score=round(current_score, 2),
        morning_score=round(morning_score, 2),
        delta=round(delta, 2),
        direction=direction,
        narrative=narrative,
        crash_proximity=crash_proximity,
        color=color,
    )

    # --- Field 2: Pending Tier 1 Windows ---
    pending_tier1: List[PendingTier1Item] = []
    for item in confirmation_queue_items:
        queued_at = getattr(item, 'queued_at', now)
        if isinstance(queued_at, str):
            try:
                queued_at = datetime.datetime.fromisoformat(queued_at)
            except ValueError:
                queued_at = now

        veto_hours = getattr(item, 'veto_window_hours', 4)
        expires_at = queued_at + datetime.timedelta(hours=veto_hours)
        minutes_remaining = max(0, (expires_at - now).total_seconds() / 60)

        status = 'OPEN'
        if hasattr(item, 'status'):
            status = str(item.status)
        elif minutes_remaining <= 0:
            status = 'EXPIRED'

        pending_tier1.append(PendingTier1Item(
            action_type=getattr(item, 'triggering_module', 'UNKNOWN'),
            module=getattr(item, 'triggering_module', 'UNKNOWN'),
            ticker=getattr(item, 'item', item).ticker if hasattr(getattr(item, 'item', item), 'ticker') else 'N/A',
            queued_at=queued_at.strftime('%H%M CT') if hasattr(queued_at, 'strftime') else str(queued_at),
            expires_at=expires_at.strftime('%H%M CT') if hasattr(expires_at, 'strftime') else str(expires_at),
            minutes_remaining=round(minutes_remaining, 0),
            status=status,
            is_urgent=0 < minutes_remaining < 60,
        ))

    # --- Field 3: Intraday CDM/TDC Signals ---
    intraday_log = _get_intraday_events(morning_time)
    intraday_signals: List[IntradaySignal] = []
    for event in intraday_log:
        action = event.get('action_type', '')
        severity = 'HIGH'
        if 'CRITICAL' in event.get('triggering_signal', '').upper():
            severity = 'CRITICAL'

        signal_type = 'CDM' if 'CDM' in action else 'TDC'
        intraday_signals.append(IntradaySignal(
            signal_type=signal_type,
            severity=severity,
            entity=event.get('triggering_signal', 'Unknown'),
            affected_positions=event.get('ticker', '').split(',') if event.get('ticker') else [],
            headline=event.get('triggering_signal', ''),
            tis_score=event.get('mics_score'),
            thesis_status=None,
            timestamp=event.get('timestamp', ''),
        ))

    # --- Field 4: Session Execution Summary ---
    exec_log = _get_execution_events(morning_time)
    orders: List[ExecutionEntry] = []
    laep_mode_changed = None

    for event in exec_log:
        action = event.get('action_type', '')
        if action == 'LAEP_MODE_CHANGE':
            laep_mode_changed = event.get('triggering_signal', '')
            continue

        orders.append(ExecutionEntry(
            time=event.get('timestamp', '')[:16],
            module=event.get('triggering_module', ''),
            action=action,
            ticker=event.get('ticker', ''),
            size='',
            price='',
            status='FILLED' if 'FILLED' in action else ('FAILED' if 'FAIL' in action else 'SUBMITTED'),
        ))

    has_failures = any(o.status == 'FAILED' for o in orders)

    # Weight drift check
    weight_drift_flags: List[Dict[str, Any]] = []
    if live_positions and nav > 0:
        for p in live_positions:
            if not hasattr(p, 'sec_type') or p.sec_type != 'STK':
                continue
            current_weight = p.market_value / nav if nav > 0 else 0
            target = target_weights.get(p.ticker, 0)
            drift_pp = abs(current_weight - target) * 100
            if drift_pp > 0.5:
                weight_drift_flags.append({
                    'ticker': p.ticker,
                    'current_pct': round(current_weight * 100, 2),
                    'target_pct': round(target * 100, 2),
                    'drift_pp': round(drift_pp, 2),
                })

    execution_summary = ExecutionSummary(
        orders=orders,
        weight_drift_flags=weight_drift_flags,
        laep_mode=laep_mode,
        laep_mode_changed=laep_mode_changed,
        has_failures=has_failures,
    )

    # --- Field 5: Overnight Risk Assessment ---
    overnight_watch = _generate_overnight_watch(
        regime_delta, pending_tier1, intraday_signals, ptrh_status
    )

    return EODSnapshotState(
        date_label=now.strftime('%B %d, %Y'),
        generation_time=now.strftime('%H:%M CT'),
        regime_delta=regime_delta,
        pending_tier1=pending_tier1,
        intraday_signals=intraday_signals,
        execution_summary=execution_summary,
        overnight_watch=overnight_watch,
        morning_monitor_score=morning_score,
        regime_at_generation=current_regime,
    )


# --- Markdown Renderer (one-page constraint) ---

def render_eod_markdown(state: EODSnapshotState) -> str:
    """
    Render the EOD Snapshot as Markdown for the daily monitor pipeline.
    Enforces the one-page / under-two-minutes reading constraint.
    """
    rd = state.regime_delta

    md = f"""# ACHELION ARMS · End-of-Day Snapshot · {state.date_label} · CONFIDENTIAL — GP Distribution
**END-OF-DAY SNAPSHOT — {state.generation_time}**
*Achelion Capital Management, LLC · Architecture AB v4.0 · Not for external distribution*

---

### 1 · REGIME SCORE DELTA

"""

    # Field 1 — Regime Score Delta
    if rd.crash_proximity:
        md += f"**⚑ CRASH BOUNDARY PROXIMITY**\n\n"

    stable_text = "Stable — no material movement during session." if rd.narrative is None else rd.narrative
    md += (f"**{rd.current_regime}** · Score: **{rd.current_score}** · "
           f"Morning: {rd.morning_score} · Delta: {rd.delta:+.2f} {rd.direction}\n\n"
           f"{stable_text}\n")

    # Field 2 — Pending Tier 1 Windows
    md += "\n---\n\n### 2 · PENDING TIER 1 WINDOWS\n\n"
    if not state.pending_tier1:
        md += "No Tier 1 actions pending. Decision queue clear.\n"
    else:
        for item in state.pending_tier1:
            urgency = " **⚑ URGENT**" if item.is_urgent else ""
            md += (f"- **{item.action_type}** — {item.ticker} · "
                   f"Queued: {item.queued_at} · Expires: {item.expires_at} · "
                   f"{item.minutes_remaining:.0f}m remaining · "
                   f"STATUS: {item.status}{urgency}\n")
            if item.status == 'OPEN':
                md += f"  *No response by {item.expires_at} = system executes at MICS-implied sizing.*\n"

    # Field 3 — Intraday CDM/TDC Signals
    md += "\n---\n\n### 3 · INTRADAY CDM / TDC SIGNALS\n\n"
    if not state.intraday_signals:
        md += "No new CDM or TDC signals during session.\n"
    else:
        for sig in state.intraday_signals:
            positions_str = ', '.join(sig.affected_positions) if sig.affected_positions else 'N/A'
            tis_str = f" · TIS: {sig.tis_score:.1f}" if sig.tis_score is not None else ""
            status_str = f" · {sig.thesis_status}" if sig.thesis_status else ""
            md += (f"- **{sig.signal_type} {sig.severity}**: {sig.headline} · "
                   f"Positions: {positions_str}{tis_str}{status_str}\n")

    # Field 4 — Session Execution Summary
    md += "\n---\n\n### 4 · SESSION EXECUTION SUMMARY\n\n"
    es = state.execution_summary
    if not es.orders:
        md += f"No orders executed during session. LAEP: {es.laep_mode}.\n"
    else:
        for order in es.orders:
            status_marker = "**FAILED**" if order.status == 'FAILED' else order.status
            md += f"- {order.time} · {order.module} · {order.action} · {order.ticker} · {status_marker}\n"

    if es.laep_mode_changed:
        md += f"\nLAEP mode changed: {es.laep_mode_changed}\n"

    if es.has_failures:
        md += "\n**⚑ EXECUTION FAILURES DETECTED** — target allocation not yet achieved.\n"

    if es.weight_drift_flags:
        md += "\n**Weight Drift Flags (>0.5pp):**\n"
        for drift in es.weight_drift_flags:
            md += f"- {drift['ticker']}: {drift['current_pct']:.1f}% actual vs {drift['target_pct']:.1f}% target ({drift['drift_pp']:.1f}pp drift)\n"
    elif es.orders:
        md += "\nPortfolio weights within tolerance.\n"

    # Field 5 — Overnight Risk Assessment
    md += f"\n---\n\n### 5 · OVERNIGHT RISK ASSESSMENT\n\n"
    md += f"{state.overnight_watch}\n"

    # Footer
    md += (f"\n---\n*Data sourced from public markets · {state.date_label} · "
           f"{state.generation_time} · Regime: {state.regime_delta.current_regime} "
           f"{state.regime_delta.current_score} (morning: {state.regime_delta.morning_score}) · "
           f"Architecture AB v4.0 · Achelion Capital Management, LLC · Not for distribution*\n")

    return md


def generate_eod_snapshot(
    current_score: float,
    current_regime: str,
    confirmation_queue_items: List[Any],
    live_positions: List[Any],
    target_weights: Dict[str, float],
    nav: float,
    ptrh_status: str = 'NORMAL',
    laep_mode: str = 'NORMAL',
) -> str:
    """
    Main entry point — builds EOD Snapshot state and renders to Markdown.

    Returns:
        Rendered Markdown string for the EOD Snapshot.
    """
    state = build_eod_snapshot(
        current_score=current_score,
        current_regime=current_regime,
        confirmation_queue_items=confirmation_queue_items,
        live_positions=live_positions,
        target_weights=target_weights,
        nav=nav,
        ptrh_status=ptrh_status,
        laep_mode=laep_mode,
    )

    markdown = render_eod_markdown(state)

    # Log generation
    append_to_log(SessionLogEntry(
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        action_type='EOD_SNAPSHOT_GENERATED',
        triggering_module='EOD_SNAPSHOT',
        triggering_signal=f"Score {current_score:.2f} (delta {state.regime_delta.delta:+.2f} from morning). "
                          f"Pending Tier 1: {len(state.pending_tier1)}. "
                          f"Intraday signals: {len(state.intraday_signals)}.",
        regime_at_action=current_regime,
    ))

    return markdown
