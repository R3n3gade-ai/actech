"""
ARMS Engine: Kevlar Hard Limit Enforcement

Enforces concentration limits across the portfolio.
Prevents any single thesis from destroying the fund regardless of MICS score.

Blueprint Correction Memo CRITICAL #5 (March 28, 2026):
  - 3% minimum floor is NOT canonical and contradicts MICS conviction-squared
    weighting. REMOVED.
  - 22% maximum and 48% AI sector cap await formal PM specification.
    Retained as provisional guardrails pending PM confirmation.

Reference: CLAUDE.md v4.0 — Kevlar position-level risk controls
"""

# Provisional hard limits — awaiting formal PM specification
MAX_SINGLE_POSITION_PCT = 0.22  # 22% Max (provisional — needs FSD citation)
MAX_AI_SECTOR_PCT = 0.48        # 48% Total AI Sector Cap (provisional)

def apply_kevlar_limits(target_weight: float) -> float:
    """
    Caps an individual position's target weight at the Kevlar maximum.
    
    No minimum floor — MICS conviction-squared weighting uses a dynamic
    denominator with no artificial minimum (Blueprint Correction Memo
    CRITICAL #5). Low-conviction positions size naturally via C² math.
    """
    if target_weight > MAX_SINGLE_POSITION_PCT:
        return MAX_SINGLE_POSITION_PCT
    return target_weight
