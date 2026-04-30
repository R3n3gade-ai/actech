# src/data_feeds/pipeline.py
# Production data ingestion pipeline. No mocks. No synthetic fallbacks.

import logging
from typing import List

from .interfaces import FeedPlugin, SignalRecord
from .fred_plugin import FredPlugin
from .crypto_plugin import CryptoPlugin
from .pmi_plugin import PmiPlugin
from .coinglass_feed import CoinglassPlugin

logger = logging.getLogger(__name__)


class DataPipeline:
    """
    Production data feed pipeline.
    
    Feed Architecture:
      FRED        — Macro: VIX, yields, HY spread, T10Y2Y, PCR, margin debt
      IBKR        — CME futures basis, OI, stablecoin pegs, CBOE SKEW
      CoinGlass   — Crypto derivatives: funding, OI, liquidations, long/short
      PMI (CSV)   — ISM Manufacturing PMI (manual monthly update)
    
    If a critical feed fails, the pipeline errors.
    CoinGlass failure is non-critical (crypto modules degrade gracefully).
    """
    
    def __init__(self):
        self.plugins: List[FeedPlugin] = [
            FredPlugin(),       # FRED API (requires FRED_API_KEY)
            CryptoPlugin(),     # IBKR live market data (requires IB Gateway)
            PmiPlugin(),        # ISM PMI from CSV bridge (data/pmi_temp_bridge.csv)
            CoinglassPlugin(),  # CoinGlass public API (free, no auth)
        ]
        print(f"[DataPipeline] Initialized with {len(self.plugins)} production feed(s).")

    def run_all_feeds(self) -> List[SignalRecord]:
        """
        Runs all feed plugins. FRED/IBKR/PMI are critical.
        CoinGlass is non-critical — logs warning on failure.
        """
        all_signals = []
        print(f"[DataPipeline] Running {len(self.plugins)} feed(s)...")
        for plugin in self.plugins:
            try:
                signals = plugin.fetch()
                all_signals.extend(signals)
                print(f"[DataPipeline]   - {plugin.name}: OK, {len(signals)} record(s).")
            except Exception as e:
                if plugin.name == "COINGLASS":
                    logger.warning("[DataPipeline] CoinGlass failed (non-critical): %s", e)
                    print(f"[DataPipeline]   - {plugin.name}: FAILED (non-critical): {e}")
                else:
                    print(f"[DataPipeline]   - {plugin.name}: FAILED: {e}")
                    raise
        
        print(f"[DataPipeline] Complete. Total records: {len(all_signals)}")
        return all_signals
