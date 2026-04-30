"""
ARMS Module: STRC Dynamic Reserve Layer

The STRC reserve layer deploys into QQQ puts alongside the permanent PTRH layer.
It is separately managed from PTRH but additive to total hedge coverage.

Deployment schedule by regime (THB v4.0 FINAL):
  RISK_ON:    0% deployed  — STRC locked, earning 11.5% yield
  WATCH:      0% deployed  — STRC locked, earning 11.5% yield
  NEUTRAL:   50% deployed  — half of STRC sleeve converted to QQQ puts
  DEFENSIVE: 100% deployed — full STRC sleeve converted to QQQ puts
  CRASH:     100% deployed — full STRC sleeve converted to QQQ puts

STRC is a yield instrument (11.5% annual). No price harvest trigger.
DSHP explicitly skips STRC.

Reference: ARMS THB v4.0 FINAL — Section 5 (Two Layers)
Reference: ARMS Blueprint Correction Memo — Correction #8
"""

from dataclasses import dataclass
from typing import Optional

from engine.tail_hedge import PTRH_REGIME_TABLE


# --- Constants ---

STRC_TARGET_WEIGHT = 0.04       # 4% of NAV
STRC_ANNUAL_YIELD = 0.115       # 11.5% annual yield
STRC_REGIME_RESERVE_WEIGHT = 0.02  # 2% of NAV held as regime reserve per canonical cash structure


@dataclass
class STRCStatus:
    """Status of the STRC dynamic reserve layer."""
    regime: str
    strc_sleeve_value: float        # current market value of STRC position
    reserve_pct: float              # 0.0, 0.50, or 1.00
    deployed_notional: float        # dollars deployed as QQQ puts
    retained_notional: float        # dollars remaining in STRC (earning yield)
    annualized_yield_income: float  # projected annual yield on retained portion
    last_action: str                # DEPLOY / RETRACT / HOLD


def calculate_strc_reserve_deployment(
    regime_label: str,
    strc_position_value: float,
) -> STRCStatus:
    """
    Calculate how much of the STRC position to deploy as QQQ puts
    based on the current regime.

    The reserve_pct comes from the canonical PTRH_REGIME_TABLE.
    Deployed notional is additive to the permanent PTRH layer.
    """
    entry = PTRH_REGIME_TABLE.get(regime_label, PTRH_REGIME_TABLE["RISK_ON"])
    reserve_pct = entry["strc_reserve"]

    deployed = strc_position_value * reserve_pct
    retained = strc_position_value - deployed
    yield_income = retained * STRC_ANNUAL_YIELD

    # Determine action description
    if reserve_pct == 0.0:
        action = "HOLD"
    elif reserve_pct >= 1.0:
        action = "DEPLOY"
    else:
        action = "DEPLOY"

    return STRCStatus(
        regime=regime_label,
        strc_sleeve_value=strc_position_value,
        reserve_pct=reserve_pct,
        deployed_notional=deployed,
        retained_notional=retained,
        annualized_yield_income=yield_income,
        last_action=action,
    )


def get_total_hedge_notional(
    ptrh_notional: float,
    strc_deployed_notional: float,
) -> float:
    """
    Total QQQ put requirement = permanent PTRH + dynamic STRC reserve.
    Both layers are additive per THB v4.0 FINAL.
    """
    return ptrh_notional + strc_deployed_notional
