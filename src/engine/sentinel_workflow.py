"""
ARMS Engine: SENTINEL v2.0 Thesis Workflow

Replaces the hardcoded SENTINEL bridge with a durable thesis-entry and 
thesis-lifecycle workflow as defined in FSD v1.1 Section 3 & 4.
"""

import json
import os
import uuid
import datetime
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Literal

from intelligence.llm_wrapper import llm_wrapper
from data_feeds.sec_edgar_plugin import sec_edgar_plugin
from engine.mics import calculate_mics, SentinelGateInputs, MICSResult
from engine.bridge_paths import bridge_path


@dataclass
class SentinelThesisRecord:
    """Persistent state of a SENTINEL thesis."""
    thesis_id: str
    ticker: str
    status: Literal['DRAFT', 'ACTIVE', 'REJECTED', 'RETIRED']
    
    gate1_pass: Optional[bool] = None
    gate1_rationale: Optional[str] = None
    
    gate2_pass: Optional[bool] = None
    gate2_rationale: Optional[str] = None
    
    gate3_raw_score: Optional[float] = None
    gate3_rationale: Optional[str] = None
    
    gate4_fem_impact: Optional[str] = None
    
    gate5_regime_at_entry: Optional[str] = None
    
    gate6_source_category: Optional[Literal['Cat A', 'Cat B', 'Cat C', 'None']] = None
    
    mics_score: Optional[float] = None
    mics_c_level: Optional[int] = None
    
    created_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())


