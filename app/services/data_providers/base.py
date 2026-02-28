"""Base interface for market data providers."""

from abc import ABC, abstractmethod
from datetime import date
from typing import Any, Dict, List, Optional


class MarketDataProvider(ABC):
    """Abstract base for fetching price history and fundamentals."""

    @abstractmethod
    def fetch_price_history(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        full_history: bool = False,
        period: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Fetch OHLCV rows. Returns list of {date, open, high, low, close, volume}."""
        pass

    @abstractmethod
    def fetch_fundamentals(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Fetch fundamentals. Returns dict with keys matching Fundamental model fields."""
        pass
