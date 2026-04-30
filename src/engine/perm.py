"""
ARMS Engine: PERM (Covered Call Execution)

Tier 0 — Autonomous Execution (no PM confirmation required).

Executes covered calls against core equity positions when:
  1. Unrealized gain exceeds 30%
  2. Options market is open
  3. Implied volatility exceeds VIX-calibrated threshold

Strike selection: ATM (at-the-money).
No veto window. No confirmation. System executes and logs.

Reference: THB v4.0, Section 10
Reference: Addendum 3, Section 4 — Tier 0 Promotion
"""
import datetime
from dataclasses import dataclass
from typing import Dict, List, Optional
from execution.order_request import OrderRequest
from engine.thesis_retirement import TRPStatus
from reporting.audit_log import SessionLogEntry, append_to_log

# --- Configuration ---

PERM_GAIN_THRESHOLD = 0.30        # 30% unrealized gain required
PERM_VIX_FLOOR = 18.0             # Minimum VIX level for overwrite (IV must be attractive)
PERM_MIN_DTE = 14                 # Minimum days to expiration for the call
PERM_MAX_DTE = 45                 # Maximum days to expiration

@dataclass
class PermStatus:
    active_overwrites: int
    yield_harvested_usd: float

def run_perm_evaluation(
    trp_statuses: List[TRPStatus],
    position_gains: Optional[Dict[str, float]] = None,
    current_vix: float = 0.0,
    options_market_open: bool = True,
) -> List[OrderRequest]:
    """
    Evaluate positions for covered call overwrite.

    Tier 0 autonomous execution per FSD + Addendum 3, Section 4:

    PRIMARY path (30% gain trigger):
    - Unrealized gain exceeds 30% on any equity position
    - Options market is open
    - VIX >= 18.0 (IV attractive enough to harvest premium)
    - ATM strike, no PM confirmation, no veto window

    SECONDARY path (CDF decay):
    - CDF multiplier decays to <= 0.80 (conviction fading)
    - Same VIX and market-open requirements

    Args:
        trp_statuses: TRP statuses for thesis-tracked positions (CDF decay path).
        position_gains: Dict mapping ticker -> unrealized gain as decimal
                        (e.g., 0.35 = 35%). Used for the primary 30% gain trigger.
        current_vix: Current VIX level.
        options_market_open: Whether the options market is accessible.
    """
    orders = []
    now = datetime.datetime.now(datetime.timezone.utc)

    # Gate checks that apply to ALL paths
    if current_vix < PERM_VIX_FLOOR:
        return orders  # IV too low — premium not worth harvesting
    if not options_market_open:
        return orders  # Options market closed

    tickers_with_orders = set()
    retirement_tickers = {trp.ticker for trp in trp_statuses if trp.is_retirement_due}

    # --- PRIMARY PATH: 30% unrealized gain trigger ---
    if position_gains:
        for ticker, gain_pct in position_gains.items():
            if ticker in retirement_tickers:
                continue  # Position flagged for retirement — don't overwrite
            if gain_pct >= PERM_GAIN_THRESHOLD:
                signal = f"PERM Overwrite: unrealized gain {gain_pct:.1%} >= {PERM_GAIN_THRESHOLD:.0%}"
                order = OrderRequest(
                    ticker=ticker,
                    action='SELL_CALL',
                    quantity=1.0,
                    order_type='LIMIT',
                    limit_price=0.0,  # ATM — broker fills at market mid
                    execution_window_min=60,
                    slippage_budget_bps=10.0,
                    priority=3,
                    triggering_module='PERM',
                    triggering_signal=signal,
                    tier=0,
                    confirmation_required=False,
                    veto_window_hours=0.0,
                )
                orders.append(order)
                tickers_with_orders.add(ticker)

                append_to_log(SessionLogEntry(
                    timestamp=now.isoformat(),
                    action_type='PERM_OVERWRITE',
                    triggering_module='PERM',
                    triggering_signal=signal,
                    ticker=ticker,
                ))

    # --- SECONDARY PATH: CDF decay ---
    for trp in trp_statuses:
        if trp.is_retirement_due:
            continue
        if trp.ticker in tickers_with_orders:
            continue  # Already triggered via gain path — don't duplicate

        if trp.cdf_multiplier <= 0.80:
            signal = f"PERM Overwrite: CDF decay ({trp.cdf_multiplier:.2f}x)"
            order = OrderRequest(
                ticker=trp.ticker,
                action='SELL_CALL',
                quantity=1.0,
                order_type='LIMIT',
                limit_price=0.0,
                execution_window_min=60,
                slippage_budget_bps=10.0,
                priority=3,
                triggering_module='PERM',
                triggering_signal=signal,
                tier=0,
                confirmation_required=False,
                veto_window_hours=0.0,
            )
            orders.append(order)

            append_to_log(SessionLogEntry(
                timestamp=now.isoformat(),
                action_type='PERM_OVERWRITE',
                triggering_module='PERM',
                triggering_signal=signal,
                ticker=trp.ticker,
            ))

    return orders
