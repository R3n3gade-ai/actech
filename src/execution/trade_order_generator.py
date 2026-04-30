"""
ARMS Execution: Trade Order Generator

Translates target portfolio weights into discrete OrderRequests by 
comparing the Master Engine targets against the live Position objects 
from the broker.

Reference: THB v4.0, Section 10
"""
from typing import Dict, List
import uuid

from execution.order_request import OrderRequest
from execution.interfaces import Position

def generate_rebalance_orders(
    current_positions: List[Position], 
    target_weights: Dict[str, float], 
    nav: float
) -> List[OrderRequest]:
    """
    Compares live broker positions against Master Engine target weights and
    emits the necessary OrderRequests to reconcile the difference.
    """
    orders = []
    
    # 1. Map current positions to their dollar value
    current_values = {}
    for pos in current_positions:
        # Ignore derivative contracts or specific non-equity sleeves
        if pos.sec_type != 'STK':
            continue
        current_values[pos.ticker] = pos.market_value
        
    # 2. Iterate through target weights
    for ticker, target_pct in target_weights.items():
        target_value = target_pct * nav
        current_value = current_values.get(ticker, 0.0)
        
        delta_usd = target_value - current_value
        
        # Deadband threshold (don't execute micro-trades under 0.25% NAV drift)
        deadband_usd = 0.0025 * nav
        
        if abs(delta_usd) > deadband_usd:
            action = 'BUY' if delta_usd > 0 else 'SELL'
            
            # Simplified order generation (assuming fractional shares or dollar-routing)
            # In a full institutional setup, delta_usd is converted to integer shares
            order = OrderRequest(
                ticker=ticker,
                action=action,
                quantity=abs(delta_usd),
                quantity_kind='NOTIONAL_USD',
                order_type='MARKET',
                execution_window_min=30,
                slippage_budget_bps=8.0,
                priority=4,              # Equities priority
                triggering_module='MASTER_ENGINE',
                triggering_signal=f"Rebalance: Target {target_pct:.2%}, Drift ${delta_usd:,.2f}",
                tier=0,                  # Normal rebalances are Tier 0
                confirmation_required=False,
                correlation_id=str(uuid.uuid4())
            )
            orders.append(order)
            
    # 3. Handle positions that are in the book but have target weight 0.0 (Full Exit)
    for ticker, current_value in current_values.items():
        if ticker not in target_weights or target_weights[ticker] == 0.0:
            if current_value > 0:
                order = OrderRequest(
                    ticker=ticker,
                    action='SELL',
                    quantity=current_value,
                    quantity_kind='NOTIONAL_USD',
                    order_type='MARKET',
                    execution_window_min=30,
                    slippage_budget_bps=8.0,
                    priority=2, # Exits take higher priority
                    triggering_module='MASTER_ENGINE',
                    triggering_signal=f"Full Exit: Target weight is 0.0%.",
                    tier=0,
                    confirmation_required=False,
                    correlation_id=str(uuid.uuid4())
                )
                orders.append(order)

    return orders
