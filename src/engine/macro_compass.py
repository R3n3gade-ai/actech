"""
ARMS Engine: Macro Compass (L2)

Calculates the Macro Regime Score (0.0 to 1.0) from incoming data pipeline
signals plus a typed macro-event state. This replaces the prior opaque
LLM-event-overlay shortcut with a deterministic, auditable interim path.
"""

from typing import List, Optional

from data_feeds.interfaces import SignalRecord
from data_feeds.macro_event_state import MacroEventState


def _extract_raw(signals: List[SignalRecord], signal_type: str, default: float) -> float:
    for s in signals:
        if s.signal_type == signal_type:
            return float(s.raw_value if s.raw_value is not None else s.value)
    return default




def calculate_macro_regime_score(signals: List[SignalRecord], event_state: Optional[MacroEventState] = None) -> float:
    """
    Computes a broader macro regime score from sourced inputs.

    Inputs currently used:
    - VIX
    - HY credit spreads
    - PMI
    - 10Y Treasury yield
    - typed macro-event state (deterministic interim replacement for opaque LLM overlay)
    """
    if not signals:
        raise RuntimeError(
            "MacroCompass received empty signal pipeline. "
            "Cannot compute regime score without live market data. "
            "Check data feed connections (FRED, Binance, PMI)."
        )

    signal_types = {s.signal_type for s in signals}
    required = {'VIX_INDEX', 'HY_CREDIT_SPREAD', 'PMI_NOWCAST', '10Y_TREASURY_YIELD'}
    missing = required - signal_types
    if missing:
        print(f"[MacroCompass] WARNING: Missing signals {missing} — using defaults for those inputs.")

    vix = _extract_raw(signals, 'VIX_INDEX', 20.0)
    hy_spread = _extract_raw(signals, 'HY_CREDIT_SPREAD', 4.0)
    pmi = _extract_raw(signals, 'PMI_NOWCAST', 50.0)
    ten_y = _extract_raw(signals, '10Y_TREASURY_YIELD', 4.0)

    # Core stress normalization
    v_stress = max(0.0, min(1.0, (vix - 10.0) / 35.0))
    h_stress = max(0.0, min(1.0, (hy_spread - 3.0) / 5.0))
    p_stress = max(0.0, min(1.0, (55.0 - pmi) / 10.0))
    y_stress = max(0.0, min(1.0, (ten_y - 3.5) / 2.0))

    base_score = (
        (v_stress * 0.30) +
        (h_stress * 0.30) +
        (p_stress * 0.20) +
        (y_stress * 0.20)
    )

    if event_state is None:
        event_state = MacroEventState()

    overlay = max(0.0, min(1.0, float(event_state.override_floor)))
    score = max(base_score, overlay)

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
