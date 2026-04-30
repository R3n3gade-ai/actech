"""
ARMS Data Feeds: Crypto Plugin (IBKR + Coinbase implementation)

Fetches crypto microstructure signals:
  - BTC/ETH futures basis (funding rate proxy) via IBKR CME futures
  - BTC/ETH open interest via IBKR CME contract details
  - Stablecoin pegs via Coinbase public spot API

Binance endpoints are geo-blocked in the US (HTTP 451). All futures
data is sourced from CME via the existing IBKR connection at zero
additional cost.
"""

import datetime
import json
import logging
import math
import os
from typing import List, Optional

from urllib.request import Request, urlopen

from data_feeds.interfaces import FeedPlugin, SignalRecord

try:
    import ib_insync as ibi
    IB_AVAILABLE = True
except ImportError:
    IB_AVAILABLE = False

logger = logging.getLogger(__name__)


class CryptoPlugin(FeedPlugin):
    """
    Crypto microstructure feed using IBKR for CME futures data and
    Coinbase for stablecoin peg monitoring.
    """

    def __init__(self):
        self._ib: Optional['ibi.IB'] = None
        self._connected = False

    @property
    def name(self) -> str:
        return "CRYPTO_SENSES"

    # ------------------------------------------------------------------ #
    #  IBKR Connection (reuses same gateway as broker_api)
    # ------------------------------------------------------------------ #

    def _ensure_ib_connection(self) -> bool:
        """Connect to IB Gateway if not already connected."""
        if not IB_AVAILABLE:
            logger.warning("[%s] ib_insync not installed; IBKR crypto feeds unavailable.", self.name)
            return False

        if self._ib is not None and self._ib.isConnected():
            return True

        host = os.environ.get('IB_HOST') or os.environ.get('TWS_HOST') or '127.0.0.1'
        port = int(os.environ.get('IB_PORT') or os.environ.get('TWS_PORT') or 4002)
        # Use a distinct client ID to avoid collision with the main broker adapter
        client_id = int(os.environ.get('IB_CRYPTO_CLIENT_ID', '10'))

        try:
            self._ib = ibi.IB()
            self._ib.connect(host, port, clientId=client_id, timeout=10)
            self._connected = self._ib.isConnected()
            if self._connected:
                print(f"[{self.name}] Connected to IBKR at {host}:{port} (clientId={client_id})")
            return self._connected
        except Exception as exc:
            logger.error("[%s] IBKR connection failed: %s", self.name, exc)
            self._ib = None
            self._connected = False
            return False

    # ------------------------------------------------------------------ #
    #  IBKR Futures Basis (funding rate proxy)
    # ------------------------------------------------------------------ #

    def _fetch_futures_basis(self, records: List[SignalRecord], now: str) -> None:
        """
        Derives a funding-rate proxy from CME futures basis:
          basis = (futures_price - spot_price) / spot_price

        Positive basis = contango = bullish sentiment (like positive perpetual funding).
        Negative basis = backwardation = bearish / stress.

        Normalization maps basis from [-0.05, +0.05] to [0, 1] with 0.5 = flat.
        """
        if not self._connected or self._ib is None:
            return

        # CME BTC futures (BRR) and ETH futures (ETH)
        specs = [
            {"underlying": "BRR", "signal_type": "BTC_FUNDING", "ticker": "BTC",
             "spot_symbol": "BTC", "exchange": "CME", "multiplier": "5"},
            {"underlying": "ETH", "signal_type": "ETH_FUNDING", "ticker": "ETH",
             "exchange": "CME", "multiplier": "50"},
        ]

        for spec in specs:
            try:
                # 1. Get front-month futures contract
                fut = ibi.Future(
                    symbol=spec["underlying"],
                    exchange=spec["exchange"],
                    currency="USD",
                )
                details = self._ib.reqContractDetails(fut)
                if not details:
                    print(f"[{self.name}] No CME futures found for {spec['underlying']}")
                    continue

                # Pick the nearest expiry
                details.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)
                front = details[0].contract
                self._ib.qualifyContracts(front)

                # 2. Get futures price
                fut_ticker = self._ib.reqTickers(front)
                if not fut_ticker:
                    continue
                fut_price = fut_ticker[0].marketPrice()
                if fut_price is None or math.isnan(fut_price) or fut_price <= 0:
                    fut_price = fut_ticker[0].close
                if fut_price is None or math.isnan(fut_price) or fut_price <= 0:
                    print(f"[{self.name}] No valid futures price for {spec['underlying']}")
                    continue

                # 3. Get spot price via crypto contract (IBKR provides PAXOS spot)
                spot_contract = ibi.Crypto(spec["spot_symbol"], "PAXOS", "USD") if spec["underlying"] == "BRR" else None
                spot_price = None
                if spot_contract is not None:
                    try:
                        self._ib.qualifyContracts(spot_contract)
                        spot_tickers = self._ib.reqTickers(spot_contract)
                        if spot_tickers:
                            sp = spot_tickers[0].marketPrice()
                            if sp is not None and not math.isnan(sp) and sp > 0:
                                spot_price = sp
                    except Exception:
                        pass

                # Fallback: use Coinbase spot for the price
                if spot_price is None:
                    try:
                        coinbase_symbol = "BTC-USD" if spec["underlying"] == "BRR" else "ETH-USD"
                        cb_data = self._fetch_coinbase_ticker(coinbase_symbol)
                        if cb_data:
                            spot_price = float(cb_data["price"])
                    except Exception:
                        pass

                if spot_price is None or spot_price <= 0:
                    print(f"[{self.name}] No spot price for {spec['underlying']}; skipping basis calc.")
                    continue

                # 4. Calculate annualized basis
                basis = (float(fut_price) - spot_price) / spot_price

                # Normalize: map [-0.05, +0.05] → [0, 1], with 0.5 = flat
                normalized = max(0.0, min(1.0, (basis + 0.05) / 0.10))

                records.append(SignalRecord(
                    ticker=spec["ticker"],
                    signal_type=spec["signal_type"],
                    value=normalized,
                    raw_value=basis,
                    source=self.name,
                    timestamp=now,
                    cost_tier='FREE'
                ))
                print(f"[{self.name}] {spec['signal_type']}: basis={basis:.6f} "
                      f"(fut={fut_price:.2f}, spot={spot_price:.2f})")

            except Exception as exc:
                print(f"[{self.name}] Futures basis failed for {spec['underlying']}: {exc}")

    # ------------------------------------------------------------------ #
    #  IBKR Open Interest via CME futures
    # ------------------------------------------------------------------ #

    def _fetch_open_interest(self, records: List[SignalRecord], now: str) -> None:
        """
        Fetches open interest from CME BTC/ETH futures via IBKR contract details.
        Uses recent bar volume as a stress proxy when OI changes aren't available
        in real-time (IBKR provides OI on daily bars).
        """
        if not self._connected or self._ib is None:
            return

        specs = [
            {"underlying": "BRR", "signal_type": "BTC_OI", "ticker": "BTC", "exchange": "CME"},
            {"underlying": "ETH", "signal_type": "ETH_OI", "ticker": "ETH", "exchange": "CME"},
        ]

        for spec in specs:
            try:
                fut = ibi.Future(
                    symbol=spec["underlying"],
                    exchange=spec["exchange"],
                    currency="USD",
                )
                details = self._ib.reqContractDetails(fut)
                if not details:
                    continue

                details.sort(key=lambda d: d.contract.lastTradeDateOrContractMonth)
                front = details[0].contract
                self._ib.qualifyContracts(front)

                # Request daily bars to get volume + OI
                bars = self._ib.reqHistoricalData(
                    front,
                    endDateTime='',
                    durationStr='5 D',
                    barSizeSetting='1 day',
                    whatToShow='TRADES',
                    useRTH=False,
                    formatDate=1,
                )

                if not bars or len(bars) < 2:
                    print(f"[{self.name}] Insufficient bar data for {spec['underlying']} OI")
                    continue

                # Latest bar volume vs prior bar volume as activity proxy
                latest_vol = float(bars[-1].volume)
                prior_vol = float(bars[-2].volume)
                latest_close = float(bars[-1].close)
                prior_close = float(bars[-2].close)

                # Stress proxy: rising volume + falling price = bearish positioning
                price_change = (latest_close - prior_close) / prior_close if prior_close > 0 else 0.0
                vol_change = (latest_vol - prior_vol) / prior_vol if prior_vol > 0 else 0.0

                # Rising volume + falling price → stress; scale so 5% drop = 0.25
                oi_stress = max(0.0, -price_change) * 5.0
                if vol_change > 0.5:  # Significant volume surge amplifies stress
                    oi_stress *= 1.25
                normalized = max(0.0, min(1.0, oi_stress))

                records.append(SignalRecord(
                    ticker=spec["ticker"],
                    signal_type=spec["signal_type"],
                    value=normalized,
                    raw_value=latest_vol,
                    source=self.name,
                    timestamp=now,
                    cost_tier='FREE'
                ))
                print(f"[{self.name}] {spec['signal_type']}: vol={latest_vol:.0f}, "
                      f"price_chg={price_change:.4f}, stress={normalized:.3f}")

            except Exception as exc:
                print(f"[{self.name}] OI fetch failed for {spec['underlying']}: {exc}")

    # ------------------------------------------------------------------ #
    #  Coinbase stablecoin pegs (unchanged — works from US)
    # ------------------------------------------------------------------ #

    def _fetch_coinbase_ticker(self, product_id: str) -> Optional[dict]:
        """Fetch a single Coinbase product ticker."""
        req = Request(
            f"https://api.exchange.coinbase.com/products/{product_id}/ticker",
            headers={"User-Agent": "Achelion-ARMS/1.0"},
        )
        with urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def _fetch_stablecoin_pegs(self, records: List[SignalRecord], now: str) -> None:
        """Fetch USDT, USDC, and DAI peg data from Coinbase."""
        peg_specs = [
            ("USDT-USD", "USDT_PEG"),
            ("USDC-USD", "USDC_PEG"),
            ("DAI-USD", "DAI_PEG"),
        ]
        for product_id, signal_type in peg_specs:
            try:
                payload = self._fetch_coinbase_ticker(product_id)
                if payload is None:
                    continue
                raw_value = float(payload["price"])
                # Normalize: $0.95 → 0.0, $1.05 → 1.0, $1.00 → 0.5
                normalized = max(0.0, min(1.0, (raw_value - 0.95) / 0.10))
                records.append(SignalRecord(
                    ticker="GEOPOL",
                    signal_type=signal_type,
                    value=normalized,
                    raw_value=raw_value,
                    source=self.name,
                    timestamp=payload.get("time", now),
                    cost_tier='FREE'
                ))
            except Exception as exc:
                print(f"[{self.name}] Stablecoin peg fetch failed for {product_id}: {exc}")

    # ------------------------------------------------------------------ #
    #  CBOE SKEW Index via IBKR market data
    # ------------------------------------------------------------------ #

    def _fetch_cboe_skew(self, records: List[SignalRecord], now: str) -> None:
        """Fetch CBOE SKEW index from IBKR (it's a CBOE-listed index)."""
        if not self._connected or self._ib is None:
            return

        try:
            skew_contract = ibi.Index("SKEW", "CBOE", "USD")
            self._ib.qualifyContracts(skew_contract)
            tickers = self._ib.reqTickers(skew_contract)
            if not tickers:
                print(f"[{self.name}] No SKEW data from IBKR")
                return

            skew_val = tickers[0].marketPrice()
            if skew_val is None or math.isnan(skew_val) or skew_val <= 0:
                skew_val = tickers[0].close
            if skew_val is None or math.isnan(skew_val) or skew_val <= 0:
                print(f"[{self.name}] No valid SKEW price from IBKR")
                return

            records.append(SignalRecord(
                ticker="MACRO",
                signal_type="CBOE_SKEW",
                value=skew_val / 200.0,  # 100-200 range → 0.5-1.0
                raw_value=skew_val,
                source=self.name,
                timestamp=now,
                cost_tier='FREE',
            ))
            print(f"[{self.name}] CBOE SKEW: {skew_val:.1f}")

        except Exception as exc:
            print(f"[{self.name}] SKEW fetch failed: {exc}")

    # ------------------------------------------------------------------ #
    #  Main feed entry point
    # ------------------------------------------------------------------ #

    def fetch(self) -> List[SignalRecord]:
        print(f"[{self.name}] Fetching live market data via IBKR + Coinbase...")
        records: List[SignalRecord] = []
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # 1. Connect to IBKR
        ib_ok = self._ensure_ib_connection()
        if not ib_ok:
            print(f"[{self.name}] WARNING: IBKR unavailable — futures + SKEW signals will be missing.")

        # 2. CME futures basis (funding rate proxy)
        self._fetch_futures_basis(records, now)

        # 3. CME open interest / volume stress
        self._fetch_open_interest(records, now)

        # 4. CBOE SKEW index
        self._fetch_cboe_skew(records, now)

        # 5. Stablecoin pegs from Coinbase
        self._fetch_stablecoin_pegs(records, now)

        # 6. Disconnect IBKR
        if self._ib is not None and self._ib.isConnected():
            try:
                self._ib.disconnect()
            except Exception:
                pass
            self._ib = None
            self._connected = False

        print(f"[{self.name}] Fetch complete. Returning {len(records)} records.")
        return records


if __name__ == '__main__':
    plugin = CryptoPlugin()
    for r in plugin.fetch():
        print(f"{r.signal_type}: {r.raw_value} (Normalized: {r.value})")
