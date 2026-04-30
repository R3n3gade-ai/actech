"""
ARMS Engine: Liquidity-Adjusted Execution Protocol (LAEP)

Converts target allocations into execution-ready order parameters by
accounting for VIX regime, position size constraints, and spread
conditions before orders are submitted to the broker API.

Five VIX Execution Tiers (Auto Deployment v1.1 §7.1):
  Tier 1: VIX < 16   — NORMAL    — Limit at mid, aggressive fill
  Tier 2: VIX 16-22  — ELEVATED  — Limit 0.1-0.2% through mid, patience
  Tier 3: VIX 22-28  — HIGH      — VWAP for large sizes, spread-aware
  Tier 4: VIX > 28   — STRESS    — TWAP over full session, size reduced 50%
  Tier 5: VIX > 40   — CRISIS    — Execution halted except CRASH protocol

Position Size Constraints (§7.2):
  - Max single-order: 20% of 10-day ADV
  - Exceed 20% ADV: split into child orders across session
  - Minimum fill: 80% of target within session
  - Slippage budget: 0.15% max vs decision-time price

Order Scenarios (§7.3):
  - Standard (VIX<22): Limit at mid, 30-min fill window
  - High-vol: VWAP algo, 15% volume participation
  - CRASH exit: Market orders for top 5 by ADV, limits for illiquid
  - SLOF legs: Spread order, both legs simultaneously
  - PTRH roll: Spread order, buy new + sell expiring simultaneously
  - CDF trim: Limit sell at bid+1, step to bid after 60 min
  - PDS ceiling: Immediate market sell, no fill optimization

Reference: Auto Deployment & Execution v1.1, Section 7
"""
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class VIXTier(Enum):
    """Five canonical VIX execution tiers per Auto Deployment v1.1 §7.1."""
    NORMAL   = 1   # VIX < 16
    ELEVATED = 2   # VIX 16-22
    HIGH     = 3   # VIX 22-28
    STRESS   = 4   # VIX 28-40
    CRISIS   = 5   # VIX > 40


@dataclass
class LAEPParameters:
    """Execution parameters determined by LAEP for a given order."""
    vix_tier: VIXTier
    order_type: str            # 'MARKET' | 'VWAP' | 'LIMIT'
    execution_window_min: int  # minutes
    slippage_budget_bps: float # basis points
    volume_participation_pct: Optional[float] = None  # for VWAP orders
    size_reduction_factor: float = 1.0  # 1.0 = full size, 0.5 = halved
    execution_halted: bool = False  # True in CRISIS for non-CRASH orders


# ---------------------------------------------------------------------------
# VIX Tier Classification
# ---------------------------------------------------------------------------

def classify_vix_tier(vix: float) -> VIXTier:
    """Classify current VIX into one of the 5 execution tiers."""
    if vix < 16:
        return VIXTier.NORMAL
    elif vix < 22:
        return VIXTier.ELEVATED
    elif vix < 28:
        return VIXTier.HIGH
    elif vix <= 40:
        return VIXTier.STRESS
    else:
        return VIXTier.CRISIS


# ---------------------------------------------------------------------------
# Execution Parameter Resolution
# ---------------------------------------------------------------------------

# Maximum slippage budget per tier (basis points)
_TIER_SLIPPAGE = {
    VIXTier.NORMAL:   5.0,
    VIXTier.ELEVATED: 10.0,
    VIXTier.HIGH:     15.0,    # 0.15% = 15 bps per §7.2
    VIXTier.STRESS:   25.0,
    VIXTier.CRISIS:   50.0,
}

# Default execution window per tier (minutes)
_TIER_WINDOW = {
    VIXTier.NORMAL:   30,
    VIXTier.ELEVATED: 45,
    VIXTier.HIGH:     60,
    VIXTier.STRESS:   390,   # Full session TWAP
    VIXTier.CRISIS:   390,
}


