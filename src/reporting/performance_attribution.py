"""
ARMS Reporting: Performance Attribution Engine

Attributes every basis point of alpha to the specific ARMS module that
generated the trade signal. Produces LP-defensible track record
documentation showing exactly which cognitive layer drove each decision.

Attribution chain:
  1. Read session_log.jsonl for all TRADE-type entries with triggering_module
  2. Match each trade to its P&L outcome from the execution ledger
  3. Aggregate by module → per-module alpha contribution
  4. Generate attribution report with time-weighted returns

Modules tracked:
  SENTINEL, CDF, PERM, DSHP, ARAS, PDS, TRP, FEM, MICS

Reference: ARMS GP Briefing v1.0, Section 4 — "LP-Defensible Track Record"
Reference: ARMS FSD v1.1, Section 11.4 — Session Log Structure
"""

import datetime
import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from reporting.audit_log import SessionLogEntry

# --- Configuration ---

SESSION_LOG_PATH = 'achelion_arms/logs/session_log.jsonl'
EXECUTION_LEDGER_PATH = 'achelion_arms/state/execution_ledger.jsonl'
ATTRIBUTION_OUTPUT_PATH = 'achelion_arms/state/attribution_report.json'

# All modules that can generate trade signals
ATTRIBUTION_MODULES = [
    'SENTINEL', 'CDF', 'PERM', 'DSHP', 'ARAS', 'PDS',
    'TRP', 'FEM', 'MICS', 'ARES',
]


# --- Data Structures ---

@dataclass
class TradeAttribution:
    """Single trade with its attributed module and P&L."""
    correlation_id: str
    ticker: str
    triggering_module: str
    action: str  # BUY, SELL, etc.
    entry_timestamp: str
    realized_pnl_bps: float = 0.0
    unrealized_pnl_bps: float = 0.0
    is_closed: bool = False


@dataclass
class ModulePerformance:
    """Aggregated performance stats for one ARMS module."""
    module_name: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_pnl_bps: float = 0.0
    avg_pnl_bps: float = 0.0
    win_rate: float = 0.0
    max_gain_bps: float = 0.0
    max_loss_bps: float = 0.0


@dataclass
class AttributionReport:
    """Full attribution report across all modules."""
    generated_at: str
    period_start: str
    period_end: str
    total_trades: int
    total_pnl_bps: float
    module_breakdown: Dict[str, dict]
    top_contributors: List[dict]


# --- Core Logic ---

