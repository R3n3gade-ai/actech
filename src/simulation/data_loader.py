"""
Historical Data Loader ("Time Machine")
========================================
Fetches and caches historical daily market data for backtesting.

Sources:
  - yfinance: Equity prices, VIX, Treasury yields, crypto, ETFs
  - HY Spread proxy: (1/HYG - 1/LQD) scaled to basis points
  - PMI: Monthly ISM Manufacturing PMI (forward-filled daily)

All data is cached to disk after first fetch to avoid repeated API calls.
"""

import os
import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "backtest_cache"

# Default Architecture AB equity tickers
DEFAULT_EQUITY_TICKERS = [
    "TSLA", "NVDA", "AMD", "PLTR", "ALAB", "MU",
    "ANET", "AVGO", "MRVL", "ARM", "ETN", "VRT",
]

DEFENSIVE_TICKERS = ["SGOV", "SGOL", "DBMF", "STRC", "TLT"]
BENCHMARK_TICKERS = ["SPY", "QQQ"]

# Crypto: IBIT didn't exist before Jan 2024, use BTC-USD as proxy
CRYPTO_PROXY = "BTC-USD"
CRYPTO_ETF = "IBIT"

# Macro signal tickers on yfinance
MACRO_TICKERS = {
    "VIX": "^VIX",
    "TNX": "^TNX",       # 10-Year Treasury Yield (×10 on yfinance)
    "HYG": "HYG",        # High-yield corporate bond ETF
    "LQD": "LQD",        # Investment-grade corporate bond ETF
}

# ISM Manufacturing PMI monthly data (2019-2023)
# Source: Institute for Supply Management historical releases
ISM_PMI_MONTHLY = {
    "2019-01": 56.6, "2019-02": 54.2, "2019-03": 55.3, "2019-04": 52.8,
    "2019-05": 52.1, "2019-06": 51.7, "2019-07": 51.2, "2019-08": 49.1,
    "2019-09": 47.8, "2019-10": 48.3, "2019-11": 48.1, "2019-12": 47.2,
    "2020-01": 50.9, "2020-02": 50.1, "2020-03": 49.1, "2020-04": 41.5,
    "2020-05": 43.1, "2020-06": 52.6, "2020-07": 54.2, "2020-08": 56.0,
    "2020-09": 55.4, "2020-10": 59.3, "2020-11": 57.5, "2020-12": 60.7,
    "2021-01": 58.7, "2021-02": 60.8, "2021-03": 64.7, "2021-04": 60.7,
    "2021-05": 61.2, "2021-06": 60.6, "2021-07": 59.5, "2021-08": 59.9,
    "2021-09": 61.1, "2021-10": 60.8, "2021-11": 61.1, "2021-12": 58.7,
    "2022-01": 57.6, "2022-02": 58.6, "2022-03": 57.1, "2022-04": 55.4,
    "2022-05": 56.1, "2022-06": 53.0, "2022-07": 52.8, "2022-08": 52.8,
    "2022-09": 50.9, "2022-10": 50.2, "2022-11": 49.0, "2022-12": 48.4,
    "2023-01": 47.4, "2023-02": 47.7, "2023-03": 46.3, "2023-04": 47.1,
    "2023-05": 46.9, "2023-06": 46.0, "2023-07": 46.4, "2023-08": 47.6,
    "2023-09": 49.0, "2023-10": 46.7, "2023-11": 46.7, "2023-12": 47.4,
    "2024-01": 49.1, "2024-02": 47.8, "2024-03": 50.3, "2024-04": 49.2,
    "2024-05": 48.7, "2024-06": 48.5, "2024-07": 46.8, "2024-08": 47.2,
    "2024-09": 47.2, "2024-10": 46.5, "2024-11": 48.4, "2024-12": 49.3,
    "2025-01": 50.9, "2025-02": 50.3, "2025-03": 49.0,
}


