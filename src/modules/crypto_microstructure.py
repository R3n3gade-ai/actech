"""
ARMS Engine: Crypto Microstructure

ARAS Sub-Module — Early Detection Radar (EDR)
Detects structural fragility in crypto derivatives plumbing that will
amplify any sell event into a liquidation cascade.

Companion to Addendum 7 (deleveraging_risk.py) — read together.
  Addendum 7: "Are institutions being forced to sell?"
  Addendum 8: "Will the market plumbing amplify selling into a cascade?"

Composite formula weights (by lead time):
  Basis Compression:          0.30  (3-7 days lead)
  Order Book Depth:           0.25  (1-3 days lead)
  Liquidation Cluster Density: 0.20  (2-5 days lead)
  Long/Short Ratio Extremity:  0.15  (3-7 days lead)
  Stablecoin Exchange Outflow: 0.10  (3-5 days lead)

Reference: ARMS Addendum 8 v1.0 — Crypto Microstructure Module Specification
"""
from dataclasses import dataclass
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class CryptoMicrostructureSignal:
    """Output from the crypto microstructure module."""
    status: str            # NORMAL | ELEVATED | WARNING | CRITICAL
    stress_score: float    # Composite 0.0-1.0
    basis_sub: float
    depth_sub: float
    liquidation_sub: float
    long_short_sub: float
    stablecoin_sub: float


