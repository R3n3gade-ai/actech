# src/execution/interfaces.py
# Defines the standard broker and position interfaces for the execution layer.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal, List, Optional

from .order_request import OrderRequest

@dataclass
class Position:
    """
    A standardized representation of a portfolio position.
    """
    ticker: str
    sec_type: str
    quantity: float
    average_cost: float
    market_value: float
    con_id: int
    expiry: Optional[str] = None
    strike: Optional[float] = None
    right: Optional[str] = None
    multiplier: Optional[float] = None

class Broker(ABC):
    """
    An abstract base class that defines the standard interface for any broker.
    Any broker implementation (e.g., for IBKR, Alpaca) must implement these methods.
    """

    @abstractmethod
    def connect(self):
        """Establish a connection to the broker."""
        pass

    @abstractmethod
    def disconnect(self):
        """Terminate the connection to the broker."""
        pass

    @abstractmethod
    def submit_order(self, order: OrderRequest) -> str:
        """
        Submits an order to the broker.
        
        Returns:
            A unique order ID from the broker.
        """
        pass

    @abstractmethod
    def get_positions(self) -> List[Position]:
        """
        Retrieves the current portfolio positions from the broker.
        """
        pass

    @abstractmethod
    def get_nav(self) -> float:
        """
        Retrieves the current Net Asset Value of the account.
        """
        pass

    @abstractmethod
    def get_order_status(self, order_id: str) -> Literal['PENDING', 'FILLED', 'FAILED', 'CANCELLED']:
        """
        Checks the status of a previously submitted order.
        """
        pass
