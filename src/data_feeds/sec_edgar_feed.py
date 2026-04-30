"""
ARMS Data Feeds: SEC EDGAR Form 4 feed

Truthful automated event ingestion path for insider-sale style CDM events.
Uses SEC submissions JSON endpoints for a configured watchlist of CIKs.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List
from urllib.request import Request, urlopen

from engine.cdm import NewsItem
from engine.bridge_paths import bridge_path

SEC_HEADERS = {
    "User-Agent": os.environ.get("ARMS_SEC_USER_AGENT", "Achelion ARMS contact@example.com"),
    "Accept-Encoding": "gzip, deflate",
    "Host": "data.sec.gov"
}


def _fetch_json(url: str):
    req = Request(url, headers=SEC_HEADERS)
    with urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _load_watchlist() -> Dict[str, Dict[str, str]]:
    path = bridge_path('ARMS_SEC_WATCHLIST_JSON', 'sec_watchlist.json')
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        payload = json.load(f)
    return payload if isinstance(payload, dict) else {}


def fetch_form4_events(limit_per_company: int = 5) -> List[NewsItem]:
    watchlist = _load_watchlist()
    if not watchlist:
        print('[SEC_EDGAR] No watchlist configured; skipping SEC feed.')
        return []

    events: List[NewsItem] = []
    for ticker, meta in watchlist.items():
        cik = str(meta.get('cik', '')).strip()
        company_name = str(meta.get('name', ticker)).strip()
        if not cik:
            continue
        cik_padded = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        try:
            payload = _fetch_json(url)
            recent = payload.get('filings', {}).get('recent', {})
            forms = recent.get('form', [])
            dates = recent.get('filingDate', [])
            accessions = recent.get('accessionNumber', [])
            for idx, form in enumerate(forms[:limit_per_company]):
                if str(form).strip() != '4':
                    continue
                filing_date = dates[idx] if idx < len(dates) else datetime.now(timezone.utc).date().isoformat()
                accession = accessions[idx] if idx < len(accessions) else ''
                headline = f"{company_name} Form 4 filed"
                content = f"SEC EDGAR Form 4 detected for {company_name}. Accession: {accession}"
                events.append(NewsItem(
                    source='SEC_EDGAR',
                    headline=headline,
                    content=content,
                    timestamp=f"{filing_date}T00:00:00Z",
                    entities=[company_name, ticker],
                    event_type='INSIDER_SALE'
                ))
        except Exception as e:
            print(f"[SEC_EDGAR] Failed for {ticker}/{company_name}: {e}")

    print(f"[SEC_EDGAR] Returning {len(events)} Form 4 event(s).")
    return events