class CryptoMicrostructure:
    """
    ARAS sub-module — advisory input to composite score.
    Runs every 5 minutes. Tier 0 — fully autonomous, no PM input.

    score() returns float 0.0-1.0. Higher = more structural fragility.
    Weights calibrated against Oct 10 2025 cascade anatomy.
    """

    # Weights from Addendum 8 Section 4
    W_BASIS       = 0.30
    W_DEPTH       = 0.25
    W_LIQUIDATION = 0.20
    W_LONGSHORT   = 0.15
    W_STABLECOIN  = 0.10

    # ── Signal Layer 1: Spot/Futures Basis Compression ───────────────
    # Input: annualized basis (futures premium over spot) in percent
    # Source: CME via IBKR (existing) or Binance quarterly futures

    def _basis_compression_score(self, data: Dict[str, Any]) -> float:
        basis_ann = data.get('btc_basis_annualized_pct')
        if basis_ann is None:
            # Fallback: use the 0-1 normalized BTC_FUNDING from crypto_plugin
            funding_norm = data.get('btc_funding_normalized')
            if funding_norm is not None:
                # Map 0-1 normalized back: 0.0 = deep backwardation, 1.0 = strong contango
                # Invert: low funding (backwardation) = high stress
                if funding_norm < 0.20:
                    return 0.90  # Critical / backwardation
                if funding_norm < 0.35:
                    return 0.60  # Warning — basis below ~4%
                if funding_norm < 0.45:
                    return 0.40  # Compressing — basis below ~8%
                return 0.10    # Healthy contango
            return 0.0

        # Critical / backwardation: basis negative while spot hasn't corrected
        if basis_ann < 0:
            return 0.90
        # Warning: basis < 4% — carry trade unwinding
        if basis_ann < 4:
            return 0.60
        # Compressing: basis < 8% while spot still elevated
        if basis_ann < 8:
            return 0.40
        # Healthy contango: 10-25%
        if basis_ann <= 25:
            return 0.10
        return 0.10

    # ── Signal Layer 2: Order Book Depth Deterioration ───────────────
    # Input: bid depth within 1% of mid vs 7-day average
    # Source: Binance order book API or equivalent

    def _order_book_depth_score(self, data: Dict[str, Any]) -> float:
        depth_vs_avg_pct = data.get('btc_depth_vs_7d_avg_pct')
        if depth_vs_avg_pct is None:
            return 0.0

        # depth_vs_avg_pct is how much BELOW average (positive = thinner)
        # e.g., 30 means depth is 30% below 7-day average

        # Critical: >40% below average
        if depth_vs_avg_pct > 40:
            return 0.90
        # Warning: 25-40% below — market makers withdrawing
        if depth_vs_avg_pct > 25:
            return 0.60
        # Thinning: 15-25% below without price move
        if depth_vs_avg_pct > 15:
            return 0.40
        # Normal: within ±15% of average
        return max(0.0, min(0.20, depth_vs_avg_pct / 75.0))

    # ── Signal Layer 3: Liquidation Cluster Density ──────────────────
    # Input: long liquidation cluster density within 10% downside vs 30-day avg
    # Source: Coinglass liquidation heatmap API

    def _liquidation_cluster_score(self, data: Dict[str, Any]) -> float:
        cluster_ratio = data.get('btc_liq_cluster_ratio')  # multiple of 30-day avg
        if cluster_ratio is None:
            return 0.0

        # Critical: 2.5×+ average — cascade waiting for trigger
        if cluster_ratio >= 2.5:
            return 0.90
        # Warning: 1.5× average — elevated cascade risk
        if cluster_ratio >= 1.5:
            return 0.60
        # Building: 1.2× average
        if cluster_ratio >= 1.2:
            return 0.30
        # Normal: at or below average
        return max(0.0, min(0.20, (cluster_ratio - 0.5) / 2.5))

    # ── Signal Layer 4: Long/Short Ratio Extremity ───────────────────
    # Input: 3-exchange weighted long percentage (Binance 45%, OKX 30%, Bybit 25%)
    # Source: OKX + Bybit REST APIs + CoinGlass aggregation

    def _long_short_ratio_score(self, data: Dict[str, Any]) -> float:
        long_pct = data.get('btc_long_pct_weighted')
        if long_pct is None:
            return 0.0

        # Extreme: >75% longs — cascade risk on any sell catalyst
        if long_pct > 75:
            return 0.90
        # Crowded: 70-75% — fragile one-sided market
        if long_pct > 70:
            return 0.60
        # Directional skew: 60-70%
        if long_pct > 60:
            return 0.30
        # Balanced: 45-60%
        return max(0.0, min(0.20, max(0, long_pct - 45) / 75.0))

    # ── Signal Layer 5: Stablecoin Exchange Outflow ──────────────────
    # Input: 7-day net stablecoin flow as % of total exchange reserves
    # Source: Glassnode free tier or DefiLlama

    def _stablecoin_outflow_score(self, data: Dict[str, Any]) -> float:
        outflow_pct = data.get('stablecoin_7d_outflow_pct')  # positive = outflows
        btc_price_flat = data.get('btc_price_24h_change_pct', 0.0)

        if outflow_pct is None:
            return 0.0

        # Only meaningful when BTC price is flat or rising (defensive withdrawal)
        if btc_price_flat < -5.0:
            # Price already crashing — this is reactive, not predictive
            return 0.10

        # Critical: >10% of reserves exiting
        if outflow_pct > 10:
            return 0.90
        # Warning: 5-10% — defensive withdrawal
        if outflow_pct > 5:
            return 0.60
        # Mild outflow: 3-5%
        if outflow_pct > 3:
            return 0.30
        # Normal: within ±3%
        return max(0.0, min(0.20, max(0, outflow_pct) / 15.0))

    # ── Composite Score ──────────────────────────────────────────────

    def score(self, market_data: Dict[str, Any]) -> float:
        """
        Returns float 0.0-1.0. Higher = more microstructure fragility.
        Weights calibrated against Oct 10 2025 cascade anatomy.
        """
        s1 = self._basis_compression_score(market_data)
        s2 = self._order_book_depth_score(market_data)
        s3 = self._liquidation_cluster_score(market_data)
        s4 = self._long_short_ratio_score(market_data)
        s5 = self._stablecoin_outflow_score(market_data)

        composite = (
            s1 * self.W_BASIS +
            s2 * self.W_DEPTH +
            s3 * self.W_LIQUIDATION +
            s4 * self.W_LONGSHORT +
            s5 * self.W_STABLECOIN
        )
        return max(0.0, min(1.0, composite))

    def run(self, market_data: Dict[str, Any]) -> CryptoMicrostructureSignal:
        """Full run returning detailed signal with sub-scores."""
        s1 = self._basis_compression_score(market_data)
        s2 = self._order_book_depth_score(market_data)
        s3 = self._liquidation_cluster_score(market_data)
        s4 = self._long_short_ratio_score(market_data)
        s5 = self._stablecoin_outflow_score(market_data)

        composite = max(0.0, min(1.0,
            s1 * self.W_BASIS +
            s2 * self.W_DEPTH +
            s3 * self.W_LIQUIDATION +
            s4 * self.W_LONGSHORT +
            s5 * self.W_STABLECOIN
        ))

        if composite >= 0.65:
            status = "CRITICAL"
        elif composite >= 0.40:
            status = "WARNING"
        elif composite >= 0.20:
            status = "ELEVATED"
        else:
            status = "NORMAL"

        return CryptoMicrostructureSignal(
            status=status,
            stress_score=composite,
            basis_sub=s1,
            depth_sub=s2,
            liquidation_sub=s3,
            long_short_sub=s4,
            stablecoin_sub=s5,
        )


# ── Backward-compatible wrapper ──────────────────────────────────────
_instance = CryptoMicrostructure()

def run_crypto_microstructure_check(signals=None, market_data: Optional[Dict[str, Any]] = None) -> CryptoMicrostructureSignal:
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
            if s.signal_type == 'BTC_FUNDING':
                data['btc_funding_normalized'] = s.value
            elif s.signal_type == 'BTC_BASIS_ANN':
                data['btc_basis_annualized_pct'] = s.raw_value
            elif s.signal_type == 'BTC_DEPTH_VS_AVG':
                data['btc_depth_vs_7d_avg_pct'] = s.raw_value
            elif s.signal_type == 'BTC_LIQ_CLUSTER_RATIO':
                data['btc_liq_cluster_ratio'] = s.raw_value
            elif s.signal_type == 'BTC_LONG_PCT':
                data['btc_long_pct_weighted'] = s.raw_value
            elif s.signal_type == 'STABLECOIN_7D_OUTFLOW':
                data['stablecoin_7d_outflow_pct'] = s.raw_value
            elif s.signal_type == 'BTC_OI':
                # Legacy OI stress from crypto_plugin — use as rough proxy
                data['btc_funding_normalized'] = data.get('btc_funding_normalized', 0.5)
    return _instance.run(data)
