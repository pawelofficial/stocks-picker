"""yfinance fallback when POLYGON_API_KEY is not set."""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf
from curl_cffi.requests import Session as CffiSession

from app.services.data_providers.base import MarketDataProvider
from app.services.webshare_proxy import get_next_proxy, is_webshare_configured

log = logging.getLogger(__name__)

_YF_INTERVAL = {"D": "1d", "W": "1wk", "M": "1mo"}
_INITIAL_PERIOD = {"D": "max", "W": "max", "M": "max"}


def _session_with_proxy():
    """Build a curl_cffi Session with WebShare proxy if configured."""
    proxy = get_next_proxy()
    if not proxy:
        return None
    s = CffiSession(impersonate="chrome")
    s.proxies = {"http": proxy, "https": proxy}
    return s


def _ticker(symbol: str):
    """Create yf.Ticker with optional WebShare proxy session."""
    session = _session_with_proxy()
    return yf.Ticker(symbol, session=session) if session else yf.Ticker(symbol)


class YFinanceProvider(MarketDataProvider):
    """yfinance wrapper. Use Polygon instead if you want reliable data."""

    def __init__(self):
        if is_webshare_configured():
            log.info("WebShare rotating proxy enabled for yfinance")

    def fetch_price_history(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        full_history: bool = False,
        period: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        interval = _YF_INTERVAL.get(timeframe, "1d")
        end = end or date.today()
        ticker = _ticker(symbol)
        try:
            if full_history or start is None:
                p = period or _INITIAL_PERIOD.get(timeframe, "10y")
                df = ticker.history(
                    period=p,
                    interval=interval,
                    auto_adjust=True,
                )
            else:
                df = ticker.history(
                    start=start.isoformat(),
                    end=end.isoformat(),
                    interval=interval,
                    auto_adjust=True,
                )
        except Exception as e:
            log.warning("%s/%s: yfinance failed: %s", symbol, timeframe, e)
            return []

        if df is None or df.empty:
            return []

        rows = []
        for ts, row in df.iterrows():
            dt = ts.date() if hasattr(ts, "date") else ts
            rows.append({
                "date": str(dt),
                "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                "high": float(row["High"]) if pd.notna(row["High"]) else None,
                "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
                "close": float(row["Close"]) if pd.notna(row["Close"]) else None,
                "volume": float(row["Volume"]) if pd.notna(row["Volume"]) else None,
            })
        return rows

    def fetch_fundamentals(self, symbol: str) -> Optional[Dict[str, Any]]:
        ticker = _ticker(symbol)
        try:
            info = ticker.info
        except Exception as e:
            log.warning("%s: yfinance fundamentals failed: %s", symbol, e)
            return None
        if not info:
            return None
        # yfinance debtToEquity is % (85 = 0.85), we store as ratio
        de = info.get("debtToEquity")
        if de is not None:
            info = dict(info)
            info["debtToEquity"] = de / 100.0
        return info
