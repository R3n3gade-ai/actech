"""
ARMS Execution: OrderRequest Interface

This file defines and "freezes" the OrderRequest dataclass. This is the
standardized interface that every ARMS module uses to request a trade
from the execution layer.

No module should ever generate a trade order in any other format.

Reference: arms_fsd_master_build_v1.1.md, Section 8.3
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

@dataclass(frozen=True)
class OrderRequest:
    """
    A standardized, immutable request for a trade execution.
    
    This object is created by a signal-generating module (like ARAS, PDS, DSHP)
    and consumed by the order book and broker_api modules. The `frozen=True`
    argument makes instances of this class immutable, ensuring that an order
    request cannot be changed after it has been created.
    """
    ticker: str
    action: Literal['BUY', 'SELL', 'BUY_PUT', 'SELL_PUT', 'SELL_CALL']
    quantity: float
    quantity_kind: Literal['SHARES', 'CONTRACTS', 'NOTIONAL_USD', 'WEIGHT_PCT'] = 'SHARES'

    # Optional contract metadata for derivative execution / richer audit semantics
    con_id: Optional[int] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    option_right: Optional[Literal['P', 'C']] = None

    # Order execution parameters, determined by LAEP module
    order_type: Literal['MARKET', 'VWAP', 'LIMIT'] = 'MARKET'
    
    # Optional parameters with defaults (must be at the end)
    execution_window_min: int = 30  # e.g., 30, 60, 90
    slippage_budget_bps: float = 8.0 # e.g., 8, 20, 40
    limit_price: Optional[float] = None

    # Audit and priority information
    priority: int = 4  # 1-4, for kill chain or priority ordering
    triggering_module: str = 'UNKNOWN'
    triggering_signal: str = ''

    # Tier and confirmation information
    tier: Literal[0, 1] = 0
    confirmation_required: bool = False
    veto_window_hours: float = 0.0 # Only relevant for Tier 1
    
    # Unique execution identifier
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
