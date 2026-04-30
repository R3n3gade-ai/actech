"""
ARMS Engine: Systematic Scan Engine v2.0

This module provides low-fidelity autonomous thesis generation. It runs
every Monday pre-market against a defined universe of AI infrastructure
companies, using the Claude API to perform SENTINEL Gate 1 & 2 analysis.

"See it before the internet does."

Reference: arms_fsd_master_build_v1.1.md, Section 11.3
"""

import datetime
import json
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# --- Internal Imports ---
from intelligence.llm_wrapper import llm_wrapper

from config.scan_universe import AI_INFRASTRUCTURE_UNIVERSE
from data_feeds.sec_edgar_plugin import sec_edgar_plugin
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Data Structures ---

@dataclass
class ScanCandidate:
    """Represents a company that passed the initial automated screening."""
    ticker: str
    gate1_pass: bool
    gate2_pass: bool
    gate3_score: float
    rationale: str
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- Scan Engine Logic ---

def _fetch_documents(ticker: str) -> str:
    """
    Fetches the actual 10-K, 10-Q, and 8-K filings from the SEC EDGAR 
    feed plugin (Frontier F1 Layer). Caps each document to 15,000 characters
    to maintain LLM context boundaries.
    """
    return sec_edgar_plugin.fetch_docs(ticker, max_filings=2, max_chars_per_doc=15000)

def _calculate_gate3_score(ticker: str) -> float:
    """
    Calculates the quantitative Gate 3 mispricing score (0-30).
    Base score of 15 (neutral midpoint) adjusted by Phase 2
    anticipatory signals (ELVT, JPVI, PFVT, SCCR).

    The supplementary adjustment range is [-5, +5], applied to the
    base score and clamped to [0, 30].
    """
    from intelligence.gate3_supplementary import calculate_gate3_supplementary

    base_score = 15.0  # Neutral midpoint — no valuation data yet
    supplementary = calculate_gate3_supplementary(ticker)
    adjusted = base_score + supplementary.adjustment

    if supplementary.signal_count > 0:
        print(f"  [Gate3] {ticker}: base {base_score} + Phase 2 {supplementary.adjustment:+.2f} "
              f"= {adjusted:.2f} ({supplementary.signal_count} signals)")

    return max(0.0, min(30.0, adjusted))

def run_weekly_scan() -> List[ScanCandidate]:
    """
    Main entry point for the scan engine. Runs every Monday morning at 6AM CT.
    """
    print(f"--- ARMS Systematic Scan Engine: Monday Morning Sweep ---")
    
    candidates = []
    
    for ticker in AI_INFRASTRUCTURE_UNIVERSE:
        print(f"[Scan] Processing {ticker}...")
        
        # 1. Fetch public documents
        docs = _fetch_documents(ticker)
        
        # 2. Run SENTINEL Gate 1 & 2 Analysis via Claude API
        prompt = f"""
        You are the ARMS SENTINEL Gate 1 & 2 Analyst.
        - Gate 1: Is this a civilizational shift, not a product cycle?
        - Gate 2: Is this company non-optional (cannot be routed around)?
        
        Analyze the following context for {ticker} and return ONLY valid JSON.
        JSON format: {{'gate1_pass': bool, 'gate2_pass': bool, 'rationale': 'str'}}
        
        CONTEXT:
        {docs}
        """
        
        # Note: claude_wrapper handles the API call and task management
        response_json = llm_wrapper.call(
            task_type='sentinel_scan',
            prompt=prompt,
            knowledge_base_query=f'{ticker} SENTINEL analysis'
        )
        
                # Strip markdown if present
        if response_json.startswith("```json"):
            response_json = response_json[7:-3].strip()
        elif response_json.startswith("```"):
            response_json = response_json[3:-3].strip()
            
        try:
            res = json.loads(response_json)
            if res.get('gate1_pass') and res.get('gate2_pass'):
                # 3. For passers, calculate the quantitative Gate 3 score
                g3_score = _calculate_gate3_score(ticker)
                
                candidate = ScanCandidate(
                    ticker=ticker,
                    gate1_pass=True,
                    gate2_pass=True,
                    gate3_score=g3_score,
                    rationale=res.get('rationale', 'No rationale provided.')
                )
                candidates.append(candidate)
                
                # 4. Audit Logging
                append_to_log(SessionLogEntry(
                    timestamp=candidate.timestamp,
                    action_type='SCAN_CANDIDATE',
                    triggering_module='ScanEngine',
                    triggering_signal=f"Candidate identified: {ticker} (Gate 3 Score: {g3_score})",
                    ticker=ticker,
                    gate3_score=g3_score
                ))
                print(f"  -> Candidate Found: {ticker} (Score: {g3_score})")
                
        except (json.JSONDecodeError, KeyError) as e:
            print(f"  [Error] Failed to parse AI response for {ticker}: {e}")

    print(f"--- Scan Complete. {len(candidates)} candidate(s) identified. ---")
    return candidates

if __name__ == '__main__':
    # Test run
    run_weekly_scan()
