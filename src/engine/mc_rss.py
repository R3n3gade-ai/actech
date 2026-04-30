"""
ARMS Engine: MC-RSS (Macro Compass - Retail Sentiment Score)

This module provides the contrarian "Sentiment Trap" override for ARMS. 
It integrates retail buying flows (VandaTrack) and manager exposure (NAAIM)
to identify extremes in market positioning that signal either 
"Peak Greedy" (Risk-Off) or "Peak Fear" (ARES Re-Entry Gate 3).

"Silence is trust in the architecture."

Reference: MASTER_IMPLEMENTATION_PLAN.md
Reference: Codebase Game Plan v2.0, Step 5
"""

import datetime
from dataclasses import dataclass, field
from typing import List, Dict, Literal, Optional

# --- Internal Imports ---
from data_feeds.interfaces import SignalRecord

from data_feeds.interfaces import SignalRecord
from reporting.audit_log import SessionLogEntry, append_to_log
from engine.mc_rss_analytics import fetch_live_sentiment


# --- Data Structures ---

@dataclass
class RSSResult:
    """The output of the Retail Sentiment Score module."""
    composite_rss: float # 0.0 (Bearish/Fear) to 1.0 (Bullish/Greed)
    signal_label: Literal['EXTREME_FEAR', 'FEAR', 'NEUTRAL', 'GREED', 'EXTREME_GREED']
    is_contrarian_buy_active: bool # Gate 3 for ARES
    is_contrarian_sell_active: bool # Peak Greed Trap
    retail_flow_zscore: float
    manager_exposure_pct: float
    timestamp: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

# --- MC-RSS Logic ---

def evaluate_live_sentiment() -> RSSResult:
    """
    Wrapper that fetches live NAAIM/Sentiment data and runs the MC-RSS formula.
    """
    live_inputs = fetch_live_sentiment()
    # Provide a rolling history proxy for the z-score calculation
    history_proxy = [1.0e9, 1.1e9, 1.2e9, 1.3e9]
    
    return calculate_mc_rss(
        retail_net_buying_usd=live_inputs.retail_net_buying_usd,
        retail_history_30d=history_proxy,
        naaim_exposure_index=live_inputs.naaim_exposure_index,
        aaii_bull_bear_spread=live_inputs.aaii_bull_bear_spread
    )

def calculate_mc_rss(
    retail_net_buying_usd: float, # VandaTrack daily net flow
    retail_history_30d: List[float],
    naaim_exposure_index: float, # NAAIM manager exposure (0-200)
    aaii_bull_bear_spread: float # AAII Retail Sentiment Spread
) -> RSSResult:
    """
    Calculates the composite retail sentiment score.
    Logic: Extremes (High/Low) are treated as contrarian signals.
    """
    
    # 1. Calculate Retail Flow Z-Score (Momentum check)
    if len(retail_history_30d) > 2:
        import statistics
        mean = statistics.mean(retail_history_30d)
        stdev = statistics.stdev(retail_history_30d) if len(retail_history_30d) > 1 else 1.0
        if stdev == 0.0: stdev = 1.0
        retail_z = (retail_net_buying_usd - mean) / stdev
    else:
        retail_z = 0.0

    # 2. Normalize NAAIM (0-200 mapped to 0-1)
    naaim_norm = naaim_exposure_index / 200.0
    
    # 3. Normalize AAII Bull-Bear Spread (-1.0 to 1.0 mapped to 0-1)
    # -1.0 (Full Bear) -> 0.0 | 1.0 (Full Bull) -> 1.0
    aaii_norm = (aaii_bull_bear_spread + 1.0) / 2.0
    
    # 4. Composite Score (Weighted Average)
    # Weights: 40% Flow, 30% Managers, 30% Survey
    rss_raw = (min(1.0, max(0.0, (retail_z + 3) / 6)) * 0.4) + \
              (naaim_norm * 0.3) + \
              (aaii_norm * 0.3)

    # 5. Determine Label & Contrarian Flags
    # Boundaries are inclusive at extremes so contrarian signals fire AT the
    # threshold value (capitulation at 0.25, peak greed at 0.85), which matches
    # the spirit of "RSS reaches this level". Mid-band boundaries (0.40, 0.70)
    # default to NEUTRAL on the exact value to bias the system away from
    # over-eager labeling.
    label: Literal['EXTREME_FEAR', 'FEAR', 'NEUTRAL', 'GREED', 'EXTREME_GREED'] = 'NEUTRAL'
    buy_active = False
    sell_active = False

    if rss_raw <= 0.25:
        label = 'EXTREME_FEAR'
        buy_active = True # Capitulation signal for ARES Gate 3
    elif rss_raw < 0.40:
        label = 'FEAR'
    elif rss_raw >= 0.85:
        label = 'EXTREME_GREED'
        sell_active = True # Sentiment Trap
    elif rss_raw > 0.70:
        label = 'GREED'

    result = RSSResult(
        composite_rss=rss_raw,
        signal_label=label,
        is_contrarian_buy_active=buy_active,
        is_contrarian_sell_active=sell_active,
        retail_flow_zscore=retail_z,
        manager_exposure_pct=naaim_norm
    )

    # 6. Audit Logging for Extremes
    if buy_active or sell_active:
        append_to_log(SessionLogEntry(
            timestamp=result.timestamp,
            action_type='RSS_SIGNAL',
            triggering_module='MC-RSS',
            triggering_signal=f"Contrarian signal: {label} (RSS: {rss_raw:.2f})"
        ))
        print(f"[MC-RSS] CONTRARIAN ALERT: {label} (RSS: {rss_raw:.2f})")

    return result

if __name__ == '__main__':
    print("ARMS MC-RSS Module Active (Simulation Mode)")
    
    # Simulate EXTREME FEAR (Capitulation)
    mock_history = [1.2e9, 1.1e9, 1.3e9, 1.4e9] # Strong buying history
    res_fear = calculate_mc_rss(
        retail_net_buying_usd=0.2e9, # Sharp drop in buying
        retail_history_30d=mock_history,
        naaim_exposure_index=35.0, # Managers de-risked
        aaii_bull_bear_spread=-0.45 # Retail very bearish
    )
    print(f"Outcome (Fear): {res_fear.signal_label} - Buy Active: {res_fear.is_contrarian_buy_active}")

    # Simulate EXTREME GREED (Sentiment Trap)
    res_greed = calculate_mc_rss(
        retail_net_buying_usd=2.5e9, # Record buying
        retail_history_30d=mock_history,
        naaim_exposure_index=110.0, # Over-levered managers
        aaii_bull_bear_spread=0.60 # Retail euphoric
    )
    print(f"Outcome (Greed): {res_greed.signal_label} - Sell Active: {res_greed.is_contrarian_sell_active}")
