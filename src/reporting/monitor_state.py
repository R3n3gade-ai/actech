from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional


QueueState = Literal[
    'LOCKED',
    'WATCH',
    'TRIGGERED',
    'EVAL_ONLY',
    'HOLD_CURRENT_WEIGHT',
    'REMOVED',
    'MONITOR_LIST',
    'UNLOCKED'
]

QueueReason = Literal[
    'ASYMMETRY_RETAINED',
    'CONSENSUS_PRICED',
    'SENTINEL_UNCONFIRMED',
    'TIME_HORIZON_MISMATCH',
    'VALUATION_COMPRESSION_REQUIRED',
    'REGIME_NOT_CLEARED',
    'RISK_ON_EVAL_ONLY',
    'THESIS_MONITOR_ONLY',
    'UNKNOWN'
]

EquityFlag = Literal['OK', 'WATCH', 'THESIS+', 'TRIMMED', 'HOLD', 'REMOVED', 'ALERT']


@dataclass
class WeeklyScorecardRow:
    session_label: str
    market_summary: str
    score_estimate: float
    regime_note: str


@dataclass
class MacroInputCard:
    title: str
    value: str
    context: str


@dataclass
class QueueEntryState:
    ticker: str
    target: str
    execution_instruction: str
    state: QueueState
    reason: QueueReason
    trigger_rule: str
    notes: str = ''


@dataclass
class MonitorListEntry:
    ticker: str
    reeval_trigger: str
    rationale: str


@dataclass
class EquityBookEntryState:
    ticker: str
    name: str
    weight_pct: float
    perf_text: str
    status: EquityFlag
    rationale: str


@dataclass
class SleeveEntryState:
    ticker: str
    weight_pct: float
    rationale: str


@dataclass
class ModulePanelState:
    name: str
    status: str
    detail: str


@dataclass
class DecisionQueueItem:
    title: str
    body: str


@dataclass
class DailyMonitorState:
    date_label: str
    regime: str
    score: float
    score_direction: str
    score_prior: Optional[float]
    queue_status: str
    next_catalyst: str
    equity_ceiling_pct: float

    header_blurb: str = ''
    executive_summary: str = ''

    weekly_scorecard: List[WeeklyScorecardRow] = field(default_factory=list)
    macro_cards: List[MacroInputCard] = field(default_factory=list)
    key_developments: List[Dict[str, str]] = field(default_factory=list)

    deployment_queue: List[QueueEntryState] = field(default_factory=list)
    risk_on_queue: List[QueueEntryState] = field(default_factory=list)
    monitor_list: List[MonitorListEntry] = field(default_factory=list)
    removed_queue_items: List[QueueEntryState] = field(default_factory=list)

    equity_book: List[EquityBookEntryState] = field(default_factory=list)
    defensive_sleeve: List[SleeveEntryState] = field(default_factory=list)
    module_panels: List[ModulePanelState] = field(default_factory=list)
    pm_decision_queue: List[DecisionQueueItem] = field(default_factory=list)

    live_context: Dict[str, str] = field(default_factory=dict)
