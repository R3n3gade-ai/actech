"""
ARMS Intelligence Layer: Gate 3 Supplementary Scoring

Aggregates Phase 2 anticipatory signals (ELVT, JPVI, PFVT, SCCR) into
a supplementary score adjustment for MICS Gate 3.

The Phase 2 signals provide pre-consensus intelligence that adjusts
the base Gate 3 mispricing score (0-30). The supplementary score is
an additive adjustment (-5 to +5) based on the weight and direction
of anticipatory signals affecting each position.

Per Addendum 3, Section 3: "Signal combination weights for multi-signal
scoring feed forward into MICS Gate 3 supplementary scoring."

The quarterly learning loop (CCM) will continuously recalibrate these
signal weights based on their actual predictive accuracy.

Reference: Addendum 3, Sections 2.1-2.4 and Section 3
"""

import json
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from intelligence.elvt import get_elvt_signals_for_position, ELVTSignal
from intelligence.jpvi import get_jpvi_signals_for_position, JPVISignal
from intelligence.pfvt import get_pfvt_signals_for_position, PFVTSignal
from intelligence.sccr import get_sccr_signals_for_position, SCCRSignal


@dataclass
class Gate3SupplementaryResult:
    """Output of the supplementary scoring for a single ticker."""
    ticker: str
    adjustment: float          # Additive adjustment to Gate 3 score (-5 to +5)
    elvt_contribution: float   # ELVT component of adjustment
    jpvi_contribution: float   # JPVI component of adjustment
    pfvt_contribution: float   # PFVT component of adjustment
    sccr_contribution: float   # SCCR component of adjustment
    signal_count: int          # Total number of Phase 2 signals considered
    material_signal_count: int # CRITICAL + HIGH severity signals
    explanation: str


# --- Signal Weight Configuration ---
# These weights determine how much each signal type contributes to the
# Gate 3 supplementary adjustment. Updated quarterly by the learning loop.

_WEIGHT_PATH = os.path.join('achelion_arms', 'state', 'gate3_signal_weights.json')

_DEFAULT_WEIGHTS = {
    'elvt': 0.35,   # Earnings language — highest weight, most direct indicator
    'jpvi': 0.30,   # Job postings — strong leading indicator of capex
    'pfvt': 0.15,   # Patents — long-term, lower frequency
    'sccr': 0.20,   # Supply chain — physical reality of investment decisions
}

# Severity multipliers determine signal impact magnitude
_SEVERITY_MULTIPLIERS = {
    'CRITICAL': 1.0,
    'HIGH': 0.7,
    'MEDIUM': 0.3,
    'LOW': 0.0,
}

# Direction multipliers (positive = bullish for position, negative = bearish)
_DIRECTION_MULTIPLIERS = {
    'ACCELERATING': 1.0,
    'INCREASING': 0.6,
    'IMPROVING': 0.6,
    'ELEVATED': 0.3,
    'STABLE': 0.0,
    'SOFTENING': -0.5,
    'DECLINING': -0.8,
    'DETERIORATING': -1.0,
}


def _load_signal_weights() -> Dict[str, float]:
    """
    Load signal weights from CCM calibration state.
    Falls back to defaults if no calibration exists.
    """
    if os.path.exists(_WEIGHT_PATH):
        try:
            with open(_WEIGHT_PATH, 'r') as f:
                weights = json.load(f)
                # Validate all keys present
                for key in _DEFAULT_WEIGHTS:
                    if key not in weights:
                        weights[key] = _DEFAULT_WEIGHTS[key]
                return weights
        except Exception:
            pass
    return dict(_DEFAULT_WEIGHTS)


def _score_signals(signals, weight: float) -> float:
    """
    Convert a list of signals into a weighted contribution score.

    Each signal contributes: severity_mult * direction_mult * weight
    The final score is the average across all signals, scaled by weight.

    Returns a value typically in range [-weight, +weight].
    """
    if not signals:
        return 0.0

    total = 0.0
    counted = 0

    for sig in signals:
        severity = getattr(sig, 'severity', 'LOW')
        direction = getattr(sig, 'direction', 'STABLE')

        sev_mult = _SEVERITY_MULTIPLIERS.get(severity, 0.0)
        dir_mult = _DIRECTION_MULTIPLIERS.get(direction, 0.0)

        if sev_mult > 0:  # Only count signals with material severity
            total += sev_mult * dir_mult
            counted += 1

    if counted == 0:
        return 0.0

    avg_signal = total / counted
    return avg_signal * weight


def calculate_gate3_supplementary(ticker: str) -> Gate3SupplementaryResult:
    """
    Calculate the Gate 3 supplementary score adjustment for a portfolio position.

    Aggregates all Phase 2 anticipatory signals (ELVT, JPVI, PFVT, SCCR) into
    a single additive adjustment to the base Gate 3 mispricing score.

    The adjustment is clamped to [-5, +5] to prevent any single category
    of signal from dominating the MICS score.

    Args:
        ticker: Portfolio position ticker (e.g., 'MU', 'NVDA')

    Returns:
        Gate3SupplementaryResult with the computed adjustment and breakdown.
    """
    weights = _load_signal_weights()

    # Gather signals from all Phase 2 modules
    elvt_signals = get_elvt_signals_for_position(ticker)
    jpvi_signals = get_jpvi_signals_for_position(ticker)
    pfvt_signals = get_pfvt_signals_for_position(ticker)
    sccr_signals = get_sccr_signals_for_position(ticker)

    # Score each signal category
    # Scale factor: max per-category contribution is ±5 * category_weight
    # With 4 categories summing to 1.0, max total is ±5
    scale = 5.0

    elvt_contrib = _score_signals(elvt_signals, weights['elvt']) * scale
    jpvi_contrib = _score_signals(jpvi_signals, weights['jpvi']) * scale
    pfvt_contrib = _score_signals(pfvt_signals, weights['pfvt']) * scale
    sccr_contrib = _score_signals(sccr_signals, weights['sccr']) * scale

    raw_adjustment = elvt_contrib + jpvi_contrib + pfvt_contrib + sccr_contrib

    # Clamp to [-5, +5] range
    adjustment = max(-5.0, min(5.0, raw_adjustment))

    # Count signals
    all_signals = elvt_signals + jpvi_signals + pfvt_signals + sccr_signals
    signal_count = len(all_signals)
    material_count = sum(
        1 for s in all_signals
        if getattr(s, 'severity', 'LOW') in ('CRITICAL', 'HIGH')
    )

    # Build explanation
    parts = []
    if elvt_contrib != 0:
        parts.append(f"ELVT {elvt_contrib:+.2f}")
    if jpvi_contrib != 0:
        parts.append(f"JPVI {jpvi_contrib:+.2f}")
    if pfvt_contrib != 0:
        parts.append(f"PFVT {pfvt_contrib:+.2f}")
    if sccr_contrib != 0:
        parts.append(f"SCCR {sccr_contrib:+.2f}")

    if parts:
        explanation = f"Gate 3 supplementary: {adjustment:+.2f} ({', '.join(parts)}). " \
                      f"{signal_count} signals, {material_count} material."
    else:
        explanation = f"No Phase 2 anticipatory signals available for {ticker}."

    return Gate3SupplementaryResult(
        ticker=ticker,
        adjustment=round(adjustment, 2),
        elvt_contribution=round(elvt_contrib, 2),
        jpvi_contribution=round(jpvi_contrib, 2),
        pfvt_contribution=round(pfvt_contrib, 2),
        sccr_contribution=round(sccr_contrib, 2),
        signal_count=signal_count,
        material_signal_count=material_count,
        explanation=explanation,
    )
