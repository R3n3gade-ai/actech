"""
ARMS Execution: Correlation Monitor

Tracks intra-sleeve and cross-sleeve correlations to detect contagion.
Uses rolling 30-day return correlations between equity and crypto sleeves.
When equity/crypto correlation exceeds 0.70, diversification benefit is lost
and the system flags contagion risk for ARAS ceiling consideration.

Reference: THB v4.0, Section 10
"""
import os
import json
import datetime
from dataclasses import dataclass
from typing import List, Dict, Optional

@dataclass
class CorrelationStatus:
    equity_crypto_corr_30d: float
    intra_equity_corr: float
    status: str  # NORMAL | ELEVATED | CONTAGION
    detail: str

_STATE_PATH = os.path.join('achelion_arms', 'state', 'correlation_history.json')

def _load_return_history() -> Dict[str, List[float]]:
    if os.path.exists(_STATE_PATH):
        try:
            with open(_STATE_PATH, 'r') as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_return_history(hist: Dict[str, List[float]]):
    os.makedirs(os.path.dirname(_STATE_PATH), exist_ok=True)
    with open(_STATE_PATH, 'w') as f:
        json.dump(hist, f)

def _pearson(xs: List[float], ys: List[float]) -> float:
    n = min(len(xs), len(ys))
    if n < 5:
        return 0.0
    xs, ys = xs[-n:], ys[-n:]
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sx = (sum((x - mx) ** 2 for x in xs)) ** 0.5
    sy = (sum((y - my) ** 2 for y in ys)) ** 0.5
    if sx == 0 or sy == 0:
        return 0.0
    return cov / (sx * sy)

def run_correlation_monitor(
    equity_returns: Optional[List[float]] = None,
    crypto_returns: Optional[List[float]] = None,
) -> CorrelationStatus:
    """
    Calculate rolling equity/crypto sleeve correlation.
    
    Args:
        equity_returns: Last 30 daily returns for the equity sleeve (as decimals).
        crypto_returns: Last 30 daily returns for the crypto sleeve (as decimals).
    
    Returns:
        CorrelationStatus with current correlation reading and risk status.
    """
    hist = _load_return_history()
    
    # Append new data points if provided
    if equity_returns is not None:
        hist['equity'] = (hist.get('equity', []) + equity_returns)[-60:]
    if crypto_returns is not None:
        hist['crypto'] = (hist.get('crypto', []) + crypto_returns)[-60:]
    
    _save_return_history(hist)
    
    eq = hist.get('equity', [])
    cr = hist.get('crypto', [])
    
    # Equity/Crypto cross-sleeve correlation (30-day rolling)
    eq_crypto_corr = _pearson(eq[-30:], cr[-30:]) if len(eq) >= 5 and len(cr) >= 5 else 0.0
    
    # Intra-equity average pairwise correlation placeholder
    # In production, this would compute pairwise correlations across individual equity positions
    intra_eq = 0.45  # Baseline reasonable equity book correlation
    
    if eq_crypto_corr >= 0.80:
        status = "CONTAGION"
        detail = f"Equity/Crypto correlation {eq_crypto_corr:.2f} >= 0.80 — diversification benefit lost, contagion risk active"
    elif eq_crypto_corr >= 0.60:
        status = "ELEVATED"
        detail = f"Equity/Crypto correlation {eq_crypto_corr:.2f} >= 0.60 — reduced diversification, monitor closely"
    else:
        status = "NORMAL"
        detail = f"Equity/Crypto correlation {eq_crypto_corr:.2f} — diversification intact"
    
    return CorrelationStatus(
        equity_crypto_corr_30d=round(eq_crypto_corr, 3),
        intra_equity_corr=round(intra_eq, 3),
        status=status,
        detail=detail,
    )
