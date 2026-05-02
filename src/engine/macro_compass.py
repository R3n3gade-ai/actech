"""
ARMS Engine: Macro Compass (L2)

Calculates the Macro Regime Score (0.0 to 1.0) from incoming data pipeline
signals plus a typed macro-event state. Five sub-scores per canonical spec
(ARMS_Auto_Deployment_Execution_v1.1.txt §3.2 Composite Score Inputs):

  Sub-Score              | Inputs                                         | Weight
  Credit Stress Index    | HY OAS, IG (BBB) OAS, AAA OAS                  | 0.25
  Volatility Regime      | VIX level, VIX/VIX3M term, realized vol (SPY)  | 0.25
  Macro Momentum         | PMI, yield curve T10Y2Y, Fed Funds 3m change   | 0.20
  Liquidity Conditions   | Fed BS 4w Δ, SOFR-Tbill repo, DXY 20d Δ        | 0.20
  Equity Internals       | Breadth, dispersion, sector rotation           | 0.10

Each sub-score blends its available constituents with intra-sub-score weights
that re-normalize when an input is missing (NaN) — a missing input contributes
0 to its sub-score but does not zero out the rest of the sub-score.
"""

import math
from typing import Iterable, List, Optional, Tuple

from data_feeds.interfaces import SignalRecord
from data_feeds.macro_event_state import MacroEventState


def _extract_required(signals: List[SignalRecord], signal_type: str) -> float:
    """Return float for a REQUIRED signal. Raise RuntimeError if missing or NaN."""
    for s in signals:
        if s.signal_type == signal_type:
            val = s.raw_value if s.raw_value is not None else s.value
            try:
                fval = float(val)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    f"Required macro signal '{signal_type}' is non-numeric: {val!r}"
                ) from exc
            if math.isnan(fval):
                raise RuntimeError(
                    f"Required macro signal '{signal_type}' is NaN — no fallback permitted."
                )
            return fval
    raise RuntimeError(
        f"Required macro signal '{signal_type}' is absent from pipeline — no fallback permitted."
    )


def _extract_optional(signals: List[SignalRecord], signal_type: str) -> Optional[float]:
    """Return float if signal present and non-NaN, else None (treated as missing)."""
    for s in signals:
        if s.signal_type == signal_type:
            val = s.raw_value if s.raw_value is not None else s.value
            try:
                fval = float(val)
            except (TypeError, ValueError):
                return None
            if math.isnan(fval):
                return None
            return fval
    return None


def _blend(items: Iterable[Tuple[Optional[float], float]]) -> float:
    """Weighted blend with missing-input renormalization.

    items: iterable of (stress_value_or_None, intra_weight) pairs.
    If a stress value is None, that pair is dropped and the remaining weights
    are renormalized to sum to 1.0. If all are missing, returns 0.0.
    """
    avail = [(v, w) for (v, w) in items if v is not None and w > 0.0]
    if not avail:
        return 0.0
    wsum = sum(w for _, w in avail)
    if wsum <= 0.0:
        return 0.0
    return sum(max(0.0, min(1.0, v)) * w for v, w in avail) / wsum


