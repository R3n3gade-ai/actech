"""
Historical Data Loader ("Time Machine")
========================================
Fetches and caches historical daily market data for backtesting.

Sources:
  - FRED (Federal Reserve Economic Data): VIXCLS, DGS10, BAMLH0A0HYM2,
    T10Y2Y, BOGZ1FL663067003Q (margin debt) — canonical, authoritative
    macro series. Used when FRED_API_KEY is set; falls back to yfinance
    proxies otherwise.
  - yfinance: Equity prices, crypto, ETFs, fallback macro
  - PMI: Monthly ISM Manufacturing PMI (hand-curated; ISM does NOT license
    to FRED post-2018, so this dictionary is the canonical source).

Known gap: CBOE Equity Put/Call Ratio is NOT available on FRED. Logged once.

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
from urllib.parse import urlencode
from urllib.request import urlopen, Request

import numpy as np
import pandas as pd

# Auto-load .env if python-dotenv is available (so FRED_API_KEY is picked up)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except ImportError:
    pass

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
    "VIX3M": "^VIX3M",   # 3-Month VIX (Volatility Regime sub-score: VIX/VIX3M term structure)
    "TNX": "^TNX",       # 10-Year Treasury Yield (×10 on yfinance)
    "HYG": "HYG",        # High-yield corporate bond ETF
    "LQD": "LQD",        # Investment-grade corporate bond ETF
    "SKEW": "^SKEW",     # CBOE SKEW index (Addendum 7 §3.3 dealer-gamma proxy)
}

# Sector ETFs for Equity Internals sub-score (sector rotation / dispersion)
# Auto Deployment §3.2: Equity Internals = breadth + momentum dispersion + sector rotation
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "XLB", "XLRE", "XLC"]

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
    "2024-09": 47.2, "2024-10": 46.5, "2024-11": 48.4, "2024-12": 49.2,
    "2025-01": 50.9, "2025-02": 50.3, "2025-03": 49.0, "2025-04": 48.7,
    "2025-05": 48.5, "2025-06": 49.0, "2025-07": 48.0, "2025-08": 48.7,
    "2025-09": 49.1, "2025-10": 48.7, "2025-11": 48.2, "2025-12": 47.9,
    "2026-01": 52.6, "2026-02": 52.4, "2026-03": 52.7,
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
    # SPDR sector ETFs (Equity Internals: sector rotation)
    sector_prices: pd.DataFrame = field(default_factory=pd.DataFrame)
    # Volume DataFrames (index=Date, columns=tickers) — for LAEP ADV constraint
    equity_volumes: pd.DataFrame = field(default_factory=pd.DataFrame)
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


def _fetch_yfinance_volume(tickers: List[str], start: str, end: str) -> pd.DataFrame:
    """Fetch daily share volume from yfinance with caching.

    Used by LAEP ADV constraint (Auto Deploy v1.1 §7.2): split orders so
    no single child exceeds 20% of 10-day average daily volume.
    """
    import yfinance as yf

    sig = hashlib.md5(
        f"VOL_{sorted(tickers)}_{start}_{end}".encode()
    ).hexdigest()[:12]
    cache_file = CACHE_DIR / f"volume_{start}_{end}_{sig}.parquet"
    if cache_file.exists():
        logger.info(f"Loading cached volume: {cache_file.name}")
        return pd.read_parquet(cache_file)

    logger.info(f"Fetching volume for {len(tickers)} tickers from yfinance ({start} → {end})...")
    df = yf.download(
        tickers,
        start=start,
        end=end,
        auto_adjust=True,
        progress=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        vol = df["Volume"]
    else:
        vol = df[["Volume"]].rename(columns={"Volume": tickers[0]})

    _ensure_cache_dir()
    vol.to_parquet(cache_file)
    logger.info(f"Cached {len(vol)} volume rows → {cache_file.name}")
    return vol


def _fetch_fred_series(series_id: str, start: str, end: str) -> Optional[pd.Series]:
    """
    Fetch a historical FRED series as a daily-indexed pandas Series.

    Uses Parquet caching (same dir as yfinance). Returns None if FRED_API_KEY
    is missing or the call fails — caller falls back to yfinance proxy.

    Sources:
      VIXCLS         — CBOE VIX (daily)
      DGS10          — 10Y Treasury constant-maturity yield, % (daily)
      BAMLH0A0HYM2   — ICE BofA US High Yield Index OAS, % (daily, canonical)
      T10Y2Y         — 10Y minus 2Y Treasury spread, % (daily)
    """
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        return None

    cache_file = CACHE_DIR / f"fred_{series_id}_{start}_{end}.parquet"
    if cache_file.exists():
        logger.info(f"FRED cache hit: {series_id}")
        return pd.read_parquet(cache_file)[series_id]

    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": start,
        "observation_end": end,
    }
    url = f"https://api.stlouisfed.org/fred/series/observations?{urlencode(params)}"
    try:
        with urlopen(url, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"FRED fetch failed for {series_id}: {e}")
        return None

    obs = payload.get("observations", [])
    rows = [(pd.Timestamp(o["date"]), float(o["value"]))
            for o in obs if o.get("value") not in (None, ".")]
    if not rows:
        logger.warning(f"FRED returned no usable observations for {series_id}")
        return None

    s = pd.Series(dict(rows), name=series_id)
    s.index = pd.DatetimeIndex(s.index)
    s = s.sort_index()

    _ensure_cache_dir()
    s.to_frame().to_parquet(cache_file)
    logger.info(f"FRED cached {len(s)} obs → {cache_file.name}")
    return s


_PCR_GAP_LOGGED = False


def _fetch_cryptocompare_oi(start: str, end: str) -> Optional[pd.DataFrame]:
    """
    Fetch aggregated BTC perpetual Open Interest history from CryptoCompare.

    Free public endpoint (data-api.cryptocompare.com), no API key required.
    Aggregates USD-notional OI across the three major USDT-perp venues:
        Binance, OKX, Bybit
    BitMEX inverse contracts are denominated differently and excluded to keep
    the aggregate clean.

    Source per venue:
      https://data-api.cryptocompare.com/futures/v1/historical/open-interest/days
        ?market={mkt}&instrument=BTC-USDT-VANILLA-PERPETUAL&limit=2000&to_ts=...
      `CLOSE_QUOTE` field = OI in USD notional (close-of-day).

    Returns a DataFrame indexed by daily UTC date with columns:
      btc_oi_current        — aggregated USD OI close-of-day
      btc_oi_30d_high       — rolling 30-day max of btc_oi_current
      btc_oi_24h_change_pct — day-over-day % change of btc_oi_current

    Coverage: 2022-01-30 onward (Binance perp listing depth on CryptoCompare).
    """
    cache_file = CACHE_DIR / f"cc_oi_btc_{start}_{end}.parquet"
    if cache_file.exists():
        logger.info("CryptoCompare OI cache hit")
        return pd.read_parquet(cache_file)

    venues = ["binance", "okex", "bybit"]
    end_ts = int((pd.Timestamp(end) + pd.Timedelta(days=1)).timestamp())

    per_venue: Dict[str, pd.Series] = {}
    base = "https://data-api.cryptocompare.com/futures/v1/historical/open-interest/days"
    try:
        for mkt in venues:
            params = {
                "market": mkt,
                "instrument": "BTC-USDT-VANILLA-PERPETUAL",
                "limit": "2000",
                "to_ts": str(end_ts),
            }
            url = f"{base}?{urlencode(params)}"
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Achelion-ARMS/1.0)"})
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("Data", [])
            if not data:
                logger.warning(f"CryptoCompare OI: no data for {mkt}")
                continue
            rows = []
            for entry in data:
                ts = pd.Timestamp(int(entry["TIMESTAMP"]), unit="s")
                oi_usd = float(entry.get("CLOSE_QUOTE") or 0.0)
                rows.append((ts, oi_usd))
            s = pd.Series(dict(rows), name=mkt).sort_index()
            per_venue[mkt] = s
            logger.info(f"CryptoCompare OI: {mkt} → {len(s)} daily obs")
    except Exception as e:
        logger.warning(f"CryptoCompare OI fetch failed: {e}")
        return None

    if not per_venue:
        return None

    df = pd.concat(per_venue, axis=1).sort_index()
    df = df.fillna(0.0)
    aggregated = df.sum(axis=1).rename("btc_oi_current")
    # Trim to requested window
    start_ts = pd.Timestamp(start)
    end_dt = pd.Timestamp(end)
    aggregated = aggregated.loc[(aggregated.index >= start_ts - pd.Timedelta(days=35))
                                & (aggregated.index <= end_dt)]
    if aggregated.empty:
        logger.warning("CryptoCompare OI: aggregated series empty after trim")
        return None

    # Need 30d lookback for the rolling high — buffer in caller already includes it.
    out = pd.DataFrame({
        "btc_oi_current": aggregated,
        "btc_oi_30d_high": aggregated.rolling(window=30, min_periods=1).max(),
        "btc_oi_24h_change_pct": aggregated.pct_change() * 100.0,
    })
    out.index.name = "Date"

    _ensure_cache_dir()
    out.to_parquet(cache_file)
    logger.info(f"CryptoCompare OI cached {len(out)} daily obs → {cache_file.name}")
    return out


def _fetch_cryptocompare_funding(start: str, end: str) -> Optional[List[tuple]]:
    """
    CryptoCompare aggregated funding rate for Binance BTC-USDT perpetual.

    Free public endpoint (data-api.cryptocompare.com), no API key required. This
    is the canonical Binance perp funding history feed for ARMS Addendum 7 §3.1
    — Binance fapi is geo-blocked from US so we route through CryptoCompare's
    aggregator which scrapes Binance directly.

    Endpoint: https://data-api.cryptocompare.com/futures/v1/historical/funding-rate/days
              ?market=binance&instrument=BTC-USDT-VANILLA-PERPETUAL

    Field `CLOSE_FUNDING_RATE` is the close-of-day 8h funding rate (decimal).

    Returns list of (UTC ts, rate_8h_decimal) tuples — same shape as the Deribit
    fetcher so the caller's daily aggregation logic is unchanged.
    """
    base = "https://data-api.cryptocompare.com/futures/v1/historical/funding-rate/days"
    end_ts = int((pd.Timestamp(end) + pd.Timedelta(days=1)).timestamp())
    start_ts = pd.Timestamp(start).timestamp()
    params = {
        "market": "binance",
        "instrument": "BTC-USDT-VANILLA-PERPETUAL",
        "limit": "2000",
        "to_ts": str(end_ts),
    }
    url = f"{base}?{urlencode(params)}"
    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Achelion-ARMS/1.0)"})
        with urlopen(req, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        logger.warning(f"CryptoCompare funding fetch failed: {e}")
        return None

    data = payload.get("Data", [])
    if not data:
        logger.warning("CryptoCompare funding: empty response")
        return None

    rows: List[tuple] = []
    for entry in data:
        ts = int(entry.get("TIMESTAMP", 0))
        if ts == 0 or ts < start_ts:
            continue
        # Schema: 8h funding rate close-of-day in `CLOSE` (decimal).
        rate_field = entry.get("CLOSE")
        if rate_field is None:
            rate_field = entry.get("CLOSE_FUNDING_RATE")  # legacy field name
        if rate_field is None:
            continue
        rate_8h = float(rate_field)
        # CryptoCompare returns daily close — synthesize as a single 8h sample.
        # Per-day aggregation downstream takes mean, so this is faithful.
        t = pd.Timestamp(ts, unit="s", tz="UTC")
        rows.append((t, rate_8h))
    logger.info(f"CryptoCompare funding: Binance BTC-USDT-PERP → {len(rows)} daily obs")
    return rows


def _fetch_cryptocompare_long_short(start: str, end: str) -> Optional[pd.DataFrame]:
    """
    CryptoCompare global long/short ratio for Binance BTC-USDT perpetual.

    NOTE: CryptoCompare's free Data API does not currently expose a long/short
    ratio endpoint (all probed paths return HTTP 404). This stub remains so
    the call site is preserved for when a free source becomes available
    (e.g., a future Coinglass free-tier proxy or direct OKX / Bybit feeds).
    Returns None — Addendum 8 §3.4 long/short layer accepts None and
    contributes 0.0 per spec for partial-feed environments.
    """
    return None


def _fetch_binance_funding_rate(start: str, end: str, symbol: str = "BTCUSDT") -> Optional[pd.DataFrame]:
    """
    Fetch historical BTC perpetual funding rate.

    Free public APIs, no auth required. Funding paid every 8 hours; we aggregate
    to daily annualized rate (rate_8h × 3 × 365 × 100 = annualized %).

    Source priority (US-accessible with multi-month history):
      1. CryptoCompare — Binance BTC-USDT-VANILLA-PERPETUAL daily close funding.
                          This is the canonical Addendum 7 §3.1 source — Binance
                          fapi is geo-blocked from US so CryptoCompare's
                          aggregator is the in-spec route to Binance funding.
      2. Deribit       — BTC-PERPETUAL hourly funding (multi-year history). Used
                          as fallback if CryptoCompare endpoint changes.
      3. OKX           — short window (~33d), final fallback only.

    Notes:
      • Binance fapi direct is geo-blocked from US (HTTP 451).
      • Bybit blocks via Cloudflare from US (HTTP 403).

    Returns DataFrame with columns:
      btc_funding_rate_ann      — annualized funding rate, %
      btc_funding_rate_8h_delta — change vs prior 8h window, decimal
    """
    cache_file = CACHE_DIR / f"funding_{symbol}_{start}_{end}.parquet"
    if cache_file.exists():
        logger.info(f"Funding cache hit: {symbol}")
        return pd.read_parquet(cache_file)

    rows = _fetch_cryptocompare_funding(start, end)
    source = "CryptoCompare/Binance"
    if rows is None or len(rows) == 0:
        rows = _fetch_deribit_funding(start, end)
        source = "Deribit"
    if rows is None or len(rows) == 0:
        rows = _fetch_okx_funding(symbol, start, end)
        source = "OKX"
    if rows is None or len(rows) == 0:
        logger.warning(f"Funding rate: no data returned for {symbol} (all sources failed)")
        return None

    raw = pd.DataFrame(rows, columns=["ts", "rate_8h"]).drop_duplicates("ts").set_index("ts").sort_index()
    raw["rate_8h_delta"] = raw["rate_8h"].diff()

    raw_daily_idx = raw.index.tz_convert(None).normalize() if raw.index.tz is not None else raw.index.normalize()
    raw_for_daily = raw.copy()
    raw_for_daily.index = raw_daily_idx
    daily_rate = raw_for_daily["rate_8h"].groupby(level=0).mean() * 3.0 * 365.0 * 100.0
    daily_delta = raw_for_daily["rate_8h_delta"].groupby(level=0).min()

    out = pd.DataFrame({
        "btc_funding_rate_ann": daily_rate,
        "btc_funding_rate_8h_delta": daily_delta,
    })
    out.index.name = "Date"

    _ensure_cache_dir()
    out.to_parquet(cache_file)
    logger.info(f"Funding cached {len(out)} daily obs from {source} → {cache_file.name}")
    return out


def _fetch_deribit_funding(start: str, end: str, instrument: str = "BTC-PERPETUAL") -> Optional[List[tuple]]:
    """Deribit public funding rate history. Hourly granularity. Paginate by month chunks."""
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int((pd.Timestamp(end) + pd.Timedelta(days=1)).timestamp() * 1000)
    base = "https://www.deribit.com/api/v2/public/get_funding_rate_history"
    all_rows: List[tuple] = []
    seen = set()

    # Chunk into ~25-day windows to stay well under any pagination cap
    chunk_ms = 25 * 24 * 3600 * 1000
    cursor = start_ms
    try:
        while cursor < end_ms:
            chunk_end = min(cursor + chunk_ms, end_ms)
            params = {
                "instrument_name": instrument,
                "start_timestamp": str(cursor),
                "end_timestamp": str(chunk_end),
            }
            url = f"{base}?{urlencode(params)}"
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Achelion-ARMS/1.0)"})
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            entries = payload.get("result", [])
            for e in entries:
                ts_ms = int(e["timestamp"])
                if ts_ms in seen:
                    continue
                seen.add(ts_ms)
                t = pd.Timestamp(ts_ms, unit="ms", tz="UTC")
                rate_8h = float(e.get("interest_8h", 0.0))
                all_rows.append((t, rate_8h))
            cursor = chunk_end + 1
    except Exception as e:
        logger.warning(f"Deribit funding fetch failed: {e}")
        return None
    return all_rows


def _fetch_okx_funding(symbol: str, start: str, end: str) -> Optional[List[tuple]]:
    """OKX v5 public funding-rate history. instId mapping BTCUSDT → BTC-USDT-SWAP.

    OKX pagination uses `after` to fetch records older than that ts (max 100 per page).
    We cannot use `before` and `after` together — paginate purely backwards from `end`.
    """
    inst_id = symbol.replace("USDT", "-USDT-SWAP") if symbol.endswith("USDT") else symbol
    start_ms = int(pd.Timestamp(start).timestamp() * 1000)
    end_ms = int((pd.Timestamp(end) + pd.Timedelta(days=1)).timestamp() * 1000)
    base = "https://www.okx.com/api/v5/public/funding-rate-history"
    all_rows: List[tuple] = []
    cursor_after = end_ms
    seen = set()
    try:
        for _ in range(2000):  # safety bound; ~3 entries/day → 100/page = ~33d/page
            params = {
                "instId": inst_id,
                "after": str(cursor_after),
                "limit": "100",
            }
            url = f"{base}?{urlencode(params)}"
            req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; Achelion-ARMS/1.0)"})
            with urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
            if payload.get("code") != "0":
                logger.warning(f"OKX funding error: {payload.get('msg')}")
                return None
            entries = payload.get("data", [])
            if not entries:
                break
            oldest_ts = None
            new_count = 0
            for e in entries:
                ts_ms = int(e["fundingTime"])
                if ts_ms in seen:
                    continue
                seen.add(ts_ms)
                if ts_ms < start_ms:
                    oldest_ts = ts_ms
                    continue  # skip but still track for pagination cutoff
                if ts_ms >= end_ms:
                    continue
                t = pd.Timestamp(ts_ms, unit="ms", tz="UTC")
                rate_str = e.get("realizedRate") or e.get("fundingRate")
                rate = float(rate_str)
                all_rows.append((t, rate))
                new_count += 1
                oldest_ts = ts_ms if oldest_ts is None or ts_ms < oldest_ts else oldest_ts
            if oldest_ts is None or oldest_ts <= start_ms:
                break
            if new_count == 0 and oldest_ts >= cursor_after:
                break  # no progress
            cursor_after = oldest_ts
    except Exception as e:
        logger.warning(f"OKX funding fetch failed: {e}")
        return None
    return all_rows


def _check_pmi_staleness(end_date: str) -> None:
    """Honest staleness guard: ISM PMI is hand-curated (not on FRED post-2018).
    Warn loudly if backtest extends beyond the last curated month + 45 days.
    """
    if not ISM_PMI_MONTHLY:
        raise RuntimeError("ISM_PMI_MONTHLY is empty — cannot run backtest without PMI data.")
    last_ym = max(ISM_PMI_MONTHLY.keys())
    last_dt = pd.Timestamp(last_ym + "-01") + pd.offsets.MonthEnd(0)
    end_dt = pd.Timestamp(end_date)
    lag_days = (end_dt - last_dt).days
    if lag_days > 45:
        logger.warning(
            f"PMI STALENESS: backtest end={end_date} is {lag_days} days past "
            f"last curated ISM month ({last_ym}). ISM PMI is NOT licensed to FRED "
            f"post-2018; the ISM_PMI_MONTHLY dict in data_loader.py must be updated "
            f"manually with the latest ISM Manufacturing PMI release. Backtest will "
            f"forward-fill {last_ym} into the gap — results past that date may be inaccurate."
        )


def _build_macro_signals(start: str, end: str) -> pd.DataFrame:
    """
    Build daily macro signal DataFrame.

    Canonical sources (per ARMS Infrastructure Spec §3 / Macro Compass +
    Addendum 7 §3.4 for margin debt):
      - VIX            : FRED VIXCLS  (fallback: yfinance ^VIX)
      - TNX_yield      : FRED DGS10   (fallback: yfinance ^TNX / 10)
      - HY_spread_bps  : FRED BAMLH0A0HYM2 × 100 (canonical ICE BofA OAS)
                         (fallback: HYG/LQD ratio proxy)
      - T10Y2Y         : FRED T10Y2Y  (yield curve slope, advisory)
      - MARGIN_DEBT    : FRED BOGZ1FL663067003Q (quarterly, ffill to daily)
      - MARGIN_DEBT_QOQ_PCT : derived QoQ % change (forced-selling proxy per Add. 7)
      - PMI            : ISM Manufacturing PMI (hand-curated dict, ISM not on FRED)

    Known gap (logged once): CBOE Equity Put/Call Ratio is not on FRED.
    """
    global _PCR_GAP_LOGGED

    _check_pmi_staleness(end)

    if not _PCR_GAP_LOGGED:
        logger.warning(
            "PCR GAP: CBOE Equity Put/Call Ratio is not published to FRED. "
            "Backtest does not include PCR signal. Live-system PCR would require "
            "a CBOE direct-feed or paid data subscription."
        )
        _PCR_GAP_LOGGED = True

    # ── FRED canonical series ──
    fred_vix       = _fetch_fred_series("VIXCLS",            start, end)
    fred_dgs10     = _fetch_fred_series("DGS10",             start, end)
    fred_hy_oas    = _fetch_fred_series("BAMLH0A0HYM2",      start, end)
    fred_t10y2y    = _fetch_fred_series("T10Y2Y",            start, end)
    # Auto Deployment §3.2 Credit Stress Index — IG CDS proxy via BBB OAS.
    # BAMLC0A4CBBB = ICE BofA BBB Corporate OAS (daily, % above Treasury).
    fred_ig_oas    = _fetch_fred_series("BAMLC0A4CBBB",      start, end)
    # AAA OAS — funding stress proxy (TED spread replacement; TED discontinued Jan 2022).
    fred_aaa_oas   = _fetch_fred_series("BAMLC0A1CAAA",      start, end)
    # Auto Deployment §3.2 Liquidity Conditions — Fed balance sheet (weekly).
    # WALCL = total assets, $millions, Wednesdays. Wider window for 4w change.
    walcl_start    = (pd.Timestamp(start) - pd.DateOffset(months=2)).strftime("%Y-%m-%d")
    fred_walcl     = _fetch_fred_series("WALCL",             walcl_start, end)
    # SOFR (Secured Overnight Financing Rate, daily since 2018) — repo stress.
    fred_sofr      = _fetch_fred_series("SOFR",              start, end)
    # 3M T-bill yield — for SOFR–T-bill spread (repo funding stress).
    fred_dgs3mo    = _fetch_fred_series("DGS3MO",            start, end)
    # Trade-Weighted USD Broad — DXY proxy (DTWEXBGS daily, indexed Jan 2006=100).
    fred_dxy       = _fetch_fred_series("DTWEXBGS",          start, end)
    # Fed Funds effective rate — for rate-hike-momentum proxy (Macro Momentum).
    fred_dff       = _fetch_fred_series("DFF",               start, end)
    # Margin debt: quarterly series, fetch with wider window so we have a
    # prior quarter for QoQ pct change even when start is mid-quarter.
    margin_start = (pd.Timestamp(start) - pd.DateOffset(months=6)).strftime("%Y-%m-%d")
    fred_margin    = _fetch_fred_series("BOGZ1FL663067003Q", margin_start, end)

    # Always fetch yfinance for fallback + crypto-proxy macro tickers
    raw = _fetch_yfinance(list(MACRO_TICKERS.values()), start, end)
    macro = pd.DataFrame(index=raw.index)

    # VIX (REQUIRED — Volatility Regime sub-score core input)
    if fred_vix is not None:
        macro["VIX"] = fred_vix
        logger.info("Macro: VIX from FRED VIXCLS (canonical)")
    elif "^VIX" in raw.columns:
        macro["VIX"] = raw["^VIX"]
    elif "^vix" in raw.columns:
        macro["VIX"] = raw["^vix"]
    else:
        raise RuntimeError("VIX unavailable from FRED VIXCLS and yfinance ^VIX. Required input.")

    # 10Y Treasury yield (REQUIRED)
    if fred_dgs10 is not None:
        macro["TNX_yield"] = fred_dgs10
        logger.info("Macro: TNX from FRED DGS10 (canonical)")
    elif "^TNX" in raw.columns:
        macro["TNX_yield"] = raw["^TNX"] / 10.0
    elif "^tnx" in raw.columns:
        macro["TNX_yield"] = raw["^tnx"] / 10.0
    else:
        raise RuntimeError("10Y Treasury yield unavailable from FRED DGS10 and yfinance ^TNX. Required input.")

    # HY OAS (REQUIRED — canonical ICE BofA OAS, in % → bps)
    if fred_hy_oas is not None:
        macro["HY_spread_bps"] = fred_hy_oas * 100.0
        logger.info("Macro: HY spread from FRED BAMLH0A0HYM2 (canonical OAS)")
    else:
        raise RuntimeError("HY OAS unavailable from FRED BAMLH0A0HYM2. Required input.")

    # T10Y2Y yield curve (Macro Momentum input — NaN if missing, _blend renormalizes)
    if fred_t10y2y is not None:
        macro["T10Y2Y"] = fred_t10y2y
        logger.info("Macro: T10Y2Y from FRED (advisory yield-curve signal)")
    else:
        macro["T10Y2Y"] = np.nan
        logger.warning("Macro: T10Y2Y (FRED) unavailable — yield-curve sub-input missing")

    # Margin Debt (FRED BOGZ1FL663067003Q, quarterly) + QoQ % change
    # Per Addendum 7 §3.4: rapid growth + contraction = forced selling lead signal.
    if fred_margin is not None and len(fred_margin) >= 2:
        # Resample to quarterly observations (drop daily ffill duplicates), compute QoQ
        margin_q = fred_margin.dropna().drop_duplicates()
        margin_qoq_pct = margin_q.pct_change() * 100.0
        # Forward-fill quarterly observations into daily index
        macro["MARGIN_DEBT"] = margin_q.reindex(macro.index, method="ffill")
        macro["MARGIN_DEBT_QOQ_PCT"] = margin_qoq_pct.reindex(macro.index, method="ffill")
        # Addendum 7 §3.4 explicitly calls for "3-month growth" (= QoQ for quarterly
        # FRED data) and "MoM change". For our quarterly source, MoM degrades to
        # latest-quarter % change (same as QoQ when within the same quarter).
        macro["MARGIN_DEBT_3M_GROWTH_PCT"] = macro["MARGIN_DEBT_QOQ_PCT"]
        macro["MARGIN_DEBT_MOM_CHANGE_PCT"] = macro["MARGIN_DEBT_QOQ_PCT"]
        logger.info("Macro: Margin Debt from FRED BOGZ1FL663067003Q (advisory, quarterly)")
    else:
        macro["MARGIN_DEBT"] = np.nan
        macro["MARGIN_DEBT_QOQ_PCT"] = np.nan
        macro["MARGIN_DEBT_3M_GROWTH_PCT"] = np.nan
        macro["MARGIN_DEBT_MOM_CHANGE_PCT"] = np.nan
        logger.warning("Macro: Margin Debt unavailable — advisory signal disabled")

    # CBOE SKEW index (Addendum 7 §3.3 — Phase 1 dealer-gamma proxy via ^SKEW).
    if "^SKEW" in raw.columns:
        macro["CBOE_SKEW"] = raw["^SKEW"]
        logger.info("Macro: CBOE SKEW from yfinance ^SKEW (Addendum 7 §3.3 Phase 1)")
    elif "^skew" in raw.columns:
        macro["CBOE_SKEW"] = raw["^skew"]
        logger.info("Macro: CBOE SKEW from yfinance ^skew (Addendum 7 §3.3 Phase 1)")
    else:
        macro["CBOE_SKEW"] = np.nan
        logger.warning("Macro: CBOE SKEW unavailable — Addendum 7 skew signal disabled")

    # BTC perpetual funding rate (Addendum 7 §3.1 — primary deleveraging-risk layer).
    # Sources: Binance public futures API (free, no auth, full history back to 2019).
    binance_funding = _fetch_binance_funding_rate(start, end)
    if binance_funding is not None:
        macro["BTC_FUNDING_RATE_ANN"] = binance_funding["btc_funding_rate_ann"].reindex(macro.index, method="ffill")
        macro["BTC_FUNDING_RATE_8H_DELTA"] = binance_funding["btc_funding_rate_8h_delta"].reindex(macro.index, method="ffill")
        logger.info("Macro: BTC funding rate wired (Addendum 7 §3.1 — CryptoCompare/Binance primary, Deribit/OKX fallback)")
    else:
        macro["BTC_FUNDING_RATE_ANN"] = np.nan
        macro["BTC_FUNDING_RATE_8H_DELTA"] = np.nan
        logger.warning("Macro: BTC funding rate unavailable — Addendum 7 funding-rate layer disabled")

    # BTC aggregated Open Interest (Addendum 7 §3.2 — OI fragility layer).
    # Source: CryptoCompare data-api free endpoint, BTC-USDT perp on Binance+OKX+Bybit.
    # Fetch with 35d buffer so the 30d rolling high is valid at start of window.
    oi_start = (pd.Timestamp(start) - pd.DateOffset(days=35)).strftime("%Y-%m-%d")
    cc_oi = _fetch_cryptocompare_oi(oi_start, end)
    if cc_oi is not None:
        macro["BTC_OI_CURRENT"] = cc_oi["btc_oi_current"].reindex(macro.index, method="ffill")
        macro["BTC_OI_30D_HIGH"] = cc_oi["btc_oi_30d_high"].reindex(macro.index, method="ffill")
        macro["BTC_OI_24H_CHANGE_PCT"] = cc_oi["btc_oi_24h_change_pct"].reindex(macro.index, method="ffill")
        logger.info("Macro: BTC OI from CryptoCompare aggregate (Binance+OKX+Bybit USDT-perp, Addendum 7 §3.2)")
    else:
        macro["BTC_OI_CURRENT"] = np.nan
        macro["BTC_OI_30D_HIGH"] = np.nan
        macro["BTC_OI_24H_CHANGE_PCT"] = np.nan
        logger.warning("Macro: BTC OI unavailable — Addendum 7 OI-fragility layer disabled")

    # BTC long/short ratio (Addendum 8 §3.4 — long/short extremity layer).
    # Source: CryptoCompare global account ratio for Binance BTC-USDT perp (free).
    cc_ls = _fetch_cryptocompare_long_short(start, end)
    if cc_ls is not None:
        macro["BTC_LONG_PCT_WEIGHTED"] = cc_ls["btc_long_pct_weighted"].reindex(macro.index, method="ffill")
        logger.info("Macro: BTC long/short from CryptoCompare/Binance (Addendum 8 §3.4)")
    else:
        macro["BTC_LONG_PCT_WEIGHTED"] = np.nan
        logger.warning("Macro: BTC long/short unavailable — Addendum 8 long-short layer disabled")

    # ── Auto Deployment §3.2 Composite Score Inputs (canonical) ──────────────
    # Volatility Regime sub-score: VIX/VIX3M term structure + realized vol.
    if "^VIX3M" in raw.columns:
        macro["VIX3M"] = raw["^VIX3M"]
        logger.info("Macro: VIX3M from yfinance ^VIX3M (Volatility Regime term-structure)")
    elif "^vix3m" in raw.columns:
        macro["VIX3M"] = raw["^vix3m"]
    else:
        macro["VIX3M"] = np.nan
        logger.warning("Macro: VIX3M unavailable from yfinance — term structure disabled")

    # Credit Stress sub-score additions: IG (BBB) OAS, AAA OAS (TED replacement).
    if fred_ig_oas is not None:
        macro["IG_OAS_PCT"] = fred_ig_oas
        logger.info("Macro: IG OAS from FRED BAMLC0A4CBBB (Credit Stress Index)")
    else:
        macro["IG_OAS_PCT"] = np.nan
        logger.warning("Macro: IG OAS (BAMLC0A4CBBB) unavailable — Credit Stress contribution reduced")

    if fred_aaa_oas is not None:
        macro["AAA_OAS_PCT"] = fred_aaa_oas
        logger.info("Macro: AAA OAS from FRED BAMLC0A1CAAA (TED spread replacement)")
    else:
        macro["AAA_OAS_PCT"] = np.nan
        logger.warning("Macro: AAA OAS (BAMLC0A1CAAA) unavailable — Credit Stress contribution reduced")

    # Liquidity Conditions sub-score: Fed balance sheet 4-week % change.
    if fred_walcl is not None and len(fred_walcl) >= 5:
        walcl_daily = fred_walcl.reindex(macro.index, method="ffill")
        walcl_4w_pct = walcl_daily.pct_change(periods=20) * 100.0
        macro["FED_BS_4W_PCT"] = walcl_4w_pct
        logger.info("Macro: Fed Balance Sheet 4w % change from FRED WALCL (Liquidity Conditions)")
    else:
        macro["FED_BS_4W_PCT"] = np.nan
        logger.warning("Macro: WALCL unavailable — Fed BS sub-input missing")

    # Liquidity Conditions: SOFR–3M T-bill spread (positive wide = repo stress).
    if fred_sofr is not None and fred_dgs3mo is not None:
        sofr_d = fred_sofr.reindex(macro.index, method="ffill")
        tbill_d = fred_dgs3mo.reindex(macro.index, method="ffill")
        macro["REPO_STRESS_BPS"] = (sofr_d - tbill_d) * 100.0
        logger.info("Macro: Repo stress from FRED SOFR − DGS3MO (Liquidity Conditions)")
    else:
        macro["REPO_STRESS_BPS"] = np.nan
        logger.warning("Macro: SOFR or DGS3MO unavailable — repo stress sub-input missing")

    # Liquidity Conditions: DXY (Trade-Weighted USD Broad).
    if fred_dxy is not None:
        dxy_d = fred_dxy.reindex(macro.index, method="ffill")
        macro["DXY_LEVEL"] = dxy_d
        macro["DXY_20D_PCT"] = dxy_d.pct_change(periods=20) * 100.0
        logger.info("Macro: DXY from FRED DTWEXBGS (Liquidity Conditions)")
    else:
        macro["DXY_LEVEL"] = np.nan
        macro["DXY_20D_PCT"] = np.nan
        logger.warning("Macro: DTWEXBGS unavailable — DXY sub-input missing")

    # Macro Momentum: rate-hike momentum proxy via Fed Funds 3-month change.
    if fred_dff is not None:
        dff_d = fred_dff.reindex(macro.index, method="ffill")
        macro["DFF_3M_CHANGE_BPS"] = (dff_d - dff_d.shift(63)) * 100.0
        logger.info("Macro: Fed Funds 3m change from FRED DFF (rate-hike momentum)")
    else:
        macro["DFF_3M_CHANGE_BPS"] = np.nan
        logger.warning("Macro: DFF unavailable — rate-hike momentum sub-input missing")

    # PMI — monthly ISM data, forward-filled to daily
    pmi_dates = []
    pmi_values = []
    for ym, val in sorted(ISM_PMI_MONTHLY.items()):
        dt = pd.Timestamp(ym + "-01")
        pmi_dates.append(dt)
        pmi_values.append(val)
    pmi_series = pd.Series(pmi_values, index=pd.DatetimeIndex(pmi_dates), name="PMI")
    pmi_daily = pmi_series.reindex(macro.index, method="ffill")
    macro["PMI"] = pmi_daily.bfill()

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
        # 3b. Equity volumes for LAEP ADV constraint (Auto Deploy v1.1 §7.2)
        try:
            equity_volumes = _fetch_yfinance_volume(equity_tickers, start_date, end_date)
        except Exception as exc:
            logger.warning(f"Volume fetch failed: {exc} — LAEP ADV constraint disabled")
            equity_volumes = pd.DataFrame()
    else:
        # Use QQQ as equity sleeve proxy
        equity_prices = benchmark_prices[["QQQ"]].copy()
        equity_volumes = pd.DataFrame()

    # 4. Defensive sleeve prices
    logger.info("Loading defensive sleeve prices...")
    defensive_prices = _fetch_yfinance(DEFENSIVE_TICKERS, start_date, end_date)

    # 5. Crypto prices (BTC-USD as proxy for pre-2024 periods)
    logger.info("Loading crypto prices...")
    crypto_prices = _fetch_yfinance([CRYPTO_PROXY], start_date, end_date)
    if CRYPTO_PROXY in crypto_prices.columns:
        crypto_prices = crypto_prices.rename(columns={CRYPTO_PROXY: "BTC"})

    # 6. Sector ETFs (Auto Deployment §3.2 Equity Internals: sector rotation)
    logger.info(f"Loading {len(SECTOR_ETFS)} sector ETFs...")
    try:
        sector_prices = _fetch_yfinance(SECTOR_ETFS, start_date, end_date)
    except Exception as exc:
        logger.warning(f"Sector ETF fetch failed: {exc} — sector rotation signal disabled")
        sector_prices = pd.DataFrame()

    # Align all DataFrames to the same trading day index
    # Use benchmark as the canonical trading calendar
    trading_days = benchmark_prices.dropna().index
    macro_signals = macro_signals.reindex(trading_days).ffill().bfill()
    equity_prices = equity_prices.reindex(trading_days).ffill().bfill()
    defensive_prices = defensive_prices.reindex(trading_days).ffill().bfill()
    crypto_prices = crypto_prices.reindex(trading_days).ffill().bfill()
    if not sector_prices.empty:
        sector_prices = sector_prices.reindex(trading_days).ffill().bfill()
    if not equity_volumes.empty:
        equity_volumes = equity_volumes.reindex(trading_days).ffill().bfill()

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
        sector_prices=sector_prices,
        macro_signals=macro_signals,
        trading_days=trading_days,
        tickers_loaded=all_tickers,
        equity_volumes=equity_volumes,
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
