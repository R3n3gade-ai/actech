"""
Black-Scholes Options Proxy for PTRH Backtesting
==================================================
Estimates the value of QQQ put options at -0.35 delta without
requiring expensive historical options chain data.

Uses VIX as implied volatility proxy and standard Black-Scholes
formula to price protective puts with 60-90 DTE rolling windows.
"""

import logging
import math
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import norm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RISK_FREE_RATE = 0.04          # Approximate average 2020-2023
TARGET_DELTA = -0.35           # Per PTRH spec
TARGET_DTE_RANGE = (60, 90)    # Days to expiry
ROLL_TRIGGER_DTE = 30          # Roll at 30 DTE
QQQ_CONTRACT_MULTIPLIER = 100  # Standard options contract


@dataclass
class PutPosition:
    """Represents a simulated QQQ put position."""
    strike: float
    expiry_date: pd.Timestamp
    contracts: int
    entry_price: float         # Premium per share at entry
    entry_date: pd.Timestamp
    notional_value: float      # contracts × multiplier × strike

    @property
    def dte(self) -> int:
        """Days to expiry from today (set externally)."""
        return 0  # Overridden in mark-to-market

    def mark_to_market(self, current_date: pd.Timestamp,
                       underlying_price: float, vix: float) -> float:
        """Current value of the put position."""
        days_left = (self.expiry_date - current_date).days
        if days_left <= 0:
            # Expired — intrinsic value only
            intrinsic = max(0, self.strike - underlying_price)
            return intrinsic * self.contracts * QQQ_CONTRACT_MULTIPLIER
        price = black_scholes_put(
            S=underlying_price,
            K=self.strike,
            T=days_left / 365.0,
            r=RISK_FREE_RATE,
            sigma=vix / 100.0,
        )
        return price * self.contracts * QQQ_CONTRACT_MULTIPLIER


def black_scholes_put(S: float, K: float, T: float, r: float,
                      sigma: float) -> float:
    """
    Black-Scholes European put option price.

    Parameters
    ----------
    S : float — Current underlying price
    K : float — Strike price
    T : float — Time to expiry in years
    r : float — Risk-free rate
    sigma : float — Implied volatility (annualized, decimal)

    Returns
    -------
    float — Put option price per share
    """
    if T <= 0 or sigma <= 0 or S <= 0:
        return max(0, K - S)

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)

    put_price = (K * math.exp(-r * T) * norm.cdf(-d2)
                 - S * norm.cdf(-d1))
    return max(0.0, put_price)


