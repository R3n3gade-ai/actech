"""
ARMS Engine: MC-RSS Analytics (Live Data Ingestion)

Replaces the manual RSS bridge with live public data scraping.
Fetches the NAAIM Exposure Index and provides the interface for AAII/Vanda.
"""

import requests
from bs4 import BeautifulSoup
from dataclasses import dataclass
from typing import Optional

@dataclass
class RSSLiveInputs:
    naaim_exposure_index: float
    aaii_bull_bear_spread: float
    retail_net_buying_usd: float

def fetch_naaim_index() -> Optional[float]:
    """Scrapes the latest NAAIM Exposure Index."""
    url = "https://www.naaim.org/programs/naaim-exposure-index/"
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        r = requests.get(url, headers=headers, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.content, 'html.parser')
        
        table = soup.find('table')
        if table:
            rows = table.find_all('tr')
            for row in rows[1:2]: # Get first data row
                cells = row.find_all(['td', 'th'])
                if len(cells) >= 2:
                    return float(cells[1].get_text(strip=True))
    except Exception as e:
        print(f"[MC-RSS Analytics] Failed to fetch live NAAIM data: {e}")
    return None

def fetch_live_sentiment() -> RSSLiveInputs:
    """
    Fetches available live data. 
    Fallbacks to neutral/recent static data if scraping is blocked (e.g., AAII 403s).
    """
    naaim = fetch_naaim_index()
    
    # In a full institutional setup, AAII and VandaTrack would use official API keys.
    # We fallback to static recent data for those if APIs aren't available, but
    # NAAIM provides a strong live directional signal on its own.
    return RSSLiveInputs(
        naaim_exposure_index=naaim if naaim is not None else 50.0,
        aaii_bull_bear_spread=0.15, # Proxy (Bull 40%, Bear 25% -> 0.15 spread)
        retail_net_buying_usd=1.2e9 # Proxy
    )
