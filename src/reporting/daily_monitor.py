"""
ARMS Reporting: Daily Monitor v4.0 (Architecture AB)

Generates the definitive institutional Daily Monitor. It fuses hard quantitative 
system states (L4/L5) with LLM-synthesized narrative intelligence (L3 Anticipatory Layer) 
to match the exact output expected by the GP and PM.

"Silence is trust in the architecture."
"""

import datetime
import json
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from intelligence.llm_wrapper import llm_wrapper

@dataclass
class EquityPosition:
    ticker: str
    name: str
    weight: float
    session_perf: float
    status: str
    rationale: str

@dataclass
class QueueItem:
    ticker: str
    target: str
    execution_instruction: str
    status: str
    notes: str = ''

@dataclass
class MonitorItem:
    ticker: str
    reeval_trigger: str
    rationale: str

@dataclass
class DefensiveSleeveItem:
    ticker: str
    weight: float
    rationale: str

@dataclass
class DailyMonitorV4:
    date: str
    regime: str
    score: float
    score_direction: str
    queue_status: str
    
    header_blurb: str
    executive_summary: str
    key_developments: List[Dict[str, str]]
    pm_decision_queue: List[str]
    
    macro_compass_score_yesterday: float
    macro_compass_trigger: float
    macro_compass_next_catalyst: str
    macro_compass_drivers_up: str
    macro_compass_drivers_down: str
    
    macro_inputs: Dict[str, Dict[str, str]]
    
    equity_book: List[EquityPosition]
    deployment_queue: List[QueueItem]
    removed_queue_items: List[QueueItem]
    monitor_list: List[MonitorItem]
    defensive_sleeve: List[DefensiveSleeveItem]
    
    module_status: Dict[str, Dict[str, str]]

class DailyMonitorRenderer:
    def __init__(self):
        self.version = "Architecture AB v4.0"

    def synthesize_intelligence(self, market_context: str, raw_system_state: Dict[str, Any]) -> Dict[str, Any]:
        prompt = f"""
        You are the Intelligence Synthesis Engine for ARMS (Achelion Risk Management System).
        You must generate the qualitative sections for the Daily Monitor exactly matching the tone of an institutional GP briefing.
        
        SYSTEM STATE:
        Regime: {raw_system_state.get('regime')}
        Score: {raw_system_state.get('score')} ({raw_system_state.get('score_direction')})
        Queue: {raw_system_state.get('queue_status')}
        
        MARKET/EVENT CONTEXT:
        {market_context}
        
        Generate the following in JSON format ONLY:
        {{
            "header_blurb": "Short 3-5 word phrase describing the day's theme (e.g. CEASEFIRE FRACTURING)",
            "executive_summary": "A 1-paragraph institutional summary of the day's action, score movement, and catalysts.",
            "key_developments": [
                {{"headline": "CAPITALIZED HEADLINE", "body": "1 paragraph of analysis"}}
            ],
            "pm_decision_queue": [
                "Numbered list item 1 detailing instructions to the PM (e.g. '1. DO NOT DEPLOY. Queue fires when...')",
                "Item 2..."
            ]
        }}
        """
        
        print(f"[DailyMonitor] Calling Intelligence Layer for synthesis...")
        response_json = llm_wrapper.call(
            task_type='daily_monitor_synthesis',
            prompt=prompt
        )
        
        # strip out any markdown code blocks
        if response_json.startswith("```json"):
            response_json = response_json[7:-3].strip()
        elif response_json.startswith("```"):
            response_json = response_json[3:-3].strip()
            
        try:
            parsed = json.loads(response_json)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"[DailyMonitor] LLM synthesis returned unparseable JSON: {e}\nRaw response (first 500 chars): {response_json[:500]}")
        required_keys = {'header_blurb', 'executive_summary', 'key_developments', 'pm_decision_queue'}
        missing = required_keys - set(parsed.keys())
        if missing:
            raise RuntimeError(f"[DailyMonitor] LLM synthesis missing required keys: {missing}")
        return parsed

    def render_markdown(self, monitor: DailyMonitorV4) -> str:
        score_diff = monitor.score - monitor.macro_compass_score_yesterday
        sign = "+" if score_diff >= 0 else ""
        
        md = f"""# ACHELION ARMS · Daily Monitor · {monitor.date} · {monitor.header_blurb} · CONFIDENTIAL — GP Distribution 
{monitor.regime} · Score ~{monitor.score}{monitor.score_direction} · Queue {monitor.queue_status}
*Achelion Capital Management, LLC · {self.version} · Not for external distribution*

---
**ACHELION ARMS — Daily Monitor**
{monitor.date} · {self.version}

{monitor.executive_summary}

"""
        if getattr(monitor, 'weekly_scorecard', []):
            md += "---\n### 0 · RECENT REGIME HISTORY\n\n"
            md += "| Session | Summary | Score | Regime |\n"
            md += "| :--- | :--- | :--- | :--- |\n"
            for row in getattr(monitor, 'weekly_scorecard', []):
                md += f"| {row.session_label} | {row.market_summary} | ~{row.score_estimate} | {row.regime_note} |\n"
            md += "\n"

        md += f"""---
### 1 · MACRO COMPASS — REGIME SCORING

| Yesterday's Low | Today's Score | Score Change | Queue Trigger | Equity Ceiling | Next Catalyst |
| :--- | :--- | :--- | :--- | :--- | :--- |
| ~{monitor.macro_compass_score_yesterday} | ~{monitor.score}{monitor.score_direction} | {sign}{score_diff:.2f} | {monitor.macro_compass_trigger} | 40% | {monitor.macro_compass_next_catalyst} |

**Score re-elevating drivers:** {monitor.macro_compass_drivers_up}

**Score decline drivers (still active):** {monitor.macro_compass_drivers_down}

---
### 2 · MACRO INPUTS — THURSDAY MORNING

"""
        for key, val in monitor.macro_inputs.items():
            md += f"- **{key}:** {val['value']} — *{val['context']}*\n"

        md += "\n---\n### 3 · KEY DEVELOPMENTS\n\n"
        for dev in monitor.key_developments:
            md += f"**{dev['headline']}**\n{dev['body']}\n\n"

        md += "---\n### 4 · EQUITY BOOK — DEFENSIVE 40% CEILING\n\n"
        md += "| Ticker | Name | Wt. | Session | Status | Flag | Rationale |\n"
        md += "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |\n"
        for eq in monitor.equity_book:
            sp_sign = "+" if eq.session_perf > 0 else ""
            md += f"| {eq.ticker} | {eq.name} | {eq.weight}% | {sp_sign}{eq.session_perf}% | {eq.status} | OK | {eq.rationale} |\n"

        md += f"\n---\n### 5 · DEPLOYMENT QUEUE — {monitor.queue_status}\n\n"
        md += "| # | Ticker | Target | Execution Instruction | Status |\n"
        md += "| :--- | :--- | :--- | :--- | :--- |\n"
        for i, q in enumerate(monitor.deployment_queue):
            md += f"| {i+1} | {q.ticker} | {q.target} | {q.execution_instruction} | {q.status} |\n"

        if monitor.removed_queue_items:
            md += "\n**Removed / Downgraded Queue Items**\n\n"
            md += "| Ticker | Target | Execution Instruction | Status | Notes |\n"
            md += "| :--- | :--- | :--- | :--- | :--- |\n"
            for q in monitor.removed_queue_items:
                md += f"| {q.ticker} | {q.target} | {q.execution_instruction} | {q.status} | {q.notes} |\n"

        if monitor.monitor_list:
            md += "\n**Monitor List**\n\n"
            md += "| Ticker | Re-Eval Trigger | Rationale |\n"
            md += "| :--- | :--- | :--- |\n"
            for q in monitor.monitor_list:
                md += f"| {q.ticker} | {q.reeval_trigger} | {q.rationale} |\n"

        md += "\n---\n### 6 · DEFENSIVE SLEEVE + PTRH + CASH\n\n"
        for slv in monitor.defensive_sleeve:
            md += f"- **{slv.ticker} ({slv.weight}%):** {slv.rationale}\n"

        md += "\n---\n### 7 · MODULE STATUS\n\n"
        for mod, stat in monitor.module_status.items():
            md += f"- **{mod} · {stat['status']}**: {stat['detail']}\n"

        md += "\n---\n### 8 · PM DECISION QUEUE\n\n"
        for i, dec in enumerate(monitor.pm_decision_queue):
            md += f"**{i+1}. {dec}**\n\n"

        return md