def resolve_execution_params(
    vix: float,
    requested_order_type: str,
    triggering_module: str,
    action: str,
    is_crash_protocol: bool = False,
) -> LAEPParameters:
    """
    Determine LAEP execution parameters for an order based on VIX tier.

    Args:
        vix: current VIX level
        requested_order_type: original order type ('MARKET', 'VWAP', 'LIMIT')
        triggering_module: module that generated the order (e.g. 'PDS', 'CDF', 'PTRH')
        action: order action ('BUY', 'SELL', 'BUY_PUT', 'SELL_PUT', etc.)
        is_crash_protocol: True if this is a CRASH-regime forced de-risk

    Returns:
        LAEPParameters with tier-appropriate execution constraints
    """
    tier = classify_vix_tier(vix)
    order_type = requested_order_type
    window = _TIER_WINDOW[tier]
    slippage = _TIER_SLIPPAGE[tier]
    volume_pct = None
    size_factor = 1.0
    halted = False

    # --- Tier-specific overrides ---

    if tier == VIXTier.NORMAL:
        # Limit at mid, aggressive fill targeting
        if order_type == 'MARKET':
            order_type = 'LIMIT'
        window = 30

    elif tier == VIXTier.ELEVATED:
        # Limit 0.1-0.2% through mid, patience mode
        if order_type == 'MARKET':
            order_type = 'LIMIT'
        window = 45

    elif tier == VIXTier.HIGH:
        # VWAP for large sizes, spread-aware limits
        if order_type == 'MARKET':
            order_type = 'VWAP'
        volume_pct = 0.15  # 15% volume participation per §7.3
        window = 60

    elif tier == VIXTier.STRESS:
        # TWAP over full session, single-order size reduced 50%
        if order_type in ('MARKET', 'LIMIT'):
            order_type = 'VWAP'
        volume_pct = 0.10
        size_factor = 0.50
        window = 390  # Full session

    elif tier == VIXTier.CRISIS:
        # Execution halted except CRASH protocol
        if is_crash_protocol:
            # CRASH exit: Market orders for high-ADV, limits for illiquid
            if action == 'SELL':
                order_type = 'MARKET'
            window = 390
        else:
            halted = True

    # --- Module-specific overrides per §7.3 ---

    # PDS auto-ceiling: immediate market sell, no fill optimization
    if triggering_module == 'PDS' and action == 'SELL':
        order_type = 'MARKET'
        window = 5  # Immediate
        slippage = 50.0  # Accept slippage in emergency de-risk

    # CDF-triggered trim: limit sell at bid+1
    elif triggering_module == 'CDF' and action == 'SELL':
        order_type = 'LIMIT'
        window = 60

    # PTRH roll and SLOF legs: spread order (both legs simultaneously)
    # The spread mechanics are handled at the broker level — LAEP just
    # ensures appropriate window and slippage
    elif triggering_module in ('PTRH', 'SLOF'):
        if tier.value <= VIXTier.HIGH.value:
            window = max(window, 30)
        slippage = max(slippage, 10.0)

    return LAEPParameters(
        vix_tier=tier,
        order_type=order_type,
        execution_window_min=window,
        slippage_budget_bps=slippage,
        volume_participation_pct=volume_pct,
        size_reduction_factor=size_factor,
        execution_halted=halted,
    )


# ---------------------------------------------------------------------------
# ADV Constraint Check
# ---------------------------------------------------------------------------

# Maximum single-order size as fraction of 10-day ADV (§7.2)
MAX_ADV_FRACTION = 0.20

# Minimum fill target within session (§7.2)
MIN_FILL_FRACTION = 0.80


def check_adv_constraint(
    order_notional: float,
    avg_daily_volume_notional: float,
) -> int:
    """
    Determine how many child orders are needed to stay within ADV limits.

    Args:
        order_notional: total notional value of the order
        avg_daily_volume_notional: 10-day average daily volume in notional

    Returns:
        Number of child order splits needed (1 = no split required)
    """
    if avg_daily_volume_notional <= 0:
        return 1
    max_single = avg_daily_volume_notional * MAX_ADV_FRACTION
    if order_notional <= max_single:
        return 1
    import math
    return math.ceil(order_notional / max_single)