@dataclass
class HistoricalDataBundle:
    """Complete historical data package for backtesting."""
    start_date: str
    end_date: str
    # Price DataFrames (index=Date, columns=tickers)
    equity_prices: pd.DataFrame       # Adjusted close for portfolio equities
    defensive_prices: pd.DataFrame    # SGOV, SGOL, DBMF
    benchmark_prices: pd.DataFrame    # SPY, QQQ
    crypto_prices: pd.DataFrame       # BTC-USD (or IBIT)
    # Macro signals (index=Date)
    macro_signals: pd.DataFrame       # VIX, TNX_yield, HY_spread_bps, PMI
    # Metadata
    trading_days: pd.DatetimeIndex = field(default_factory=lambda: pd.DatetimeIndex([]))
    tickers_loaded: List[str] = field(default_factory=list)


def _cache_key(tickers: List[str], start: str, end: str) -> str:
    """Generate a deterministic cache filename."""
    sig = hashlib.md5(
        f"{sorted(tickers)}_{start}_{end}".encode()
    ).hexdigest()[:12]
    return f"backtest_data_{start}_{end}_{sig}.parquet"


def _ensure_cache_dir():
    """Create cache directory if it doesn't exist."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _fetch_yfinance(tickers: List[str], start: str, end: str) -> pd.DataFrame:
    """Fetch adjusted close prices from yfinance with caching."""
    import yfinance as yf

    cache_file = CACHE_DIR / _cache_key(tickers, start, end)
    if cache_file.exists():
        logger.info(f"Loading cached data: {cache_file.name}")
        return pd.read_parquet(cache_file)

    logger.info(f"Fetching {len(tickers)} tickers from yfinance ({start} → {end})...")
    df = yf.download(
        tickers,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
    )

    # yfinance returns MultiIndex columns for multiple tickers
    if isinstance(df.columns, pd.MultiIndex):
        df = df["Close"]
    elif len(tickers) == 1:
        df = df[["Close"]].rename(columns={"Close": tickers[0]})

    # Cache to disk
    _ensure_cache_dir()
    df.to_parquet(cache_file)
    logger.info(f"Cached {len(df)} rows → {cache_file.name}")
    return df


def _build_macro_signals(start: str, end: str) -> pd.DataFrame:
    """
    Build daily macro signal DataFrame:
      - VIX: direct from ^VIX
      - TNX_yield: 10Y Treasury yield (^TNX / 10 to get %)
      - HY_spread_bps: proxy from HYG/LQD price ratio
      - PMI: ISM Manufacturing PMI (monthly, forward-filled)
    """
    raw = _fetch_yfinance(
        list(MACRO_TICKERS.values()),
        start, end,
    )

    macro = pd.DataFrame(index=raw.index)

    # VIX — direct
    if "^VIX" in raw.columns:
        macro["VIX"] = raw["^VIX"]
    elif "^vix" in raw.columns:
        macro["VIX"] = raw["^vix"]
    else:
        macro["VIX"] = 20.0  # fallback

    # 10Y Treasury Yield — yfinance ^TNX is yield × 10
    if "^TNX" in raw.columns:
        macro["TNX_yield"] = raw["^TNX"] / 10.0
    elif "^tnx" in raw.columns:
        macro["TNX_yield"] = raw["^tnx"] / 10.0
    else:
        macro["TNX_yield"] = 2.0

    # HY Spread proxy: inverse ratio of HYG to LQD
    # Higher HYG/LQD ratio = tighter spreads (risk-on)
    # Lower ratio = wider spreads (risk-off)
    # We convert to approximate basis points
    if "HYG" in raw.columns and "LQD" in raw.columns:
        ratio = raw["HYG"] / raw["LQD"]
        # Normalize: median ratio ≈ 0.72-0.78, map to 300-600 bps range
        ratio_median = ratio.median()
        macro["HY_spread_bps"] = 400 + (ratio_median - ratio) / ratio_median * 800
        macro["HY_spread_bps"] = macro["HY_spread_bps"].clip(100, 1200)
    else:
        macro["HY_spread_bps"] = 400.0

    # PMI — monthly ISM data, forward-filled to daily
    pmi_dates = []
    pmi_values = []
    for ym, val in sorted(ISM_PMI_MONTHLY.items()):
        dt = pd.Timestamp(ym + "-01")
        pmi_dates.append(dt)
        pmi_values.append(val)
    pmi_series = pd.Series(pmi_values, index=pd.DatetimeIndex(pmi_dates), name="PMI")
    pmi_daily = pmi_series.reindex(macro.index, method="ffill")
    # Backfill any leading NaN with the earliest available value
    macro["PMI"] = pmi_daily.bfill()

    # Forward-fill any NaN from market holidays
    macro = macro.ffill().bfill()

    return macro


def load_historical_data(
    start_date: str,
    end_date: str,
    equity_tickers: Optional[List[str]] = None,
    include_individual_tickers: bool = True,
) -> HistoricalDataBundle:
    """
    Load all historical data needed for backtesting.

    Parameters
    ----------
    start_date : str
        Start date in 'YYYY-MM-DD' format.
    end_date : str
        End date in 'YYYY-MM-DD' format.
    equity_tickers : list, optional
        Custom equity ticker list. Defaults to Architecture AB portfolio.
    include_individual_tickers : bool
        If True, fetch individual equity prices. If False, just use QQQ as
        equity sleeve proxy (faster, simpler).

    Returns
    -------
    HistoricalDataBundle
        Complete data package for the simulation engine.
    """
    _ensure_cache_dir()

    if equity_tickers is None:
        equity_tickers = DEFAULT_EQUITY_TICKERS

    # 1. Macro signals
    logger.info("Loading macro signals...")
    macro_signals = _build_macro_signals(start_date, end_date)

    # 2. Benchmark prices (SPY, QQQ)
    logger.info("Loading benchmark prices...")
    benchmark_prices = _fetch_yfinance(BENCHMARK_TICKERS, start_date, end_date)

    # 3. Equity prices
    if include_individual_tickers:
        logger.info(f"Loading {len(equity_tickers)} equity prices...")
        equity_prices = _fetch_yfinance(equity_tickers, start_date, end_date)
    else:
        # Use QQQ as equity sleeve proxy
        equity_prices = benchmark_prices[["QQQ"]].copy()

    # 4. Defensive sleeve prices
    logger.info("Loading defensive sleeve prices...")
    defensive_prices = _fetch_yfinance(DEFENSIVE_TICKERS, start_date, end_date)

    # 5. Crypto prices (BTC-USD as proxy for pre-2024 periods)
    logger.info("Loading crypto prices...")
    crypto_prices = _fetch_yfinance([CRYPTO_PROXY], start_date, end_date)
    if CRYPTO_PROXY in crypto_prices.columns:
        crypto_prices = crypto_prices.rename(columns={CRYPTO_PROXY: "BTC"})

    # Align all DataFrames to the same trading day index
    # Use benchmark as the canonical trading calendar
    trading_days = benchmark_prices.dropna().index
    macro_signals = macro_signals.reindex(trading_days).ffill().bfill()
    equity_prices = equity_prices.reindex(trading_days).ffill().bfill()
    defensive_prices = defensive_prices.reindex(trading_days).ffill().bfill()
    crypto_prices = crypto_prices.reindex(trading_days).ffill().bfill()

    all_tickers = (
        list(equity_prices.columns) +
        list(defensive_prices.columns) +
        list(benchmark_prices.columns)
    )

    bundle = HistoricalDataBundle(
        start_date=start_date,
        end_date=end_date,
        equity_prices=equity_prices,
        defensive_prices=defensive_prices,
        benchmark_prices=benchmark_prices,
        crypto_prices=crypto_prices,
        macro_signals=macro_signals,
        trading_days=trading_days,
        tickers_loaded=all_tickers,
    )

    logger.info(
        f"Data loaded: {len(trading_days)} trading days, "
        f"{len(all_tickers)} tickers, "
        f"{start_date} → {end_date}"
    )
    return bundle


def clear_cache():
    """Remove all cached data files."""
    if CACHE_DIR.exists():
        for f in CACHE_DIR.glob("*.parquet"):
            f.unlink()
        logger.info("Backtest cache cleared.")
