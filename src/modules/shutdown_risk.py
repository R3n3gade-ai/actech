"""
ARMS Engine: Shutdown Risk

ARAS Sub-Module evaluating extreme systemic shutdown risks including:
  - US government shutdown / debt ceiling deadlines
  - Federal Reserve blackout periods
  - Options expiration dates (quad witching)
  - Market holidays with reduced liquidity
  - Known geopolitical event dates (elections, referendums)

Maintains a forward-looking event calendar and computes days-to-event
risk levels that feed into ARAS posture and PTRH sizing.

Reference: THB v4.0, Section 10
"""
import datetime
from dataclasses import dataclass
from typing import List, Optional, Tuple

@dataclass
class ShutdownEvent:
    name: str
    date: datetime.date
    severity: str  # LOW | MEDIUM | HIGH | CRITICAL
    category: str  # FISCAL | MONETARY | EXPIRATION | GEOPOLITICAL

@dataclass
class ShutdownRiskSignal:
    status: str          # CLEAR | WATCH | ELEVATED | CRITICAL
    days_to_event: int
    nearest_event: Optional[str]
    active_risks: List[str]
    detail: str

# Forward-looking macro event calendar
# These are updated periodically via engineering process or data feed
_EVENT_CALENDAR: List[ShutdownEvent] = [
    # 2026 known events
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 1, 28), "MEDIUM", "MONETARY"),
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 3, 18), "MEDIUM", "MONETARY"),
    ShutdownEvent("Quad Witching", datetime.date(2026, 3, 20), "MEDIUM", "EXPIRATION"),
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 5, 6), "MEDIUM", "MONETARY"),
    ShutdownEvent("Quad Witching", datetime.date(2026, 6, 19), "MEDIUM", "EXPIRATION"),
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 6, 17), "MEDIUM", "MONETARY"),
    ShutdownEvent("Independence Day (early close)", datetime.date(2026, 7, 2), "LOW", "FISCAL"),
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 7, 29), "MEDIUM", "MONETARY"),
    ShutdownEvent("Quad Witching", datetime.date(2026, 9, 18), "MEDIUM", "EXPIRATION"),
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 9, 16), "MEDIUM", "MONETARY"),
    ShutdownEvent("US Midterm Elections", datetime.date(2026, 11, 3), "HIGH", "GEOPOLITICAL"),
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 11, 4), "MEDIUM", "MONETARY"),
    ShutdownEvent("Quad Witching", datetime.date(2026, 12, 18), "MEDIUM", "EXPIRATION"),
    ShutdownEvent("Fed FOMC Meeting", datetime.date(2026, 12, 16), "MEDIUM", "MONETARY"),
    # Fiscal deadlines (updated as legislation progresses)
    ShutdownEvent("US Debt Ceiling Review", datetime.date(2026, 9, 30), "HIGH", "FISCAL"),
    ShutdownEvent("US Fiscal Year End", datetime.date(2026, 9, 30), "HIGH", "FISCAL"),
]

# Severity → days-to-event thresholds for status escalation
_ESCALATION_MAP = {
    'CRITICAL': 14,  # Alert when <= 14 days away
    'HIGH': 10,
    'MEDIUM': 5,
    'LOW': 2,
}

def run_shutdown_risk_check(
    reference_date: Optional[datetime.date] = None,
    additional_events: Optional[List[ShutdownEvent]] = None,
) -> ShutdownRiskSignal:
    """
    Evaluate forward-looking shutdown/event risk.
    
    Args:
        reference_date: Date to evaluate from (default: today).
        additional_events: Extra events to consider beyond the built-in calendar.
    
    Returns:
        ShutdownRiskSignal with risk level and nearest event details.
    """
    today = reference_date or datetime.date.today()
    
    events = list(_EVENT_CALENDAR)
    if additional_events:
        events.extend(additional_events)
    
    # Filter to future events only
    future_events = [(e, (e.date - today).days) for e in events if e.date >= today]
    future_events.sort(key=lambda x: x[1])
    
    if not future_events:
        return ShutdownRiskSignal(
            status="CLEAR",
            days_to_event=99,
            nearest_event=None,
            active_risks=[],
            detail="No upcoming macro events in calendar.",
        )
    
    nearest, nearest_days = future_events[0]
    
    # Collect all active risks (within their escalation window)
    active_risks = []
    worst_status = "CLEAR"
    status_order = {"CLEAR": 0, "WATCH": 1, "ELEVATED": 2, "CRITICAL": 3}
    
    for event, days_away in future_events:
        threshold = _ESCALATION_MAP.get(event.severity, 5)
        if days_away <= threshold:
            risk_label = f"{event.name} in {days_away}d ({event.severity})"
            active_risks.append(risk_label)
            
            # Determine status escalation
            if days_away <= 2 and event.severity in ('HIGH', 'CRITICAL'):
                ev_status = "CRITICAL"
            elif days_away <= 5 and event.severity in ('HIGH', 'CRITICAL'):
                ev_status = "ELEVATED"
            elif days_away <= threshold:
                ev_status = "WATCH"
            else:
                ev_status = "CLEAR"
            
            if status_order.get(ev_status, 0) > status_order.get(worst_status, 0):
                worst_status = ev_status
    
    if not active_risks:
        detail = f"Next event: {nearest.name} in {nearest_days} days. No events within alert window."
    else:
        detail = f"{len(active_risks)} active risk(s): {'; '.join(active_risks[:3])}"
    
    return ShutdownRiskSignal(
        status=worst_status,
        days_to_event=nearest_days,
        nearest_event=nearest.name,
        active_risks=active_risks,
        detail=detail,
    )
