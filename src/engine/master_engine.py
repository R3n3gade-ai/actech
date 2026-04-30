"""
ARMS Engine: Master Engine (L4 Portfolio Construction)

Translates abstract MICS conviction scores into actual portfolio allocation 
percentages (Architecture AB 58/20/14/8), modulated by CDF decay and 
capped by ARAS/PDS ceilings and Kevlar limits.

Reference: ARMS FSD v1.1, Section 1 & 7
"""
from typing import Dict
from .kevlar import apply_kevlar_limits

def compute_target_weights(
    mics_scores: Dict[str, int], 
    cdf_multipliers: Dict[str, float], 
    aras_ceiling: float,
    slof_multiplier: float = 1.0
) -> Dict[str, float]:
    """
    Converts 1-10 MICS scores into Conviction-Squared (C^2) target weights,
    applies CDF performance decay, and scales to the ARAS equity ceiling.
    """
    target_weights = {}
    total_c_squared = 0.0
    
    # 1. Calculate effective C^2 for all positions
    effective_scores = {}
    for ticker, mics_level in mics_scores.items():
        decay = cdf_multipliers.get(ticker, 1.0)
        c_squared = (mics_level ** 2) * decay
        effective_scores[ticker] = c_squared
        total_c_squared += c_squared

    # 2. Allocate proportionally and apply Kevlar + ARAS ceiling
    if total_c_squared == 0:
        return {t: 0.0 for t in mics_scores.keys()}

    for ticker, c_squared in effective_scores.items():
        # Raw proportional weight of the equity book
        raw_equity_weight = c_squared / total_c_squared
        
        # Scale by the maximum allowed equity ceiling (e.g., 40% in DEFENSIVE)
        # Apply SLOF multiplier for asymmetric upside leverage (e.g. 1.25x in extreme RISK_ON)
        effective_ceiling = min(aras_ceiling * slof_multiplier, 1.25)
        scaled_weight = raw_equity_weight * effective_ceiling
        
        # Apply Kevlar hard limits (Max 22% single position)
        final_weight = apply_kevlar_limits(scaled_weight)
        
        target_weights[ticker] = final_weight

    return target_weights
