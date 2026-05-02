"""
ARMS Engine: Master Engine (L4 Portfolio Construction)

Translates abstract MICS conviction scores into actual portfolio allocation 
percentages (Architecture AB 58/20/14/8), modulated by CDF decay and 
capped by ARAS/PDS ceilings and Kevlar limits.

Per THB v4.0 FINAL §4: effective_ceiling = min(aras_ceiling, pds_ceiling).
PDS and ARAS are independent peers. The lower ceiling always prevails.
This computation lives here, NOT inside the PDS dataclass.

Reference: ARMS FSD v1.1, Section 1 & 7; THB v4.0 FINAL_1 §4
"""
from typing import Dict
from .kevlar import apply_kevlar_limits


def compute_effective_ceiling(
    aras_ceiling: float,
    pds_ceiling: float = 1.0,
    slof_multiplier: float = 1.0,
) -> float:
    """
    THB v4.0 FINAL §4: effective_ceiling = min(aras_ceiling, pds_ceiling).
    SLOF multiplier (1.0 or 1.25) is applied to ARAS only — never relaxes PDS.
    Hard cap at 1.25 (max SLOF lift).
    """
    aras_with_slof = min(aras_ceiling * slof_multiplier, 1.25)
    return min(aras_with_slof, pds_ceiling)


def compute_target_weights(
    mics_scores: Dict[str, int],
    cdf_multipliers: Dict[str, float],
    aras_ceiling: float,
    pds_ceiling: float = 1.0,
    slof_multiplier: float = 1.0,
    equity_sleeve_pct: float = 1.0,
) -> Dict[str, float]:
    """
    Converts 1-10 MICS scores into Conviction-Squared (C^2) target weights,
    applies CDF performance decay, and scales to the effective ceiling.

    Parameters
    ----------
    mics_scores : MICS conviction (1-10) per ticker
    cdf_multipliers : Per-ticker CDF decay factor (1.0 = no decay)
    aras_ceiling : Current ARAS regime ceiling (e.g. 1.00 RISK_ON, 0.40 DEFENSIVE)
    pds_ceiling : Current PDS ceiling (1.0 NORMAL/WARNING, 0.60 D1, 0.30 D2)
    slof_multiplier : 1.0 or 1.25 — only applied to ARAS, never PDS
    equity_sleeve_pct : Architecture AB equity sleeve target (default 1.0;
        pass 0.58 to scale weights to the 58% sleeve target).

    Returns
    -------
    Dict[ticker, weight as fraction of total NAV]
    """
    target_weights: Dict[str, float] = {}
    total_c_squared = 0.0

    effective_scores = {}
    for ticker, mics_level in mics_scores.items():
        decay = cdf_multipliers.get(ticker, 1.0)
        c_squared = (mics_level ** 2) * decay
        effective_scores[ticker] = c_squared
        total_c_squared += c_squared

    if total_c_squared == 0:
        return {t: 0.0 for t in mics_scores.keys()}

    effective_ceiling = compute_effective_ceiling(aras_ceiling, pds_ceiling, slof_multiplier)

    for ticker, c_squared in effective_scores.items():
        raw_equity_weight = c_squared / total_c_squared
        scaled_weight = raw_equity_weight * effective_ceiling * equity_sleeve_pct
        final_weight = apply_kevlar_limits(scaled_weight)
        target_weights[ticker] = final_weight

    return target_weights
