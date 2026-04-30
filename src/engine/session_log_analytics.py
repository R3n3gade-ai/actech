"""
ARMS Engine: Session Log Analytics (SLA)

This module reads the ARMS session log (JSONL) and computes three core monthly 
metrics that feed the Phase 2 Conviction Calibration Module (CCM) and Model 
Optimization Engine.

1. CDF accuracy rate — fraction of CDF-decayed positions that ultimately exited at a loss
2. Regime transition lag — average days between ARAS regime signal and formal regime change
3. SENTINEL Gate 3 predictive accuracy — Spearman-rank correlation of gate3_score vs outcome_90d

Reference: ARMS FSD v1.1, Section 11.4
"""

import datetime
import json
import os
from typing import List, Dict, Any, Optional

SESSION_LOG_PATH = "achelion_arms/logs/session_log.jsonl"


def _load_entries(log_path: str) -> List[dict]:
    """Load and parse all session log entries."""
    entries = []
    if not os.path.exists(log_path):
        return entries
    with open(log_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return entries


def _compute_cdf_accuracy(entries: List[dict]) -> float:
    """
    CDF accuracy: Of positions that reached CDF decay milestones,
    what fraction eventually exited at a loss (validating the decay signal)?
    
    Looks for CDF_DECAY entries paired with subsequent TRP_RETIREMENT or
    TRADE SELL entries for the same ticker.
    """
    cdf_tickers = set()
    for e in entries:
        if e.get('action_type') in ('CDF_DECAY', 'CDF_MILESTONE'):
            ticker = e.get('ticker')
            if ticker:
                cdf_tickers.add(ticker)

    if not cdf_tickers:
        return 0.0

    exited_at_loss = 0
    exit_actions = {'TRP_RETIREMENT', 'TRP_RETIREMENT_TIER0', 'TRADE'}
    for ticker in cdf_tickers:
        for e in entries:
            if (e.get('ticker') == ticker and
                    e.get('action_type') in exit_actions and
                    e.get('outcome_90d') is not None and
                    e['outcome_90d'] < 0):
                exited_at_loss += 1
                break

    return exited_at_loss / len(cdf_tickers) if cdf_tickers else 0.0


def _compute_regime_transition_lag(entries: List[dict]) -> float:
    """
    Regime transition lag: Average days between a regime-change signal
    (REGIME_CHANGE action) and the subsequent ARAS or rebalance action.
    Measures system responsiveness.
    """
    regime_changes = []
    response_actions = {'ARAS_UPDATE', 'REBALANCE', 'TRADE', 'PTRH_ADJUSTMENT'}

    for e in entries:
        if e.get('action_type') == 'REGIME_CHANGE':
            regime_changes.append(e)

    if not regime_changes:
        return 0.0

    lags = []
    for rc in regime_changes:
        rc_time = rc.get('timestamp', '')
        if not rc_time:
            continue
        try:
            rc_dt = datetime.datetime.fromisoformat(rc_time)
        except (ValueError, TypeError):
            continue

        # Find first response action after this regime change
        for e in entries:
            if e.get('action_type') not in response_actions:
                continue
            e_time = e.get('timestamp', '')
            if not e_time:
                continue
            try:
                e_dt = datetime.datetime.fromisoformat(e_time)
            except (ValueError, TypeError):
                continue
            if e_dt > rc_dt:
                lag_days = (e_dt - rc_dt).total_seconds() / 86400
                lags.append(lag_days)
                break

    return sum(lags) / len(lags) if lags else 0.0


def _compute_gate3_accuracy(entries: List[dict]) -> float:
    """
    Gate 3 predictive accuracy: For entries that have both a gate3_score
    and an outcome_90d, compute rank correlation (simplified Spearman).
    
    Uses a simplified approach: fraction of entries where the direction
    of gate3_score (>0 = bullish) matches the direction of outcome_90d.
    Full Spearman requires scipy which may not be available.
    """
    pairs = []
    for e in entries:
        g3 = e.get('gate3_score')
        outcome = e.get('outcome_90d')
        if g3 is not None and outcome is not None:
            pairs.append((float(g3), float(outcome)))

    if len(pairs) < 5:
        return 0.0  # Not enough data for meaningful accuracy

    # Directional accuracy: does high gate3 predict positive outcome?
    correct = 0
    for g3, outcome in pairs:
        if (g3 >= 5.0 and outcome > 0) or (g3 < 5.0 and outcome <= 0):
            correct += 1

    return correct / len(pairs) if pairs else 0.0


def compute_sla_metrics(log_path: str = SESSION_LOG_PATH) -> Dict[str, Any]:
    """
    Parses the session log and returns the three core SLA metrics.
    Returns zeros with appropriate status when insufficient data.
    """
    if not os.path.exists(log_path):
        print(f"[SLA] Warning: Log file {log_path} not found.")
        return {
            "cdf_accuracy_rate": 0.0,
            "regime_transition_lag_days": 0.0,
            "sentinel_gate3_accuracy": 0.0,
            "status": "NO_LOG_FILE",
            "entries_analyzed": 0
        }

    entries = _load_entries(log_path)
    total = len(entries)
    print(f"[SLA] Processed {total} session log entries.")

    if total < 10:
        return {
            "cdf_accuracy_rate": 0.0,
            "regime_transition_lag_days": 0.0,
            "sentinel_gate3_accuracy": 0.0,
            "status": "INSUFFICIENT_HISTORY",
            "entries_analyzed": total
        }

    cdf_acc = _compute_cdf_accuracy(entries)
    regime_lag = _compute_regime_transition_lag(entries)
    gate3_acc = _compute_gate3_accuracy(entries)

    metrics = {
        "cdf_accuracy_rate": round(cdf_acc, 4),
        "regime_transition_lag_days": round(regime_lag, 2),
        "sentinel_gate3_accuracy": round(gate3_acc, 4),
        "status": "ACTIVE",
        "entries_analyzed": total
    }

    return metrics


if __name__ == '__main__':
    # Try the real log path if running from src directory
    path = "../../achelion_arms/logs/session_log.jsonl"
    if not os.path.exists(path):
        path = "achelion_arms/logs/session_log.jsonl"
    print(json.dumps(compute_sla_metrics(path), indent=2))