def black_scholes_delta_put(S: float, K: float, T: float, r: float,
                            sigma: float) -> float:
    """Put option delta (negative value between -1 and 0)."""
    if T <= 0 or sigma <= 0 or S <= 0:
        return -1.0 if K > S else 0.0

    d1 = (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    return norm.cdf(d1) - 1.0


def find_strike_for_delta(
    underlying_price: float,
    target_delta: float,
    dte: int,
    vix: float,
    r: float = RISK_FREE_RATE,
) -> float:
    """
    Find the put strike that produces the target delta.
    Uses bisection search since BS delta is monotonic in strike.

    Parameters
    ----------
    underlying_price : float — Current QQQ price
    target_delta : float — Target put delta (e.g., -0.35)
    dte : int — Days to expiry
    vix : float — VIX level (used as IV proxy)
    r : float — Risk-free rate

    Returns
    -------
    float — Strike price rounded to nearest dollar
    """
    T = dte / 365.0
    sigma = vix / 100.0

    if T <= 0 or sigma <= 0:
        return underlying_price * 0.90

    # Bisection: THB v4.0 FINAL mandates 10-15% OTM (0.85-0.90 × spot)
    lo = underlying_price * 0.85
    hi = underlying_price * 0.90

    for _ in range(100):
        mid = (lo + hi) / 2.0
        delta = black_scholes_delta_put(underlying_price, mid, T, r, sigma)
        if delta < target_delta:
            hi = mid
        else:
            lo = mid
        if abs(hi - lo) < 0.50:
            break

    return round((lo + hi) / 2.0)


class PTRHSimulator:
    """
    Simulates PTRH (Portfolio Tail Risk Hedge) using Black-Scholes proxy.

    Manages a rolling QQQ put position:
      - Entry: Buy puts at -0.35 delta, 75 DTE
      - Roll: At 30 DTE, sell old puts and buy new ones
      - Sizing: Based on regime table notional requirement (THB v4.0 FINAL)
    """

    def __init__(self):
        self.current_position: Optional[PutPosition] = None
        self.total_premium_spent: float = 0.0
        self.total_premium_received: float = 0.0
        self.roll_count: int = 0
        self.last_roll_date: Optional[pd.Timestamp] = None

    def step(
        self,
        date: pd.Timestamp,
        qqq_price: float,
        vix: float,
        target_notional: float,
    ) -> dict:
        """
        Execute one day of PTRH simulation.

        Parameters
        ----------
        date : pd.Timestamp — Current simulation date
        qqq_price : float — QQQ closing price
        vix : float — VIX level
        target_notional : float — Required put notional from CAM

        Returns
        -------
        dict with keys:
          - ptrh_value: current mark-to-market value of puts
          - ptrh_notional: notional exposure
          - ptrh_cost: cumulative premium cost
          - ptrh_action: 'HOLD' | 'OPEN' | 'ROLL' | 'NONE'
        """
        action = "HOLD"

        # Case 1: No position — open new one
        if self.current_position is None:
            if target_notional > 0 and qqq_price > 0:
                self._open_position(date, qqq_price, vix, target_notional)
                action = "OPEN"

        else:
            # Check for roll trigger
            dte = (self.current_position.expiry_date - date).days

            if dte <= ROLL_TRIGGER_DTE:
                # Close current position (mark-to-market value)
                close_value = self.current_position.mark_to_market(
                    date, qqq_price, vix
                )
                self.total_premium_received += close_value

                # Open new position
                self._open_position(date, qqq_price, vix, target_notional)
                self.roll_count += 1
                self.last_roll_date = date
                action = "ROLL"

            elif dte <= 0:
                # Expired — exercise if ITM
                intrinsic = max(0, self.current_position.strike - qqq_price)
                exercise_value = (intrinsic * self.current_position.contracts
                                  * QQQ_CONTRACT_MULTIPLIER)
                self.total_premium_received += exercise_value
                self.current_position = None

                # Re-open
                if target_notional > 0:
                    self._open_position(date, qqq_price, vix, target_notional)
                    action = "ROLL"

        # Mark to market
        ptrh_value = 0.0
        ptrh_notional = 0.0
        if self.current_position is not None:
            ptrh_value = self.current_position.mark_to_market(
                date, qqq_price, vix
            )
            ptrh_notional = self.current_position.notional_value

        net_cost = self.total_premium_spent - self.total_premium_received

        return {
            "ptrh_value": ptrh_value,
            "ptrh_notional": ptrh_notional,
            "ptrh_net_cost": net_cost,
            "ptrh_action": action,
            "ptrh_roll_count": self.roll_count,
        }

    def _open_position(self, date: pd.Timestamp, qqq_price: float,
                       vix: float, target_notional: float):
        """Open a new put position sized to target notional."""
        dte = 75  # Mid-range of 60-90 DTE window
        strike = find_strike_for_delta(qqq_price, TARGET_DELTA, dte, vix)

        # Calculate contracts needed
        per_contract_notional = strike * QQQ_CONTRACT_MULTIPLIER
        if per_contract_notional > 0:
            contracts = max(1, int(target_notional / per_contract_notional))
        else:
            contracts = 1

        # Calculate entry premium
        T = dte / 365.0
        sigma = vix / 100.0
        entry_price = black_scholes_put(qqq_price, strike, T,
                                        RISK_FREE_RATE, sigma)

        premium_cost = entry_price * contracts * QQQ_CONTRACT_MULTIPLIER
        self.total_premium_spent += premium_cost

        self.current_position = PutPosition(
            strike=strike,
            expiry_date=date + pd.Timedelta(days=dte),
            contracts=contracts,
            entry_price=entry_price,
            entry_date=date,
            notional_value=per_contract_notional * contracts,
        )


def estimate_covered_call_premium(
    underlying_price: float,
    vix: float,
    dte: int = 30,
    delta: float = 0.30,
) -> float:
    """
    Estimate covered call premium for PERM backtesting.
    Returns premium per share.
    """
    sigma = vix / 100.0
    T = dte / 365.0

    # Find OTM call strike at target delta
    lo = underlying_price * 0.98
    hi = underlying_price * 1.30

    for _ in range(80):
        mid = (lo + hi) / 2.0
        d1 = (math.log(underlying_price / mid) +
              (RISK_FREE_RATE + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
        call_delta = norm.cdf(d1)
        if call_delta > delta:
            lo = mid
        else:
            hi = mid
        if abs(hi - lo) < 0.50:
            break

    strike = round((lo + hi) / 2.0)

    # Price the call
    d1 = (math.log(underlying_price / strike) +
          (RISK_FREE_RATE + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))
    d2 = d1 - sigma * math.sqrt(T)
    call_price = (underlying_price * norm.cdf(d1)
                  - strike * math.exp(-RISK_FREE_RATE * T) * norm.cdf(d2))

    return max(0.0, call_price)
