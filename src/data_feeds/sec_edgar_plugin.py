import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Optional
import datetime

# --- Internal Imports ---
from data_feeds.interfaces import FeedPlugin, SignalRecord

class SecEdgarPlugin(FeedPlugin):
    """
    ARMS Frontier F1: SEC EDGAR Data Ingestion.
    Fetches real-time filings and textual context for AI analysis.
    """
    
    def __init__(self):
        self._name = "SEC_EDGAR"
        self.headers = {'User-Agent': 'Achelion Capital Management (admin@achelion.com)'}
        self.ticker_to_cik = self._build_cik_map()

    @property
    def name(self) -> str:
        return self._name

    def _build_cik_map(self) -> Dict[str, str]:
        """Fetches the official SEC mapping of Ticker to CIK."""
        try:
            r = requests.get('https://www.sec.gov/files/company_tickers.json', headers=self.headers, timeout=10)
            r.raise_for_status()
            data = r.json()
            # The SEC JSON is an object with numerical keys containing {'cik_str': 123, 'ticker': 'AAPL'}
            return {v['ticker']: str(v['cik_str']).zfill(10) for k, v in data.items()}
        except Exception as e:
            print(f"[{self.name}] Error building CIK map: {e}")
            return {}

    def fetch(self) -> List[SignalRecord]:
        """
        Implementation of the FeedPlugin interface.
        For SEC EDGAR, 'fetch' might return signals like 'NEW_8K_FILED'.
        This would be used by a future event-driven architecture.
        """
        # Placeholder for event-driven signal generation
        return []

    def fetch_docs(self, ticker: str, max_filings: int = 3, max_chars_per_doc: int = 15000) -> str:
        """
        Fetches the text of the most recent significant filings (8-K, 10-Q, 10-K).
        Used by the Systematic Scan Engine for SENTINEL Gate analysis.
        """
        cik = self.ticker_to_cik.get(ticker.upper())
        if not cik:
            return f"Error: Could not resolve CIK for ticker {ticker}."

        print(f"[{self.name}] Fetching EDGAR submissions for {ticker} (CIK {cik})...")
        try:
            r = requests.get(f'https://data.sec.gov/submissions/CIK{cik}.json', headers=self.headers, timeout=10)
            r.raise_for_status()
            filings = r.json().get('filings', {}).get('recent', {})
        except Exception as e:
            return f"Error fetching submissions for {ticker}: {e}"

        docs_text = []
        docs_text.append(f"--- RECENT SEC FILINGS FOR {ticker} ---")
        
        count = 0
        for i, form in enumerate(filings.get('form', [])):
            if form in ['8-K', '10-Q', '10-K', '8-K/A']:
                acc_num_raw = filings['accessionNumber'][i]
                acc_num = acc_num_raw.replace('-', '')
                doc = filings['primaryDocument'][i]
                date = filings['filingDate'][i]
                description = filings['primaryDocDescription'][i]
                
                url = f"https://www.sec.gov/Archives/edgar/data/{str(int(cik))}/{acc_num}/{doc}"
                
                try:
                    doc_r = requests.get(url, headers=self.headers, timeout=10)
                    doc_r.raise_for_status()
                    
                    if url.endswith('.xml') or url.endswith('.htm') or url.endswith('.html'):
                        soup = BeautifulSoup(doc_r.content, 'html.parser')
                        text = soup.get_text(separator=' ', strip=True)
                    else:
                        text = doc_r.text
                        
                    # Truncate to prevent blowing up the LLM context limit
                    if len(text) > max_chars_per_doc:
                        text = text[:max_chars_per_doc] + f"... [TRUNCATED AT {max_chars_per_doc} CHARS]"
                        
                    docs_text.append(f"\n[{date}] FORM {form} - {description}")
                    docs_text.append(f"URL: {url}")
                    docs_text.append("CONTENT PREVIEW:")
                    docs_text.append(text)
                    
                    count += 1
                except Exception as e:
                    print(f"[{self.name}] Failed to fetch document {url}: {e}")
                    
                if count >= max_filings:
                    break
                    
        if not docs_text[1:]:
            return f"No recent 8-K, 10-Q, or 10-K filings found for {ticker}."
            
        return "\n".join(docs_text)

# Default instance for the system
sec_edgar_plugin = SecEdgarPlugin()
