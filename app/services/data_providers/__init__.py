"""Market data providers. Uses yfinance."""

import logging

from app.services.data_providers.yfinance_provider import YFinanceProvider

log = logging.getLogger(__name__)

_provider_instance = None


def get_provider():
    """Return the active data provider."""
    global _provider_instance
    if _provider_instance is None:
        _provider_instance = YFinanceProvider()
        log.info("Using yfinance for market data")
    return _provider_instance