def _load_session_trades() -> List[dict]:
    """Load all trade-related entries from the session log."""
    trades = []
    if not os.path.exists(SESSION_LOG_PATH):
        return trades

    trade_actions = {
        'TRADE', 'PERM_EXECUTION', 'DSHP_HARVEST', 'DSHP_HARVEST_TIER0',
        'TRP_RETIREMENT', 'TRP_RETIREMENT_TIER0', 'FEM_PAIRED_TRIM',
        'ARAS_EXECUTION', 'PDS_REDUCTION',
    }

    with open(SESSION_LOG_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                if entry.get('action_type') in trade_actions:
                    trades.append(entry)
            except json.JSONDecodeError:
                continue

    return trades


def _load_execution_ledger() -> Dict[str, dict]:
    """Load the execution ledger, keyed by correlation_id."""
    ledger = {}
    if not os.path.exists(EXECUTION_LEDGER_PATH):
        return ledger

    with open(EXECUTION_LEDGER_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                cid = entry.get('correlation_id')
                if cid:
                    ledger[cid] = entry
            except json.JSONDecodeError:
                continue

    return ledger


def build_trade_attributions() -> List[TradeAttribution]:
    """
    Match session log trades to execution ledger P&L data.
    Returns a list of attributed trades.
    """
    session_trades = _load_session_trades()
    execution_ledger = _load_execution_ledger()

    attributions = []
    for trade in session_trades:
        cid = trade.get('correlation_id', '')
        ticker = trade.get('ticker', 'UNKNOWN')
        module = trade.get('triggering_module', 'UNKNOWN')
        timestamp = trade.get('timestamp', '')

        # Match to execution ledger for P&L
        ledger_entry = execution_ledger.get(cid, {})
        realized = ledger_entry.get('realized_pnl_bps', 0.0)
        unrealized = ledger_entry.get('unrealized_pnl_bps', 0.0)
        is_closed = ledger_entry.get('is_closed', False)

        attr = TradeAttribution(
            correlation_id=cid,
            ticker=ticker,
            triggering_module=module,
            action=trade.get('action_type', 'UNKNOWN'),
            entry_timestamp=timestamp,
            realized_pnl_bps=realized,
            unrealized_pnl_bps=unrealized,
            is_closed=is_closed,
        )
        attributions.append(attr)

    return attributions


def compute_module_performance(
    attributions: List[TradeAttribution],
) -> Dict[str, ModulePerformance]:
    """
    Aggregate trade attributions by module to compute per-module performance.
    """
    module_stats: Dict[str, ModulePerformance] = {
        m: ModulePerformance(module_name=m) for m in ATTRIBUTION_MODULES
    }

    for attr in attributions:
        module = attr.triggering_module
        if module not in module_stats:
            module_stats[module] = ModulePerformance(module_name=module)

        mp = module_stats[module]
        pnl = attr.realized_pnl_bps if attr.is_closed else attr.unrealized_pnl_bps
        mp.total_trades += 1
        mp.total_pnl_bps += pnl

        if pnl > 0:
            mp.winning_trades += 1
        elif pnl < 0:
            mp.losing_trades += 1

        if pnl > mp.max_gain_bps:
            mp.max_gain_bps = pnl
        if pnl < mp.max_loss_bps:
            mp.max_loss_bps = pnl

    # Compute derived stats
    for mp in module_stats.values():
        if mp.total_trades > 0:
            mp.avg_pnl_bps = mp.total_pnl_bps / mp.total_trades
            mp.win_rate = mp.winning_trades / mp.total_trades
        else:
            mp.avg_pnl_bps = 0.0
            mp.win_rate = 0.0

    return module_stats


def generate_attribution_report(
    period_start: Optional[str] = None,
    period_end: Optional[str] = None,
) -> AttributionReport:
    """
    Generate a full performance attribution report.

    Args:
        period_start: ISO date string for report start (default: earliest trade).
        period_end: ISO date string for report end (default: now).

    Returns:
        AttributionReport with module breakdown and top contributors.
    """
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    attributions = build_trade_attributions()

    # Filter by period if specified
    if period_start:
        attributions = [a for a in attributions if a.entry_timestamp >= period_start]
    if period_end:
        attributions = [a for a in attributions if a.entry_timestamp <= period_end]

    module_perf = compute_module_performance(attributions)

    # Build module breakdown dict (serializable)
    breakdown = {}
    for name, mp in module_perf.items():
        if mp.total_trades > 0:
            breakdown[name] = {
                'total_trades': mp.total_trades,
                'winning_trades': mp.winning_trades,
                'losing_trades': mp.losing_trades,
                'total_pnl_bps': round(mp.total_pnl_bps, 2),
                'avg_pnl_bps': round(mp.avg_pnl_bps, 2),
                'win_rate': round(mp.win_rate, 4),
                'max_gain_bps': round(mp.max_gain_bps, 2),
                'max_loss_bps': round(mp.max_loss_bps, 2),
            }

    # Top contributors sorted by total P&L
    top = sorted(
        breakdown.items(),
        key=lambda x: x[1]['total_pnl_bps'],
        reverse=True
    )
    top_contributors = [
        {'module': name, 'total_pnl_bps': data['total_pnl_bps'], 'trades': data['total_trades']}
        for name, data in top[:5]
    ]

    total_trades = sum(d['total_trades'] for d in breakdown.values())
    total_pnl = sum(d['total_pnl_bps'] for d in breakdown.values())

    effective_start = period_start or (
        min(a.entry_timestamp for a in attributions) if attributions else now
    )
    effective_end = period_end or now

    report = AttributionReport(
        generated_at=now,
        period_start=effective_start,
        period_end=effective_end,
        total_trades=total_trades,
        total_pnl_bps=round(total_pnl, 2),
        module_breakdown=breakdown,
        top_contributors=top_contributors,
    )

    # Persist report
    _save_report(report)

    return report


def _save_report(report: AttributionReport):
    """Save attribution report to state directory."""
    os.makedirs(os.path.dirname(ATTRIBUTION_OUTPUT_PATH), exist_ok=True)
    with open(ATTRIBUTION_OUTPUT_PATH, 'w') as f:
        json.dump({
            'generated_at': report.generated_at,
            'period_start': report.period_start,
            'period_end': report.period_end,
            'total_trades': report.total_trades,
            'total_pnl_bps': report.total_pnl_bps,
            'module_breakdown': report.module_breakdown,
            'top_contributors': report.top_contributors,
        }, f, indent=2)
