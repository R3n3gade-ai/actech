"""
ARMS Engine: Synthetic Leverage Overlay Facility (SLOF)

Governs the expansion of portfolio exposure above 100% NAV using
synthetic instruments. Activation is regime-driven per CLAUDE.md regime table
and Auto Deployment & Execution Architecture v1.1 Section 6.2:

  RISK_ON  (<0.30)  → SLOF active — full size
  WATCH    (0.30-0.50) → SLOF active — full size
  NEUTRAL  (0.51-0.65) → SLOF reduced — proportional to 75% equity ceiling
  DEFENSIVE (0.66-0.80) → SLOF removed entirely
  CRASH    (>0.80)  → SLOF removed entirely

AUP golden gates control the expansion MULTIPLIER (e.g., 1.25×) when
SLOF is regime-eligible, but do NOT control the base active/inactive state.

Reference: THB v4.0, Section 10
Reference: CLAUDE.md Regime Table — SLOF column
Reference: Auto Deployment v1.1, Section 6.2
"""
from dataclasses import dataclass
from engine.asymmetric_upside import AupStatus

NEUTRAL_EQUITY_CEILING = 0.75  # 75% equity ceiling at NEUTRAL

@dataclass
class SlofStatus:
    is_active: bool
    current_leverage_ratio: float
    target_leverage_ratio: float

def run_slof_manager(
    aup_status: AupStatus,
    current_leverage: float,
    regime_score: float = 0.0,
) -> SlofStatus:
    """
    Determine SLOF state based on regime score and AUP eligibility.

    Regime drives active/inactive. AUP drives the multiplier when active.
    At NEUTRAL, SLOF notional is scaled proportionally with the 75% equity ceiling.
    """
    if regime_score >= 0.66:
        # DEFENSIVE or CRASH — force remove SLOF entirely
        target = 1.00
        active = False
    elif regime_score >= 0.51:
        # NEUTRAL — reduce proportionally (Auto Deployment v1.1 §6.2)
        if aup_status.is_expansion_eligible:
            full_extra = aup_status.slof_multiplier - 1.0  # e.g., 0.25 for 1.25x
            target = 1.0 + (full_extra * NEUTRAL_EQUITY_CEILING)  # e.g., 1.1875
            active = True
        else:
            target = 1.00
            active = False
    else:
        # RISK_ON or WATCH — SLOF active at full size
        if aup_status.is_expansion_eligible:
            target = aup_status.slof_multiplier  # e.g., 1.25x
            active = True
        else:
            target = 1.00
            active = False

    return SlofStatus(
        is_active=active,
        current_leverage_ratio=current_leverage,
        target_leverage_ratio=target,
    )
