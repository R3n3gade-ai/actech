"""
ARMS Data Feeds: CoinGlass Plugin — Single Crypto Derivatives Feed

CoinGlass is the sole crypto data API for ARMS. All crypto derivatives
microstructure data flows through this plugin:
  - Aggregated funding rates (BTC perpetual, OI-weighted across exchanges)
  - Open interest (aggregated across all exchanges)
  - Liquidation data (24h volume, long/short split)
  - Long/short account ratio (aggregated across exchanges)
  - BTC price and 24h change

Used by:
  - deleveraging_risk.py (Addendum 7): Funding Rate, OI Fragility
  - crypto_microstructure.py (Addendum 8): Liquidation Clusters, Long/Short Ratio
  - regime_probability.py: LIQ_VOL_24H velocity signal

Live data source. No mocks. No fallbacks.
"""
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional
from urllib.request import Request, urlopen

from data_feeds.interfaces import FeedPlugin, SignalRecord

logger = logging.getLogger(__name__)

_BASE = "https://open-api.coinglass.com/public/v2"


class CoinglassPlugin(FeedPlugin):
    """
    Single crypto derivatives data feed via CoinGlass public API.
    Free tier — no authentication required.
    """

    @property
    def name(self) -> str:
        return "COINGLASS"

    def _get(self, path: str) -> Optional[dict]:
        """GET request to CoinGlass API."""
        url = f"{_BASE}/{path}"
        req = Request(url, headers={
            "User-Agent": "Achelion-ARMS/1.0",
            "Accept": "application/json",
        })
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("success") is False:
            logger.warning("[%s] API error: %s", self.name, data.get("msg"))
            return None
        return data.get("data")

    # ── Funding Rates ──────────────────────────────────────────────────

    def _fetch_funding_rates(self, records: List[SignalRecord], now: str) -> None:
        """Aggregated BTC funding rate (OI-weighted across exchanges)."""
        try:
            data = self._get("funding?symbol=BTC")
            if data is None:
                return

            total_oi = 0.0
            weighted_rate = 0.0
            for ex in data:
                oi = float(ex.get("openInterest", 0))
                rate = float(ex.get("rate", 0))
                total_oi += oi
                weighted_rate += rate * oi

            if total_oi > 0:
                avg_8h = weighted_rate / total_oi
                annualized = avg_8h * 3 * 365 * 100  # percent

                records.append(SignalRecord(
                    ticker="BTC",
                    signal_type="BTC_FUNDING_RATE_ANN",
                    value=max(0.0, min(1.0, (annualized + 50) / 100)),
                    raw_value=annualized,
                    source=self.name,
                    timestamp=now,
                    cost_tier='FREE',
                ))
                print(f"[{self.name}] BTC Funding Rate: {annualized:.1f}% annualized")

        except Exception as exc:
            logger.warning("[%s] Funding rate fetch failed: %s", self.name, exc)

    # ── Open Interest ──────────────────────────────────────────────────

    def _fetch_open_interest(self, records: List[SignalRecord], now: str) -> None:
        """Aggregated BTC open interest across all exchanges."""
        try:
            data = self._get("open_interest?symbol=BTC")
            if data is None:
                return

            total_oi = sum(float(d.get("openInterest", 0)) for d in data)

            records.append(SignalRecord(
                ticker="BTC",
                signal_type="BTC_OI_TOTAL",
                value=total_oi / 300e9,
                raw_value=total_oi,
                source=self.name,
                timestamp=now,
                cost_tier='FREE',
            ))
            print(f"[{self.name}] BTC Total OI: ${total_oi/1e9:.1f}B")

        except Exception as exc:
            logger.warning("[%s] OI fetch failed: %s", self.name, exc)

    # ── Liquidations ───────────────────────────────────────────────────

    def _fetch_liquidation_data(self, records: List[SignalRecord], now: str) -> None:
        """24h liquidation volumes and long/short split."""
        try:
            data = self._get("liquidation?symbol=BTC&time_type=h24")
            if data is None:
                return

            total_liq = 0.0
            long_liq = 0.0
            for d in data:
                long_val = float(d.get("longLiquidationUsd", 0))
                short_val = float(d.get("shortLiquidationUsd", 0))
                total_liq += long_val + short_val
                long_liq += long_val

            long_pct = (long_liq / total_liq * 100) if total_liq > 0 else 50.0

            records.append(SignalRecord(
                ticker="BTC",
                signal_type="BTC_24H_LIQUIDATIONS",
                value=total_liq / 1e9,
                raw_value=total_liq,
                source=self.name,
                timestamp=now,
                cost_tier='FREE',
            ))
            records.append(SignalRecord(
                ticker="BTC",
                signal_type="BTC_LONG_LIQ_PCT",
                value=long_pct / 100.0,
                raw_value=long_pct,
                source=self.name,
                timestamp=now,
                cost_tier='FREE',
            ))
            # RPE module consumes this as LIQ_VOL_24H
            records.append(SignalRecord(
                ticker="BTC",
                signal_type="LIQ_VOL_24H",
                value=total_liq / 1e9,
                raw_value=total_liq,
                source=self.name,
                timestamp=now,
                cost_tier='FREE',
            ))
            print(f"[{self.name}] BTC 24h Liquidations: ${total_liq/1e6:.0f}M ({long_pct:.0f}% longs)")

        except Exception as exc:
            logger.warning("[%s] Liquidation fetch failed: %s", self.name, exc)

    # ── Long/Short Ratio ───────────────────────────────────────────────

    def _fetch_long_short_ratio(self, records: List[SignalRecord], now: str) -> None:
        """Aggregated BTC long/short account ratio across exchanges."""
        try:
            data = self._get("long_short?symbol=BTC&time_type=h1")
            if data is None:
                return

            # CoinGlass returns per-exchange long/short — aggregate by OI weight
            total_weight = 0.0
            weighted_long = 0.0
            for ex in data:
                oi = float(ex.get("openInterest", 1))
                long_rate = float(ex.get("longRate", 0.5))
                total_weight += oi
                weighted_long += long_rate * oi

            if total_weight > 0:
                long_pct = (weighted_long / total_weight) * 100
            else:
                long_pct = 50.0

            records.append(SignalRecord(
                ticker="BTC",
                signal_type="BTC_LONG_SHORT_RATIO",
                value=long_pct / 100.0,
                raw_value=long_pct,
                source=self.name,
                timestamp=now,
                cost_tier='FREE',
            ))
            print(f"[{self.name}] BTC Long/Short: {long_pct:.1f}% longs (aggregated)")

        except Exception as exc:
            logger.warning("[%s] Long/short ratio fetch failed: %s", self.name, exc)

    # ── BTC Price ──────────────────────────────────────────────────────

    def _fetch_btc_price(self, records: List[SignalRecord], now: str) -> None:
        """BTC current price and 24h change from CoinGlass."""
        try:
            data = self._get("index/bitcoin-profitable-days")
            if data is None:
                return

            price = float(data.get("price", 0))
            change_24h = float(data.get("priceChange24h", 0))

            if price > 0:
                records.append(SignalRecord(
                    ticker="BTC",
                    signal_type="BTC_PRICE_24H_CHG_PCT",
                    value=max(-1.0, min(1.0, change_24h / 100.0)),
                    raw_value=change_24h,
                    source=self.name,
                    timestamp=now,
                    cost_tier='FREE',
                ))
                print(f"[{self.name}] BTC Price: ${price:,.0f} ({change_24h:+.1f}%)")

        except Exception as exc:
            logger.warning("[%s] BTC price fetch failed: %s", self.name, exc)

    # ── Main Fetch ─────────────────────────────────────────────────────

    def fetch(self) -> List[SignalRecord]:
        print(f"[{self.name}] Fetching crypto derivatives data...")
        records: List[SignalRecord] = []
        now = datetime.now(timezone.utc).isoformat()

        self._fetch_funding_rates(records, now)
        self._fetch_open_interest(records, now)
        self._fetch_liquidation_data(records, now)
        self._fetch_long_short_ratio(records, now)
        self._fetch_btc_price(records, now)

        print(f"[{self.name}] Complete. {len(records)} records.")
        return records