class SentinelWorkflowManager:
    """Manages the lifecycle of a SENTINEL thesis."""
    
    def __init__(self):
        self.storage_path = bridge_path('ARMS_SENTINEL_JSON', 'sentinel_records_v2.json')
        self._records: Dict[str, SentinelThesisRecord] = {}
        self._load_state()

    def _load_state(self):
        if not os.path.exists(self.storage_path):
            return
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data.get("records", []):
                    rec = SentinelThesisRecord(**item)
                    self._records[rec.ticker] = rec
        except Exception as e:
            print(f"[SentinelWorkflow] Error loading state: {e}. Starting fresh.")

    def _save_state(self):
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        with open(self.storage_path, 'w', encoding='utf-8') as f:
            json.dump({
                "last_updated": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "records": [asdict(r) for r in self._records.values()]
            }, f, indent=4)

    def get_all_records(self) -> Dict[str, 'SentinelThesisRecord']:
        """Public accessor for thesis records (used by queue_reasoning)."""
        return dict(self._records)

    def get_active_thesis(self, ticker: str) -> Optional[SentinelThesisRecord]:
        rec = self._records.get(ticker.upper())
        if rec and rec.status == 'ACTIVE':
            return rec
        return None

    def initiate_thesis(self, ticker: str, source_category: Literal['Cat A', 'Cat B', 'Cat C', 'None']) -> SentinelThesisRecord:
        """Step 1: PM seeds a new idea with a declared source category."""
        ticker = ticker.upper()
        if ticker in self._records and self._records[ticker].status == 'ACTIVE':
            raise ValueError(f"Active thesis already exists for {ticker}.")
            
        record = SentinelThesisRecord(
            thesis_id=f"thm_{ticker}_{uuid.uuid4().hex[:8]}",
            ticker=ticker,
            status='DRAFT',
            gate6_source_category=source_category
        )
        self._records[ticker] = record
        self._save_state()
        print(f"[SentinelWorkflow] Initiated DRAFT thesis for {ticker} (Source: {source_category})")
        return record

    def run_automated_gates(self, ticker: str, current_regime: str, projected_fem_impact: str) -> SentinelThesisRecord:
        """Step 2: AI runs Gates 1-3, System assigns Gates 4-5."""
        ticker = ticker.upper()
        record = self._records.get(ticker)
        if not record or record.status != 'DRAFT':
            raise ValueError(f"No DRAFT thesis found for {ticker}.")

        print(f"[SentinelWorkflow] Running automated Gates 1-3 for {ticker}...")
        
        # Pull SEC context
        docs = sec_edgar_plugin.fetch_docs(ticker, max_filings=2, max_chars_per_doc=15000)
        
        # Run AI Evaluation
        prompt = f"""
        You are the ARMS SENTINEL Analyst. Evaluate Gates 1-3 for {ticker}.
        - Gate 1: Is this a civilizational shift, not a product cycle? (bool)
        - Gate 2: Is this company non-optional (cannot be routed around)? (bool)
        - Gate 3: Quantitative mispricing score (0-30 float). (Sum of multiple mispricing (0-10), inst positioning gap (0-10), consensus framing gap (0-10)).
        
        Analyze the following SEC context and return ONLY valid JSON.
        JSON format: {{
            "gate1_pass": bool, "gate1_rationale": "...",
            "gate2_pass": bool, "gate2_rationale": "...",
            "gate3_raw_score": float, "gate3_rationale": "..."
        }}
        CONTEXT:
        {docs}
        """
        
        response_json = llm_wrapper.call(
            task_type='sentinel_scan',
            prompt=prompt
        )
        
        # Strip markdown
        if response_json.startswith("```json"):
            response_json = response_json[7:-3].strip()
        elif response_json.startswith("```"):
            response_json = response_json[3:-3].strip()
            
        try:
            res = json.loads(response_json)
            record.gate1_pass = res.get('gate1_pass', False)
            record.gate1_rationale = res.get('gate1_rationale', '')
            record.gate2_pass = res.get('gate2_pass', False)
            record.gate2_rationale = res.get('gate2_rationale', '')
            record.gate3_raw_score = float(res.get('gate3_raw_score', 0.0))
            record.gate3_rationale = res.get('gate3_rationale', '')
        except Exception as e:
            print(f"[SentinelWorkflow] Failed to parse AI evaluation: {e}")
            record.status = 'REJECTED'
            self._save_state()
            return record
            
        # Hard fail conditions
        if not record.gate1_pass or not record.gate2_pass:
            print(f"[SentinelWorkflow] {ticker} failed Gate 1 or 2. Thesis REJECTED.")
            record.status = 'REJECTED'
            self._save_state()
            return record
            
        # Gate 3 Threshold Check
        threshold = 14.0 if record.gate6_source_category in ['Cat A', 'Cat B'] else 20.0
        if record.gate3_raw_score < threshold:
            print(f"[SentinelWorkflow] {ticker} Gate 3 score {record.gate3_raw_score} below {record.gate6_source_category} threshold {threshold}. Thesis REJECTED.")
            record.status = 'REJECTED'
            self._save_state()
            return record
            
        # Gate 4 & 5
        record.gate4_fem_impact = projected_fem_impact
        if projected_fem_impact.startswith("ALERT"):
             print(f"[SentinelWorkflow] {ticker} fails Gate 4 (FEM ALERT). Thesis REJECTED.")
             record.status = 'REJECTED'
             self._save_state()
             return record
             
        record.gate5_regime_at_entry = current_regime
        if current_regime in ['DEFENSIVE', 'CRASH']:
             print(f"[SentinelWorkflow] {ticker} Gate 5 deferred (Regime {current_regime}). Leaving in DRAFT.")
             record.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
             self._save_state()
             return record

        # Pass all gates -> Calculate MICS
        inputs = SentinelGateInputs(
            gate3_raw_score=record.gate3_raw_score,
            source_category=record.gate6_source_category, # type: ignore
            fem_impact=record.gate4_fem_impact,
            regime_at_entry=record.gate5_regime_at_entry
        )
        
        mics_res: MICSResult = calculate_mics(inputs)
        record.mics_score = mics_res.raw_score
        record.mics_c_level = mics_res.conviction_level
        record.status = 'ACTIVE'
        record.updated_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        
        self._save_state()
        print(f"[SentinelWorkflow] {ticker} PASSED ALL GATES. Thesis ACTIVE. (MICS: {record.mics_c_level})")
        return record

# Singleton instance
sentinel_workflow = SentinelWorkflowManager()
