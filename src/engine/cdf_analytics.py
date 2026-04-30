"""
ARMS Engine: CDF Analytics

Computes underperformance vs QQQ using live historical prices when available
via the yfinance API. This replaces dependence on manual bridge-fed CDF state.

"Silence is trust in the architecture."
"""

from dataclasses import dataclass
from typing import Optional
import yfinance as yf
from datetime import datetime, timedelta

@dataclass
class RelativePerformanceSnapshot:
    ticker: str
    days_back: int
    qqq_return: float
    position_return: float
    underperformance_pp: float

def compute_live_underperformance(ticker: str, days_back: int = 45) -> Optional[RelativePerformanceSnapshot]:
    """
    Fetches real historical data from Yahoo Finance and calculates the exact 
    relative underperformance vs QQQ over the specified timeframe.
    """
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back + 10) # Buffer for weekends/holidays
        
        # Download data
        data = yf.download(f"{ticker} QQQ", start=start_date.strftime("%Y-%m-%d"), end=end_date.strftime("%Y-%m-%d"), progress=False)
        if data.empty:
            print(f"[CDFAnalytics] Failed to fetch live history for {ticker}")
            return None
            
        prices = data['Close']
        if len(prices) < 2:
            return None
            
        # Find the row closest to 'days_back' trading days ago
        target_idx = max(0, len(prices) - days_back)
        
        pos_start = float(prices[ticker].iloc[target_idx])
        qqq_start = float(prices['QQQ'].iloc[target_idx])
        
        pos_end = float(prices[ticker].iloc[-1])
        qqq_end = float(prices['QQQ'].iloc[-1])
        
        pos_return = (pos_end / pos_start) - 1.0
        qqq_return = (qqq_end / qqq_start) - 1.0
        
        underperf = max(0.0, (qqq_return - pos_return) * 100.0)
        
        return RelativePerformanceSnapshot(
            ticker=ticker,
            days_back=days_back,
            qqq_return=qqq_return * 100.0,
            position_return=pos_return * 100.0,
            underperformance_pp=underperf
        )
    except Exception as e:
        print(f"[CDFAnalytics] Error calculating live performance for {ticker}: {e}")
        return None
