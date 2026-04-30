"""
ARMS Data Feeds: Public RSS news feed bridge

Automated, lightweight event ingestion for CDM/TDC using reachable public RSS
sources. This is an interim real feed path, not a mock layer.
"""

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List
from urllib.request import Request, urlopen

from engine.cdm import NewsItem
from config.position_dependency_map import POSITION_DEPENDENCIES

USER_AGENT = os.environ.get("ARMS_SEC_USER_AGENT", "Achelion ARMS contact@example.com")


def _fetch_xml(url: str) -> str:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=20) as response:
        return response.read().decode("utf-8", errors="ignore")


def _candidate_entities() -> List[str]:
    entities = set()
    for ticker, deps in POSITION_DEPENDENCIES.items():
        entities.add(ticker)
        for k in ('primary_demand_drivers', 'thesis_enablers', 'regulatory_counterparties'):
            for dep in deps.get(k, []):
                entities.add(dep)
    return sorted(entities, key=len, reverse=True)


def _extract_entities(text: str) -> List[str]:
    hay = text.lower()
    matched = []
    for entity in _candidate_entities():
        if entity.lower() in hay:
            matched.append(entity)
    return list(dict.fromkeys(matched))


def _classify_event(text: str) -> str:
    t = text.lower()
    if any(x in t for x in ['antitrust', 'lawsuit', 'court', 'judgment', 'doj', 'remedy']):
        return 'LEGAL_RULING'
    if any(x in t for x in ['sec', 'export control', 'rulemaking', 'investigation', 'regulator', 'filing']):
        return 'REGULATORY_FILING'
    if any(x in t for x in ['guidance cut', 'warning', 'cuts forecast', 'slows spending', 'capex cut']):
        return 'EARNINGS_WARNING'
    if any(x in t for x in ['ceo resigns', 'cfo resigns', 'steps down', 'leadership change']):
        return 'LEADERSHIP_CHANGE'
    if any(x in t for x in ['acquire', 'acquisition', 'merger', 'divestiture']):
        return 'MA_ANNOUNCEMENT'
    if any(x in t for x in ['expand', 'partnership', 'deal', 'capacity', 'compute deal']):
        return 'POSITIVE_DEVELOPMENT'
    return 'UNKNOWN'


def _parse_rss(xml_text: str, source_name: str) -> List[NewsItem]:
    items: List[NewsItem] = []
    root = ET.fromstring(xml_text)
    for item in root.findall('.//item')[:25]:
        title = (item.findtext('title') or '').strip()
        desc = (item.findtext('description') or '').strip()
        pub = (item.findtext('pubDate') or datetime.now(timezone.utc).isoformat()).strip()
        body = f"{title} {desc}".strip()
        entities = _extract_entities(body)
        if not entities:
            continue
        event_type = _classify_event(body)
        if event_type == 'UNKNOWN':
            continue
        items.append(NewsItem(
            source=source_name,
            headline=title,
            content=desc,
            timestamp=pub,
            entities=entities,
            event_type=event_type  # type: ignore[arg-type]
        ))
    return items


def fetch_public_rss_events() -> List[NewsItem]:
    # Build Yahoo RSS ticker list dynamically from position dependency map
    portfolio_tickers = ','.join(sorted(POSITION_DEPENDENCIES.keys()))
    urls = [
        ('SEC_RSS', 'https://www.sec.gov/news/pressreleases.rss'),
        ('YAHOO_FINANCE_RSS', f'https://feeds.finance.yahoo.com/rss/2.0/headline?s={portfolio_tickers}&region=US&lang=en-US')
    ]

    all_items: List[NewsItem] = []
    for source_name, url in urls:
        try:
            xml_text = _fetch_xml(url)
            all_items.extend(_parse_rss(xml_text, source_name))
        except Exception as e:
            print(f"[NewsRSS] Failed {source_name}: {e}")
    print(f"[NewsRSS] Returning {len(all_items)} event(s).")
    return all_items
