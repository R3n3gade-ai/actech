"""
ARMS Execution: Order Book & Liquidity-Adjusted Execution Protocol (LAEP)

This is the L5 execution core. It receives OrderRequests from the engine,
evaluates the current VIX via the LAEP engine module (5-tier system),
and translates the requests into actionable broker instructions with
enforced execution windows, slippage budgets, and priority ordering.

Reference: Auto Deployment v1.1 §7, THB v4.0 Section 5
"""
from dataclasses import dataclass, replace
from typing import List, Literal
from enum import Enum

from .order_request import OrderRequest
from engine.laep import classify_vix_tier, resolve_execution_params, VIXTier
from reporting.audit_log import SessionLogEntry, append_to_log
import datetime

class LiquidityMode(Enum):
    """Maps from LAEP VIXTier to display-friendly mode names."""
    NORMAL = "NORMAL"     # VIX < 16  (Tier 1)
    ELEVATED = "ELEVATED" # VIX 16-22 (Tier 2)
    HIGH = "HIGH"         # VIX 22-28 (Tier 3)
    STRESS = "STRESS"     # VIX 28-40 (Tier 4)
    CRISIS = "CRISIS"     # VIX > 40  (Tier 5)

_TIER_TO_MODE = {
    VIXTier.NORMAL:   LiquidityMode.NORMAL,
    VIXTier.ELEVATED: LiquidityMode.ELEVATED,
    VIXTier.HIGH:     LiquidityMode.HIGH,
    VIXTier.STRESS:   LiquidityMode.STRESS,
    VIXTier.CRISIS:   LiquidityMode.CRISIS,
}

@dataclass
class OrderBookEntry:
    """
    Extended representation of an order ready for broker submission.
    Incorporates LAEP dynamic constraints.
    """
    request: OrderRequest
    liquidity_mode: LiquidityMode
    order_type: Literal['MARKET', 'VWAP', 'LIMIT']
    execution_window_min: int
    slippage_budget_bps: float
    priority: int

class OrderBook:
    """
    Manages the translation and staging of OrderRequests before they hit the broker.
    """
    def __init__(self):
        self.queue: List[OrderBookEntry] = []

    def _determine_liquidity_mode(self, current_vix: float) -> LiquidityMode:
        tier = classify_vix_tier(current_vix)
        return _TIER_TO_MODE[tier]

    def process_request(self, request: OrderRequest, current_vix: float,
                        is_crash_protocol: bool = False) -> OrderBookEntry:
        """
        Applies LAEP rules to a raw OrderRequest based on current VIX tier.
        Delegates to engine.laep for 5-tier execution parameter resolution.
        """
        # Resolve execution parameters via canonical LAEP engine
        laep_params = resolve_execution_params(
            vix=current_vix,
            requested_order_type=request.order_type,
            triggering_module=request.triggering_module,
            action=request.action,
            is_crash_protocol=is_crash_protocol,
        )

        mode = _TIER_TO_MODE[laep_params.vix_tier]
        order_type = laep_params.order_type
        window = laep_params.execution_window_min
        slippage = laep_params.slippage_budget_bps
        priority = request.priority

        # If LAEP says execution halted (CRISIS + non-CRASH), log and skip
        if laep_params.execution_halted:
            append_to_log(SessionLogEntry(
                timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                action_type='LAEP_HALTED',
                triggering_module='ORDER_BOOK',
                triggering_signal=f"LAEP CRISIS: Execution halted for {request.action} {request.ticker} (non-CRASH order in VIX>{current_vix:.1f})",
                correlation_id=request.correlation_id,
                ticker=request.ticker
            ))
            # Still create entry but mark it — won't be submitted to broker
            entry = OrderBookEntry(
                request=request,
                liquidity_mode=mode,
                order_type=order_type,
                execution_window_min=window,
                slippage_budget_bps=slippage,
                priority=99  # Will sort last and be skipped
            )
            return entry

        # Crisis Priority Reorder: Illiquid crypto exits first
        if mode == LiquidityMode.CRISIS and request.action == 'SELL':
            if request.ticker == 'BSOL': priority = 1
            elif request.ticker == 'ETHB': priority = 2
            elif request.ticker == 'IBIT': priority = 3
            else: priority = 4

        # Create a new updated request because OrderRequest is frozen
        updated_request = replace(
            request, 
            order_type=order_type, 
            execution_window_min=window, 
            slippage_budget_bps=slippage, 
            priority=priority
        )

        entry = OrderBookEntry(
            request=updated_request,
            liquidity_mode=mode,
            order_type=order_type,
            execution_window_min=window,
            slippage_budget_bps=slippage,
            priority=priority
        )
        
        self.queue.append(entry)
        
        append_to_log(SessionLogEntry(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            action_type='LAEP_EVALUATION',
            triggering_module='ORDER_BOOK',
            triggering_signal=f"LAEP {mode.value}: Translated {request.action} {request.ticker} to {order_type} (Win: {window}m, Slip: {slippage}bps, Pri: {priority})",
            correlation_id=request.correlation_id,
            ticker=request.ticker
        ))
        
        return entry

    def get_executable_batch(self) -> List[OrderBookEntry]:
        """
        Returns the sorted queue ready for the broker, respecting priority.
        Priority 1 executes before Priority 4.
        """
        # Sort by priority (lower number = higher priority)
        batch = sorted(self.queue, key=lambda x: x.priority)
        self.queue = [] # Clear queue after handing off to broker
        return batch
