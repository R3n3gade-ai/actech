"""
ARMS Engine: Dealer Gamma

ARAS Sub-Module evaluating options dealer positioning.
Negative gamma environments accelerate market selling.

Uses VIX acceleration as a proxy for dealer gamma flip, since real-time
GEX data requires SqueezeMetrics or Tier1Alpha subscriptions. Rapid VIX
spikes (>30) typically coincide with dealers moving to negative gamma,
which amplifies directional moves.

Reference: THB v4.0, Section 10
"""
from dataclasses import dataclass
from typing import List, Optional
from data_feeds.interfaces import SignalRecord

@dataclass
class DealerGammaSignal:
    regime: str # 'POSITIVE', 'NEUTRAL', 'NEGATIVE'
    gamma_exposure_bn: float

def run_dealer_gamma_check(signals: Optional[List[SignalRecord]] = None) -> DealerGammaSignal:
    if signals is None:
        return DealerGammaSignal(regime="NEUTRAL", gamma_exposure_bn=0.0)
    
    vix = next((s.value for s in signals if s.signal_type == 'VIX_INDEX'), None)
    if vix is None:
        return DealerGammaSignal(regime="NEUTRAL", gamma_exposure_bn=0.0)
    
    vix_level = vix * 100.0 if vix < 1.0 else vix
    
    # VIX > 30 is a strong proxy for negative gamma regime
    # VIX 25-30 is transitional / neutral
    # VIX < 25 typically positive gamma (dealers sell vol, dampening moves)
    if vix_level > 30:
        return DealerGammaSignal(regime="NEGATIVE", gamma_exposure_bn=-1.5)
    elif vix_level > 25:
        return DealerGammaSignal(regime="NEUTRAL", gamma_exposure_bn=0.5)
    else:
        return DealerGammaSignal(regime="POSITIVE", gamma_exposure_bn=2.5)
