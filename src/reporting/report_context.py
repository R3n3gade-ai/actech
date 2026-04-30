from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from reporting.audit_log import SessionLogEntry


@dataclass
class ReportContextSummary:
    prior_score_estimate: Optional[float]
    transition_count: int
    trade_count: int
    tdc_review_count: int
    queue_change_count: int


def summarize_recent_session_log(entries: List[dict]) -> ReportContextSummary:
    trades = 0
    tdc_reviews = 0
    queue_changes = 0
    prior_score_estimate = None

    for row in entries:
        action_type = str(row.get('action_type', ''))
        signal = str(row.get('triggering_signal', ''))
        if action_type == 'TRADE':
            trades += 1
        elif action_type in {'TDC_REVIEW', 'TDC_WEEKLY_AUDIT'}:
            tdc_reviews += 1
        elif action_type == 'QUEUE_STATE_CHANGE':
            queue_changes += 1

    return ReportContextSummary(
        prior_score_estimate=prior_score_estimate,
        transition_count=0,
        trade_count=trades,
        tdc_review_count=tdc_reviews,
        queue_change_count=queue_changes,
    )
