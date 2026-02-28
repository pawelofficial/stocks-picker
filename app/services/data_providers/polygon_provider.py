"""Polygon.io market data provider. Reliable, no rate-limit hell.

Free tier: 5 req/min, 2 years EOD, fundamentals. Sign up at polygon.io.
Set POLYGON_API_KEY in env to use.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import requests

from app.services.data_providers.base import MarketDataProvider

log = logging.getLogger(__name__)

BASE_URL = "https://api.polygon.io"
# Free tier: 5 req/min — wait 13s between calls to stay safe
DELAY_SECONDS = 13


def _period_to_days(period: str) -> int:
    """Convert yfinance-style period (1y, 2y, max) to days."""
    period = (period or "10y").lower().strip()
    if period == "max":
        return 365 * 30  # ~30 years
    if period.endswith("y"):
        return int(float(period[:-1]) * 365)
    if period.endswith("mo"):
        return int(float(period[:-2]) * 30)
    if period.endswith("d"):
        return int(float(period[:-1]))
    return 365 * 10


class PolygonProvider(MarketDataProvider):
    """Polygon.io REST API. Proper rate limits, no Yahoo nonsense."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._last_request = 0.0

    def _throttle(self):
        elapsed = time.monotonic() - self._last_request
        if elapsed < DELAY_SECONDS:
            time.sleep(DELAY_SECONDS - elapsed)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict:
        url = f"{BASE_URL}{path}"
        p = params or {}
        p["apiKey"] = self.api_key
        self._throttle()
        r = requests.get(url, params=p, timeout=30)
        r.raise_for_status()
        return r.json()

    def fetch_price_history(
        self,
        symbol: str,
        timeframe: str,
        start: Optional[date] = None,
        end: Optional[date] = None,
        full_history: bool = False,
        period: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        timespan_map = {"D": "day", "W": "week", "M": "month"}
        ts = timespan_map.get(timeframe, "day")
        end = end or date.today()
        if full_history or start is None:
            days = _period_to_days(period or "10y")
            start = end - timedelta(days=days)
        start_s = start.isoformat()
        end_s = end.isoformat()
        path = f"/v2/aggs/ticker/{symbol}/range/1/{ts}/{start_s}/{end_s}"
        try:
            data = self._get(path, {"adjusted": "true", "sort": "asc"})
        except requests.RequestException as e:
            log.warning("%s/%s: Polygon request failed: %s", symbol, timeframe, e)
            return []

        results = data.get("results") or []
        rows = []
        for bar in results:
            t_ms = bar.get("t", 0)
            dt = date.fromtimestamp(t_ms / 1000) if t_ms else None
            if not dt:
                continue
            rows.append({
                "date": dt.isoformat(),
                "open": bar.get("o"),
                "high": bar.get("h"),
                "low": bar.get("l"),
                "close": bar.get("c"),
                "volume": bar.get("v"),
            })
        return rows

    def fetch_fundamentals(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Ticker details + latest financials. Some fields may be null on free tier."""
        try:
            # Ticker details: market cap, shares, etc.
            data = self._get(f"/v3/reference/tickers/{symbol}")
            result = data.get("results")
            if not result:
                return None
            r = result
            market_cap = r.get("market_cap")
            if market_cap is None and r.get("share_class_outstanding"):
                # Can derive from close * shares if we had close
                pass

            # Map to our Fundamental model fields
            info = {
                "trailingPE": None,  # Polygon ticker details don't have P/E
                "forwardPE": None,
                "priceToBook": None,
                "debtToEquity": None,
                "currentRatio": None,
                "interestExpense": None,
                "ebitda": None,
                "returnOnEquity": None,
                "marketCap": market_cap,
                "totalRevenue": None,
                "netIncomeToCommon": None,
                "totalDebt": None,
                "totalCash": None,
            }

            # Try Stock Financials API for balance sheet / income (may not be on free tier)
            try:
                fin = self._get("/vX/reference/financials", {"ticker": symbol, "limit": 1})
                fins = fin.get("results")
                if fins:
                    f = fins[0].get("financials", {}) or {}
                    bs = f.get("balance_sheet", {}) or {}
                    inc = f.get("income_statement", {}) or {}
                    comp = f.get("comprehensive_income", {}) or {}
                    info["totalRevenue"] = _num(inc.get("revenues") or inc.get("sales_revenue_net"))
                    info["netIncomeToCommon"] = _num(comp.get("net_income_loss") or inc.get("net_income_loss"))
                    info["totalDebt"] = _num(bs.get("liabilities"))
                    info["totalCash"] = _num(bs.get("cash_and_cash_equivalents_at_carrying_value"))
                    eq = _num(bs.get("equity"))
                    if eq and info["totalDebt"]:
                        info["debtToEquity"] = info["totalDebt"] / eq
                    curr_assets = _num(bs.get("assets_current"))
                    curr_liab = _num(bs.get("liabilities_current"))
                    if curr_liab and curr_liab != 0 and curr_assets is not None:
                        info["currentRatio"] = curr_assets / curr_liab
            except Exception:
                pass

            return info
        except requests.RequestException as e:
            log.warning("%s: Polygon fundamentals failed: %s", symbol, e)
            return None


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None
