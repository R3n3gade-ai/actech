# src/data_feeds/interfaces.py
# Defines the standard interfaces for all data feed plugins.
# This ensures a "feed-agnostic" architecture, where any data source
# can be plugged in as long as it conforms to these standards.

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Any, Literal

VALID_COST_TIERS = ('FREE', 'LOW', 'INSTITUTIONAL', 'FREE_PROXY', 'FALLBACK')

@dataclass
class SignalRecord:
    """
    The standardized data structure for a single piece of information flowing into ARMS.
    Every feed plugin MUST return a list of these objects.
    
    Based on FSD v1.1, Section 11.2.
    """
    ticker: str  # e.g., 'NVDA', or 'MACRO' for system-wide signals
    signal_type: str  # e.g., 'PRICE_USD', 'VIX_INDEX', 'FED_FUNDS_RATE'
    value: float  # The primary value, normalized to 0-1 where applicable
    raw_value: Any  # The original value before any normalization (e.g., 725.50)
    source: str  # The name of the feed plugin, e.g., 'FRED'
    timestamp: str  # ISO 8601 format string
    cost_tier: Literal['FREE', 'LOW', 'INSTITUTIONAL', 'FREE_PROXY', 'FALLBACK']  # Source provenance / fallback tier


class FeedPlugin(ABC):
    """
    The abstract base class that all data feed plugins must inherit from.
    
    Each plugin is responsible for connecting to a single data source
    (e.g., FRED, SEC EDGAR, a future institutional feed) and transforming
    its data into a list of standard SignalRecord objects.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """The unique name of the feed, e.g., 'FRED'."""
        pass

    @abstractmethod
    def fetch(self) -> List[SignalRecord]:
        """
        Fetches the latest data from the source and returns it as a list
        of standardized SignalRecord objects.
        
        This method will be called by the main data pipeline scheduler.
        """
        pass

