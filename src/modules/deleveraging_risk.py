"""
ARMS Engine: Deleveraging Risk

ARAS Sub-Module — Early Detection Radar (EDR)
Detects institutional forced selling before price confirms it across
five signal layers calibrated against the October 10, 2025 crypto crash.

Composite formula weights (by lead time):
  Funding Rate Stress:   0.35  (7-9 days lead)
  OI Fragility:          0.30  (5-7 days lead)
  Options Skew:          0.15  (2-4 days lead)
  Margin Debt:           0.12  (weeks — structural)
  BTC/QQQ Correlation:   0.08  (<24h — confirmation)

Reference: ARMS Addendum 7 v1.0 — Deleveraging Risk Module Specification
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class DeleveragingSignal:
    """Output from the deleveraging risk module."""
    status: str            # NORMAL | WATCH | ELEVATED | CRITICAL
    velocity_score: float  # Composite 0.0-1.0
    funding_rate_sub: float
    oi_fragility_sub: float
    skew_sub: float
    margin_debt_sub: float
    correlation_sub: float


class DeleveragingRisk:
    """
    ARAS sub-module — advisory input to composite score.
    Runs every 5 minutes. Tier 0 — fully autonomous, no PM input.

    score() returns float 0.0-1.0. Higher = more deleveraging risk.
    Weights calibrated against Oct 10 2025 event.
    """

    # Weights from Addendum 7 Section 4
    W_FUNDING   = 0.35
    W_OI        = 0.30
    W_SKEW      = 0.15
    W_MARGIN    = 0.12
    W_CORR      = 0.08

    # ── Signal Layer 1: Funding Rate Stress ──────────────────────────
    # Input: BTC_FUNDING_RATE_ANN — annualized perpetual funding rate (%)
    # Source: CoinGlass aggregated or CME basis proxy

    def _funding_rate_score(self, data: Dict[str, Any]) -> float:
        rate = data.get('btc_funding_rate_ann')
        rate_delta = data.get('btc_funding_rate_8h_delta')  # single 8h window change

        if rate is None:
            return 0.0

        # Critical override: rate goes negative — longs being liquidated at scale
        # (Spec §4 Table 11: "Critical || Rate goes negative || 0.8 – 1.0")
        if rate < 0:
            return 0.90

        # Warning override: 8h funding collapse > 0.04%
        # (Spec §4 Table 11: "Warning || Rate collapses >0.04% in single 8h window || 0.5 – 0.7")
        if rate_delta is not None and rate_delta < -0.04:
            return 0.65

        # Calibration anchored to Spec Table 14 (the canonical Oct 10 2025 reference):
        #   17–22% sustained → ~0.45 sub-score
        #   30% sustained    → ~0.75 sub-score  ← Spec §6 recalibration: 30% → 25%
        # Applying the §6 recalibration knob with linear interpolation:
        #   rate ≤ 0    → 0.0
        #   0–10%       → 0.0 → 0.20    (Normal band, Table 11)
        #   10–20%      → 0.20 → 0.45   (Elevated low — building leverage)
        #   20–25%      → 0.45 → 0.75   (Elevated peak per recalibration)
        #   ≥25%        → 0.75          (capped — collapse-trigger handled above)
        if rate >= 25.0:
            return 0.75
        if rate >= 20.0:
            return 0.45 + (rate - 20.0) / 5.0 * 0.30   # 0.45 → 0.75
        if rate >= 10.0:
            return 0.20 + (rate - 10.0) / 10.0 * 0.25  # 0.20 → 0.45
        return max(0.0, rate / 10.0 * 0.20)            # 0.00 → 0.20

    # ── Signal Layer 2: Open Interest Fragility ──────────────────────
    # Input: btc_oi_current, btc_oi_30d_high, btc_oi_24h_change_pct, btc_price_24h_change_pct

    def _oi_fragility_score(self, data: Dict[str, Any]) -> float:
        oi_current = data.get('btc_oi_current')
        oi_30d_high = data.get('btc_oi_30d_high')
        oi_24h_change = data.get('btc_oi_24h_change_pct', 0.0)
        price_24h_change = data.get('btc_price_24h_change_pct', 0.0)
        funding_rate_ann = data.get('btc_funding_rate_ann') or 0.0

        if oi_current is None:
            return 0.0

        # Spec §3.2 + §4: Forced liquidation — OI drops >15% in 24h
        if oi_24h_change < -15.0:
            return 0.90

        # Spec §3.2: Stealth unwinding — OI drops >8% while price flat or rising
        if oi_24h_change < -8.0 and price_24h_change >= -1.0:
            return 0.70

        # Spec §4 table: "Peak fragility — OI at 30-day high while funding rate
        # above 20% ann. || 0.4 – 0.6". Gradient based on (a) closeness to 30d high
        # and (b) magnitude of funding rate above the 20% threshold.
        if oi_30d_high is not None and oi_30d_high > 0:
            oi_ratio = oi_current / oi_30d_high
            if oi_ratio >= 0.95 and funding_rate_ann >= 20:
                # Map ratio 0.95 → 1.00 to a 0.40 → 0.50 baseline, plus a funding
                # cofactor: each percentage point of funding above 20% adds 0.01,
                # capped at the spec band ceiling of 0.60 (5 pts above 20%).
                ratio_term = 0.40 + min(0.10, (oi_ratio - 0.95) / 0.05 * 0.10)
                fund_term = min(0.10, max(0.0, (funding_rate_ann - 20.0) / 5.0 * 0.10))
                return min(0.60, ratio_term + fund_term)

        # Normal: OI well below 30-day high or funding rate not elevated
        return 0.10

    # ── Signal Layer 3: Options Skew / Dealer Gamma ──────────────────
    # Phase 1: CBOE SKEW index (^SKEW via Yahoo Finance)
    # Phase 2: Polygon.io 25-delta QQQ put/call skew

    def _skew_score(self, data: Dict[str, Any]) -> float:
        skew = data.get('cboe_skew_index')
        if skew is None:
            return 0.0

        # Critical: SKEW > 150 — amplification regime active
        if skew > 150:
            return 0.90
        # Warning: 140-150 — dealers short gamma
        if skew > 140:
            return 0.60
        # Elevated: 125-140 — institutions buying downside
        if skew > 125:
            return 0.40
        # Normal: 100-125
        return max(0.0, min(0.20, (skew - 100) / 125.0))

    # ── Signal Layer 4: Margin Debt Structural Fragility ─────────────
    # Input: FRED MARGDEBT series, 3-month rate of change

    def _margin_debt_score(self, data: Dict[str, Any]) -> float:
        margin_3m_growth = data.get('margin_debt_3m_growth_pct')
        margin_mom_change = data.get('margin_debt_mom_change_pct')

        if margin_3m_growth is None:
            return 0.0

        # Deleveraging: MoM contraction after rapid growth
        if margin_mom_change is not None and margin_mom_change < 0 and margin_3m_growth > 10:
            return 0.85

        # Fragile: 3-month growth > 20%
        if margin_3m_growth > 20:
            return 0.60
        # Elevated: 10-20%
        if margin_3m_growth > 10:
            return 0.30
        # Normal: 0-10%
        return max(0.0, min(0.20, margin_3m_growth / 50.0))

    # ── Signal Layer 5: BTC/QQQ Correlation Spike ────────────────────
    # 5-day rolling Pearson correlation — confirmation signal only

    def _correlation_score(self, data: Dict[str, Any]) -> float:
        corr = data.get('btc_qqq_corr_5d')
        if corr is None:
            return 0.0

        # Critical: correlation > 0.85 and rising — liquidation underway
        if corr > 0.85:
            return 0.90
        # Warning: > 0.70 — unified positioning
        if corr > 0.70:
            return 0.60
        # Elevated: 0.50-0.70 — same capital flows
        if corr > 0.50:
            return 0.35
        # Normal: 0.20-0.50
        return max(0.0, min(0.20, corr / 2.5))

    # ── Composite Score ──────────────────────────────────────────────

    def score(self, market_data: Dict[str, Any]) -> float:
        """
        Returns float 0.0-1.0. Higher = more deleveraging risk.
        Weights calibrated against Oct 10 2025 event.

        Canonical Addendum 7 §4 weighted sum. None inputs yield sub-score 0.0
        (live system MUST provide all 5 layers for spec-compliant operation).
        """
        s1 = self._funding_rate_score(market_data)
        s2 = self._oi_fragility_score(market_data)
        s3 = self._skew_score(market_data)
        s4 = self._margin_debt_score(market_data)
        s5 = self._correlation_score(market_data)

        composite = (
            s1 * self.W_FUNDING +
            s2 * self.W_OI +
            s3 * self.W_SKEW +
            s4 * self.W_MARGIN +
            s5 * self.W_CORR
        )
        return max(0.0, min(1.0, composite))

    def run(self, market_data: Dict[str, Any]) -> DeleveragingSignal:
        """Full run returning detailed signal with sub-scores."""
        s1 = self._funding_rate_score(market_data)
        s2 = self._oi_fragility_score(market_data)
        s3 = self._skew_score(market_data)
        s4 = self._margin_debt_score(market_data)
        s5 = self._correlation_score(market_data)

        composite = self.score(market_data)

        if composite >= 0.65:
            status = "CRITICAL"
        elif composite >= 0.40:
            status = "ELEVATED"
        elif composite >= 0.20:
            status = "WATCH"
        else:
            status = "NORMAL"

        return DeleveragingSignal(
            status=status,
            velocity_score=composite,
            funding_rate_sub=s1,
            oi_fragility_sub=s2,
            skew_sub=s3,
            margin_debt_sub=s4,
            correlation_sub=s5,
        )


# ── Backward-compatible wrapper ──────────────────────────────────────
_instance = DeleveragingRisk()

def run_deleveraging_check(signals=None, market_data: Optional[Dict[str, Any]] = None) -> DeleveragingSignal:
    """
    Backward-compatible entry point called from main.py.
    Accepts either raw SignalRecords (legacy) or structured market_data dict.
    """
    if market_data is not None:
        return _instance.run(market_data)

    # Legacy path: extract what we can from SignalRecord list
    data: Dict[str, Any] = {}
    if signals:
        for s in signals:
            if s.signal_type == 'BTC_FUNDING_RATE_ANN':
                data['btc_funding_rate_ann'] = s.raw_value
            elif s.signal_type == 'BTC_OI':
                data['btc_oi_current'] = s.raw_value
            elif s.signal_type == 'CBOE_SKEW':
                data['cboe_skew_index'] = s.raw_value
            elif s.signal_type == 'MARGIN_DEBT_3M_GROWTH':
                data['margin_debt_3m_growth_pct'] = s.raw_value
            elif s.signal_type == 'BTC_QQQ_CORR_5D':
                data['btc_qqq_corr_5d'] = s.value
    return _instance.run(data)
