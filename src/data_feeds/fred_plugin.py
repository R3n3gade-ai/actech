# src/data_feeds/fred_plugin.py
# A concrete implementation of a FeedPlugin for the FRED data source.

import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict
from urllib.parse import urlencode
from urllib.request import urlopen

logger = logging.getLogger(__name__)

from .interfaces import FeedPlugin, SignalRecord

# A list of specific economic series we want to track from FRED.
# This can be expanded or moved to configuration later.
FRED_SERIES_IDS: Dict[str, str] = {
    "FED_FUNDS_RATE": "FEDFUNDS",
    "10Y_TREASURY_YIELD": "DGS10",
    "VIX_INDEX": "VIXCLS",
    "HY_CREDIT_SPREAD": "BAMLH0A0HYM2",
    "T10Y2Y": "T10Y2Y",            # Yield curve spread (RPE velocity signal)
    "EQUITY_PCR": "EQUITYPC",       # CBOE equity put/call ratio (PCR regime module)
}

# Separate: monthly series needing multi-observation fetch for rate-of-change
FRED_MARGIN_SERIES = "BOGZ1FL663067003Q"  # Margin debt (quarterly, billions)

class FredPlugin(FeedPlugin):
    """
    A FeedPlugin to fetch key economic indicators from the
    Federal Reserve Economic Data (FRED) API.
    """

    BASE_URL = "https://api.stlouisfed.org/fred/series/observations"

    @property
    def name(self) -> str:
        return "FRED"

    def _get_api_key(self) -> str:
        api_key = os.environ.get("FRED_API_KEY")
        if not api_key:
            raise RuntimeError("FRED_API_KEY is not set in the environment.")
        return api_key

    def _fetch_latest_observation(self, series_id: str) -> dict:
        params = {
            "series_id": series_id,
            "api_key": self._get_api_key(),
            "file_type": "json",
            "sort_order": "desc",
            "limit": 10,
        }
        url = f"{self.BASE_URL}?{urlencode(params)}"

        with urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        observations = payload.get("observations", [])
        for obs in observations:
            value = obs.get("value")
            if value not in (None, "."):
                return obs

        raise RuntimeError(f"No usable observation returned for FRED series {series_id}.")

    def _normalize_value(self, signal_type: str, value: float) -> float:
        if signal_type in {"FED_FUNDS_RATE", "10Y_TREASURY_YIELD", "HY_CREDIT_SPREAD"}:
            return value / 100.0
        if signal_type == "VIX_INDEX":
            return value / 100.0
        if signal_type == "PMI_NOWCAST":
            return value / 100.0
        if signal_type == "T10Y2Y":
            return value / 100.0   # e.g. -0.5 → -0.005
        if signal_type == "EQUITY_PCR":
            return value  # Already 0-1 range (e.g. 0.65)
        return value

    def fetch(self) -> List[SignalRecord]:
        """
        Fetch the latest available data for configured FRED series.
        """
        print(f"[{self.name} Plugin] Fetching live data from FRED...")

        records: List[SignalRecord] = []
        fetched_at = datetime.now(timezone.utc).isoformat()

        try:
            api_key = self._get_api_key()
        except RuntimeError as e:
            print(f"[{self.name} Plugin] FAILED with error: {e}")
            raise e

        for signal_type, series_id in FRED_SERIES_IDS.items():
            try:
                obs = self._fetch_latest_observation(series_id)
                raw_value = float(obs["value"])
                normalized_value = self._normalize_value(signal_type, raw_value)
                observation_date = obs.get("date", fetched_at)

                records.append(SignalRecord(
                    ticker="MACRO",
                    signal_type=signal_type,
                    value=normalized_value,
                    raw_value=raw_value,
                    source=self.name,
                    timestamp=observation_date,
                    cost_tier='FREE'
                ))
            except Exception as e:
                print(f"[{self.name} Plugin] Failed to fetch {series_id}: {e}. No fallback — production mode.")
                raise RuntimeError(f"FRED series unavailable: {series_id}") from e
        # ── Margin Debt (structural signal for Addendum 7) ──
        # FRED margin debt is quarterly with ~2 month lag. We fetch the last 4
        # observations and compute 3-month growth rate and MoM change.
        try:
            margin_records = self._fetch_margin_debt_growth(fetched_at)
            records.extend(margin_records)
        except Exception as e:
            logger.warning("[%s] Margin debt fetch failed (non-critical): %s", self.name, e)

        print(f"[{self.name} Plugin] Live fetch complete. Returning {len(records)} records.")
        return records

    def _fetch_margin_debt_growth(self, fetched_at: str) -> List[SignalRecord]:
        """
        Fetch last 4 quarterly margin debt observations from FRED and compute
        3-quarter growth rate and quarter-over-quarter change for Addendum 7
        Signal Layer 4 (Margin Debt Structural Fragility).
        """
        params = {
            "series_id": FRED_MARGIN_SERIES,
            "api_key": self._get_api_key(),
            "file_type": "json",
            "sort_order": "desc",
            "limit": 8,
        }
        url = f"{self.BASE_URL}?{urlencode(params)}"

        with urlopen(url, timeout=15) as response:
            payload = json.loads(response.read().decode("utf-8"))

        observations = payload.get("observations", [])
        values = []
        for obs in observations:
            v = obs.get("value")
            if v not in (None, "."):
                values.append(float(v))
            if len(values) >= 4:
                break

        records = []
        if len(values) >= 2:
            # Quarter-over-quarter change
            mom_change = ((values[0] - values[1]) / values[1]) * 100 if values[1] != 0 else 0
            records.append(SignalRecord(
                ticker="MACRO",
                signal_type="MARGIN_DEBT_MOM_CHANGE",
                value=mom_change / 100.0,
                raw_value=mom_change,
                source=self.name,
                timestamp=fetched_at,
                cost_tier='FREE'
            ))

        if len(values) >= 4:
            # 3-quarter growth rate
            growth_3q = ((values[0] - values[3]) / values[3]) * 100 if values[3] != 0 else 0
            records.append(SignalRecord(
                ticker="MACRO",
                signal_type="MARGIN_DEBT_3M_GROWTH",
                value=growth_3q / 100.0,
                raw_value=growth_3q,
                source=self.name,
                timestamp=fetched_at,
                cost_tier='FREE'
            ))

        return records

