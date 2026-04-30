"""
ARMS Data Feeds: Interface Definitions

This file defines the abstract interface that all data feed plugins must implement.
This ensures that the data ingestion pipeline can treat all data sources
identically, whether they are free APIs or expensive institutional feeds.

This is the "receptor" for Frontier F1.

Reference: arms_fsd_master_build_v1.1.md, Section 11.2
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Any, Literal

@dataclass
class SignalRecord:
    """
    A standardized data structure for a single piece of information
    produced by a feed plugin.
    """
    ticker: str  # Ticker symbol, or a special name like 'MACRO' or 'GEOPOL'
    signal_type: str  # A unique identifier for the signal, e.g., 'CPI_MOM', 'EARNINGS_VELOCITY'
    value: float  # The normalized value of the signal (e.g., 0.0 to 1.0) where applicable
    raw_value: Any  # The original value before normalization (e.g., 8.5 for CPI)
    source: str  # The name of the feed plugin, e.g., 'FRED', 'SEC_EDGAR'
    timestamp: str  # ISO 8601 format timestamp
    cost_tier: Literal['FREE', 'LOW', 'INSTITUTIONAL']  # The cost category of the data source

class FeedPlugin(ABC):
    """
    Abstract base class for all data feed plugins.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The unique name of the feed plugin."""
        pass

    @abstractmethod
    def fetch(self) -> List[SignalRecord]:
        """
        The core method of a plugin. It fetches data from its source,
        processes it, and returns a list of standardized SignalRecord objects.
        
        This method should handle its own API calls, error handling, and data
        transformation.
        """
        pass