def run_daily_monitor(raw_inputs: Dict[str, Any], market_context: str) -> str:
    renderer = DailyMonitorRenderer()
    intel = renderer.synthesize_intelligence(market_context, raw_inputs)
    
    monitor = DailyMonitorV4(
        date=raw_inputs.get('date', datetime.datetime.now().strftime("%B %d, %Y")),
        regime=raw_inputs.get('regime', 'DEFENSIVE'),
        score=raw_inputs.get('score', 0.74),
        score_direction=raw_inputs.get('score_direction', '↑'),
        queue_status=raw_inputs.get('queue_status', 'LOCKED'),
        header_blurb=intel.get('header_blurb', ''),
        executive_summary=intel.get('executive_summary', ''),
        key_developments=intel.get('key_developments', []),
        pm_decision_queue=intel.get('pm_decision_queue', []),
        macro_compass_score_yesterday=raw_inputs.get('macro_compass_score_yesterday', 0.72),
        macro_compass_trigger=raw_inputs.get('macro_compass_trigger', 0.65),
        macro_compass_next_catalyst=raw_inputs.get('macro_compass_next_catalyst', 'Unknown'),
        macro_compass_drivers_up=raw_inputs.get('macro_compass_drivers_up', ''),
        macro_compass_drivers_down=raw_inputs.get('macro_compass_drivers_down', ''),
        macro_inputs=raw_inputs.get('macro_inputs', {}),
        equity_book=[EquityPosition(**eq) for eq in raw_inputs.get('equity_book', [])],
        deployment_queue=[QueueItem(**q) for q in raw_inputs.get('deployment_queue', [])],
        removed_queue_items=[QueueItem(**q) for q in raw_inputs.get('removed_queue_items', [])],
        monitor_list=[MonitorItem(**m) for m in raw_inputs.get('monitor_list', [])],
        defensive_sleeve=[DefensiveSleeveItem(**s) for s in raw_inputs.get('defensive_sleeve', [])],
        module_status=raw_inputs.get('module_status', {})
    )
    
    return renderer.render_markdown(monitor)
