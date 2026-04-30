"""
ARMS Module: Customer Dependency Map (CDM) Engine

This module provides the cross-sector signal propagation layer for ARMS.
It scans incoming data (news, filings) for named entities defined in the
dependency map and propagates alerts to all positions that depend on those
entities.

This is the "data layer" for the Thesis Dependency Checker (TDC).

"The system catches what the PM should not have to catch."

Reference: ARMS Module Specification — CDM + TDC | Addendum 2 to FSD v1.1
"""

import datetime
import json
import os
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Literal

# --- Internal Imports ---
from config.position_dependency_map import POSITION_DEPENDENCIES, ALERT_SENSITIVITY_MAPPING
from reporting.audit_log import SessionLogEntry, append_to_log
from engine.bridge_paths import bridge_path

# --- Data Structures ---

@dataclass
class NewsItem:
    """Represents a single piece of incoming data to be scanned."""
    source: str  # e.g., 'SEC_EDGAR', 'NewsAPI'
    headline: str
    content: str
    timestamp: str # ISO format
    entities: List[str]  # Pre-processed named entities found in the content
    event_type: Literal[
        'LEGAL_RULING', 'REGULATORY_FILING', 'EARNINGS_WARNING', 'MA_ANNOUNCEMENT',
        'INSIDER_SALE', 'LEADERSHIP_CHANGE', 'CREDIT_EVENT', 'PARTNERSHIP_CHANGE',
        'PRODUCT_DELAY', 'POSITIVE_DEVELOPMENT', 'UNKNOWN'
    ]

@dataclass
class CdmAlert:
    """Represents a single alert propagated to a dependent position."""
    ticker: str
    triggering_entity: str
    event_type: str
    severity: Literal['CRITICAL', 'HIGH', 'MEDIUM', 'LOW']
    headline: str
    source_item: NewsItem
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- CDM Engine Logic ---

def _find_dependent_tickers(entity: str) -> List[str]:
    """
    Finds all tickers in the map that depend on the given entity.
    Includes smart matching for common name aliases (e.g., Google/Alphabet).
    """
    affected_tickers = []
    normalized_entity = entity.lower()
    
    # Simple alias mapping
    aliases = {
        'google': 'alphabet',
        'alphabet': 'google',
        'aws': 'amazon',
        'amazon': 'aws'
    }
    
    match_candidates = [normalized_entity]
    if normalized_entity in aliases:
        match_candidates.append(aliases[normalized_entity])
    
    for ticker, dependencies in POSITION_DEPENDENCIES.items():
        # Combine all dependency lists for a full scan
        all_deps = (
            dependencies.get('primary_demand_drivers', []) +
            dependencies.get('thesis_enablers', []) +
            dependencies.get('regulatory_counterparties', [])
        )
        
        # Check if any candidate matches any dependency
        if any(any(c in dep.lower() for c in match_candidates) for dep in all_deps):
            affected_tickers.append(ticker)
            
    return list(set(affected_tickers)) # Remove duplicates

def run_cdm_scan(news_items: List[NewsItem]) -> List[CdmAlert]:
    """
    Scans a list of news items and generates CDM alerts for dependent positions.
    Propagates events based on position-specific sensitivity.
    """
    all_alerts = []
    
    for item in news_items:
        # Per spec, positive/unknown events do not generate alerts for propagation
        if item.event_type in ['POSITIVE_DEVELOPMENT', 'UNKNOWN']:
            continue 

        for entity in item.entities:
            dependent_tickers = _find_dependent_tickers(entity)
            
            for ticker in dependent_tickers:
                sensitivity = POSITION_DEPENDENCIES[ticker]['alert_sensitivity']
                triggering_events = ALERT_SENSITIVITY_MAPPING.get(sensitivity, [])
                
                if item.event_type in triggering_events:
                    # Severity mapping logic:
                    # 1. Start with sensitivity level
                    severity = sensitivity 
                    # 2. Per spec: LEGAL_RULING is always CRITICAL
                    if item.event_type == 'LEGAL_RULING':
                        severity = 'CRITICAL'

                    alert = CdmAlert(
                        ticker=ticker,
                        triggering_entity=entity,
                        event_type=item.event_type,
                        severity=severity, # type: ignore
                        headline=item.headline,
                        source_item=item
                    )
                    all_alerts.append(alert)
                    
                    # Log the propagation event to the audit trail
                    append_to_log(SessionLogEntry(
                        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        action_type='CDM_PROPAGATION',
                        triggering_module='CDM',
                        triggering_signal=f"{item.event_type} at {entity} affects {ticker}.",
                        ticker=ticker
                    ))
                    
                    print(f"[CDM] Alert Generated: {ticker} <-- {entity} ({item.event_type})")

    # Persist CDM alerts to queryable JSONL log
    _persist_cdm_alerts(all_alerts)

    return all_alerts


CDM_ALERT_LOG_PATH = bridge_path('ARMS_CDM_ALERT_LOG', 'cdm_alert_log.jsonl')


def _persist_cdm_alerts(alerts: List[CdmAlert]) -> None:
    """Append CDM alerts to a persistent JSONL log for auditability."""
    if not alerts:
        return
    os.makedirs(os.path.dirname(CDM_ALERT_LOG_PATH), exist_ok=True)
    with open(CDM_ALERT_LOG_PATH, 'a', encoding='utf-8') as f:
        for alert in alerts:
            entry = {
                'ticker': alert.ticker,
                'triggering_entity': alert.triggering_entity,
                'event_type': alert.event_type,
                'severity': alert.severity,
                'headline': alert.headline,
                'timestamp': alert.timestamp,
                'source': alert.source_item.source if alert.source_item else 'UNKNOWN',
            }
            f.write(json.dumps(entry) + '\n')
    print(f"[CDM] {len(alerts)} alert(s) persisted to {CDM_ALERT_LOG_PATH}")

if __name__ == '__main__':
    print("ARMS CDM Module Active (Simulation Mode)")
    
    # Simulate the Google Antitrust event for MU
    mock_item = NewsItem(
        source='NewsAPI',
        headline='DOJ Pursuing Structural Remedies Against Google',
        content='The DOJ is considering a breakup of Google following antitrust ruling...',
        timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        entities=['Google'],
        event_type='LEGAL_RULING'
    )
    
    alerts = run_cdm_scan([mock_item])
    for a in alerts:
        print(f"Propagated to: {a.ticker} (Severity: {a.severity})")
