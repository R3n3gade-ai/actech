"""
ARMS Data Feeds: ISM Manufacturing PMI Plugin

Production rule: do not emit fabricated PMI values.

Development mode: reads from CSV bridge file (data/pmi_temp_bridge.csv)
updated monthly from the ISM Manufacturing PMI Report (ismworld.org).

Production upgrade path: swap to Trading Economics / FMP / S&P Global API
by changing only the _fetch_from_api() method. The SignalRecord contract
and downstream consumers remain unchanged.

CSV format expected:
  Date,Value,% Change vs Last Year
  2026-03-01,52.7,7.6
"""

import csv
import os
import logging
from datetime import datetime, timezone
from typing import List, Optional

from data_feeds.interfaces import FeedPlugin, SignalRecord

logger = logging.getLogger(__name__)

STRICT_LIVE_MODE = os.environ.get('ARMS_STRICT_LIVE', '1').strip().lower() not in {'0', 'false', 'no'}

# CSV bridge paths — checked in order
_CSV_PATHS = [
    os.environ.get('ARMS_PMI_CSV', ''),
    os.path.join('data', 'pmi_temp_bridge.csv'),
    os.path.join('achelion_arms', 'state', 'pmi_latest.csv'),
]


class PmiPlugin(FeedPlugin):
    @property
    def name(self) -> str:
        return "ISM_PMI"

    def _read_csv_bridge(self) -> Optional[dict]:
        """Read the most recent PMI value from the CSV bridge file."""
        for path in _CSV_PATHS:
            if not path or not os.path.exists(path):
                continue
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                if not rows:
                    continue
                # First data row is the most recent (descending order)
                row = rows[0]
                value = float(row.get('Value', row.get('value', '')))
                date_str = row.get('Date', row.get('date', ''))
                logger.info("[%s] Read PMI %.1f from %s (date: %s)", self.name, value, path, date_str)
                return {'value': value, 'date': date_str, 'path': path}
            except Exception as e:
                logger.warning("[%s] Failed to parse %s: %s", self.name, path, e)
                continue
        return None

    def fetch(self) -> List[SignalRecord]:
        print(f"[{self.name}] Fetching ISM Manufacturing PMI...")
        now = datetime.now(timezone.utc).isoformat()

        result = self._read_csv_bridge()
        if result is not None:
            raw_val = result['value']
            obs_date = result['date'] or now
            print(f"[{self.name}] ISM Manufacturing PMI: {raw_val} (from {result['path']}, date: {obs_date})")
            return [SignalRecord(
                ticker="MACRO",
                signal_type="PMI_NOWCAST",
                value=raw_val / 100.0,
                raw_value=raw_val,
                source=self.name,
                timestamp=obs_date,
                cost_tier='FREE',
            )]

        # No CSV bridge available
        if STRICT_LIVE_MODE:
            raise RuntimeError(
                f"[{self.name}] PMI CSV bridge not found at any path: {_CSV_PATHS}. "
                "Update data/pmi_temp_bridge.csv with the latest ISM Manufacturing PMI."
            )

        print(f"[{self.name}] WARNING: No PMI CSV found. Using last known baseline (strict mode disabled).")
        return [SignalRecord(
            ticker="MACRO",
            signal_type="PMI_NOWCAST",
            value=0.527,
            raw_value=52.7,
            source=f"{self.name}_FALLBACK",
            timestamp=now,
            cost_tier='FALLBACK',
        )]


if __name__ == '__main__':
    plugin = PmiPlugin()
    for r in plugin.fetch():
        print(f"{r.signal_type}: {r.raw_value} (Normalized: {r.value})")