def calculate_macro_regime_score(signals: List[SignalRecord], event_state: Optional[MacroEventState] = None) -> float:
    """Compute composite regime score in [0.0, 1.0] per Auto Deployment §3.2."""
    if not signals:
        raise RuntimeError(
            "MacroCompass received empty signal pipeline. "
            "Cannot compute regime score without live market data. "
            "Check data feed connections (FRED, Binance, PMI)."
        )

    # ── Required core inputs — raise on missing/NaN. No defaults. ──
    vix       = _extract_required(signals, 'VIX_INDEX')
    hy_spread = _extract_required(signals, 'HY_CREDIT_SPREAD')   # in %
    pmi       = _extract_required(signals, 'PMI_NOWCAST')
    ten_y     = _extract_required(signals, '10Y_TREASURY_YIELD')  # noqa: F841 (reserved for spec extensions)

    # ── Optional advisory inputs (None when source unavailable). ──
    t10y2y_opt    = _extract_optional(signals, 'YIELD_CURVE_T10Y2Y')
    margin_qoq    = _extract_optional(signals, 'MARGIN_DEBT_QOQ_PCT')
    cboe_skew     = _extract_optional(signals, 'CBOE_SKEW')

    # ── Auto Deployment §3.2 expanded inputs (NaN-aware: optional) ──
    ig_oas      = _extract_optional(signals, 'IG_OAS_PCT')             # %
    aaa_oas     = _extract_optional(signals, 'AAA_OAS_PCT')            # %
    vix3m       = _extract_optional(signals, 'VIX3M')                  # index
    realized_v  = _extract_optional(signals, 'REALIZED_VOL_20D')       # annualized %
    fed_bs_4w   = _extract_optional(signals, 'FED_BS_4W_PCT')          # % change
    repo_stress = _extract_optional(signals, 'REPO_STRESS_BPS')        # bps
    dxy_20d     = _extract_optional(signals, 'DXY_20D_PCT')            # % change
    dff_3m_bps  = _extract_optional(signals, 'DFF_3M_CHANGE_BPS')      # bps
    breadth     = _extract_optional(signals, 'BREADTH_PCT_ABOVE_50DMA') # 0..100
    dispersion  = _extract_optional(signals, 'CROSS_SECTIONAL_DISPERSION_PCT')  # %
    sector_rot  = _extract_optional(signals, 'SECTOR_ROTATION_SPREAD_PCT')  # % (max-min sector 20d return)

    # ─────────────────────────────────────────────────────────────────────
    # 1. Credit Stress Index (0.25) — HY OAS, IG OAS, AAA OAS
    # Scales calibrated to historical recession-tier thresholds (saturation at
    # ~2020 COVID, not 2008 GFC) so a real-but-narrow crisis correctly registers.
    # ─────────────────────────────────────────────────────────────────────
    # HY OAS: 300bps → 0,  800bps → 1.0   (2020 COVID peak ≈ 1100bps)
    hy_stress = (hy_spread - 3.0) / 5.0
    # IG (BBB) OAS: 100bps → 0,  400bps → 1.0   (2020 COVID peak ≈ 370bps)
    ig_stress = ((ig_oas - 1.0) / 3.0) if ig_oas is not None else None
    # AAA OAS: 30bps → 0,  150bps → 1.0   (TED-replacement; funding stress)
    aaa_stress = ((aaa_oas - 0.30) / 1.20) if aaa_oas is not None else None
    credit_stress_score = _blend([
        (hy_stress,  0.50),
        (ig_stress,  0.30),
        (aaa_stress, 0.20),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # 2. Volatility Regime (0.25) — VIX level, VIX/VIX3M term, realized vol
    # ─────────────────────────────────────────────────────────────────────
    # VIX: 10 → 0,  45 → 1.0   (2020 COVID peak ≈ 82; 2008 peak ≈ 89)
    v_stress = (vix - 10.0) / 35.0
    # Term structure: VIX > VIX3M = backwardation = acute near-term stress.
    # Ratio 1.0 = neutral, 1.30 = severe backwardation.
    if vix3m is not None and vix3m > 0:
        ratio = vix / vix3m
        ts_stress = (ratio - 1.0) / 0.30
    else:
        ts_stress = None
    # Realized 20d vol (annualized %): 10% → 0,  40% → 1.0
    rv_stress = ((realized_v - 10.0) / 30.0) if realized_v is not None else None
    volatility_regime_score = _blend([
        (v_stress,  0.50),
        (ts_stress, 0.25),
        (rv_stress, 0.25),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # 3. Macro Momentum (0.20) — PMI, yield curve, rate-hike velocity
    # ─────────────────────────────────────────────────────────────────────
    p_stress = (55.0 - pmi) / 10.0
    # Yield curve: T10Y2Y +0.50 → 0,  -0.50 → 1.0  (deep inversion = recession)
    yc_stress = ((0.50 - t10y2y_opt) / 1.0) if t10y2y_opt is not None else None
    # Fed Funds 3m change: 0bps → 0,  +150bps in 3m → 1.0  (rapid hike compression)
    rh_stress = (dff_3m_bps / 150.0) if dff_3m_bps is not None else None
    macro_momentum_score = _blend([
        (p_stress,  0.50),
        (yc_stress, 0.30),
        (rh_stress, 0.20),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # 4. Liquidity Conditions (0.20) — Fed balance sheet Δ, repo rates, dollar index
    # Spec (Auto Deployment v1.1 §3.2 Table 2): three inputs only.
    # Equal-weighted (spec does not assign intra-weights).
    # ─────────────────────────────────────────────────────────────────────
    # Fed BS 4w % change: +0.5% → 0 (QE),  -1.5% → 1.0 (rapid QT/liquidity drain)
    if fed_bs_4w is not None:
        fbs_stress = (0.5 - fed_bs_4w) / 2.0
    else:
        fbs_stress = None
    # Repo stress (SOFR-Tbill): 0bps → 0,  +25bps wide → 1.0
    repo_s = (repo_stress / 25.0) if repo_stress is not None else None
    # DXY 20d change: 0% → 0,  +5% → 1.0  (USD funding squeeze)
    dxy_s = (dxy_20d / 5.0) if dxy_20d is not None else None
    liquidity_conditions_score = _blend([
        (fbs_stress, 1.0/3.0),
        (repo_s,     1.0/3.0),
        (dxy_s,      1.0/3.0),
    ])

    # ─────────────────────────────────────────────────────────────────────
    # 5. Equity Internals (0.10) — breadth, momentum dispersion, sector rotation
    # Spec (Auto Deployment v1.1 §3.2 Table 2): three inputs.
    # Equal-weighted (spec does not assign intra-weights).
    # ─────────────────────────────────────────────────────────────────────
    # Breadth: 70% above 50DMA → 0 (healthy),  20% → 1.0 (broad weakness)
    breadth_stress = ((70.0 - breadth) / 50.0) if breadth is not None else None
    # Dispersion: 3% → 0,  12% → 1.0  (wide dispersion = thesis fragmentation)
    disp_stress = ((dispersion - 3.0) / 9.0) if dispersion is not None else None
    # Sector rotation: max-min spread of 20d sector returns. 5% → 0,  20% → 1.0
    # (wide sector spread = active rotation = late-cycle / risk-off)
    sector_stress = ((sector_rot - 5.0) / 15.0) if sector_rot is not None else None
    equity_internals_score = _blend([
        (breadth_stress, 1.0/3.0),
        (disp_stress,    1.0/3.0),
        (sector_stress,  1.0/3.0),
    ])

    base_score = (
        (credit_stress_score        * 0.25) +
        (volatility_regime_score    * 0.25) +
        (macro_momentum_score       * 0.20) +
        (liquidity_conditions_score * 0.20) +
        (equity_internals_score     * 0.10)
    )

    # Advisory adder 1: margin debt QoQ — BIDIRECTIONAL (Addendum 7 §3.4).
    # Skipped entirely if signal is missing (no fallback to 0).
    margin_adder = 0.0
    if margin_qoq is not None:
        if margin_qoq < 0.0:
            margin_adder = min(0.05, (-margin_qoq) / 10.0 * 0.05)
        elif margin_qoq > 5.0:
            margin_adder = min(0.05, (margin_qoq - 5.0) / 10.0 * 0.05)

    # Advisory adder 2: CBOE SKEW (Addendum 7 §3.3 Phase 1).
    # Skipped entirely if signal is missing (no fallback to 120).
    skew_adder = 0.0
    if cboe_skew is not None and cboe_skew > 130.0:
        skew_adder = min(0.05, (cboe_skew - 130.0) / 20.0 * 0.05)

    composite = base_score + margin_adder + skew_adder

    if event_state is None:
        event_state = MacroEventState()

    overlay = max(0.0, min(1.0, float(event_state.override_floor)))
    score = max(composite, overlay)

    return max(0.0, min(1.0, score))


def get_regime_label(score: float) -> str:
    """Map composite score to regime label per CLAUDE.md v4.0 REGIME DEFINITIONS.

    Boundaries match ARAS canonical spec (Addendum 1 §1.2):
      RISK_ON   ≤ 0.30
      WATCH     0.31 – 0.50
      NEUTRAL   0.51 – 0.65
      DEFENSIVE 0.66 – 0.80
      CRASH     > 0.80
    """
    if score <= 0.30:
        return "RISK_ON"
    if score <= 0.50:
        return "WATCH"
    if score <= 0.65:
        return "NEUTRAL"
    if score <= 0.80:
        return "DEFENSIVE"
    return "CRASH"
