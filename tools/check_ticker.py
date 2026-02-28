#!/usr/bin/env python3
"""Check if a ticker is available on yfinance.

Usage:
    python tools/check_ticker.py XOM
    python tools/check_ticker.py AAPL MSFT
"""

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Tuple

# Allow imports from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.data_providers import get_provider


def check_ticker(symbol: str) -> Tuple[bool, str]:
    """Check if ticker is fetchable. Returns (ok, message)."""
    symbol = symbol.strip().upper()
    if not symbol:
        return False, "Empty symbol"

    provider = get_provider()
    end = date.today()
    start = end - timedelta(days=5)

    # Try price history first
    try:
        rows = provider.fetch_price_history(symbol, "D", start=start, end=end, full_history=False)
        if rows:
            last = rows[-1]
            close = last.get("close")
            if close is not None:
                return True, f"OK – price data available (last close: {close:.2f})"
            return True, "OK – price data available"
    except Exception as e:
        pass

    # Fallback: try fundamentals
    try:
        info = provider.fetch_fundamentals(symbol)
        if info:
            mc = info.get("marketCap")
            if mc is not None:
                return True, f"OK – fundamentals available (market cap: {mc:,.0f})"
            return True, "OK – fundamentals available (minimal)"
    except Exception as e:
        return False, f"Unavailable: {e}"

    return False, "Unavailable – no price or fundamentals data"


def main():
    if len(sys.argv) < 2:
        print("Usage: python tools/check_ticker.py SYMBOL [SYMBOL ...]")
        print("Example: python tools/check_ticker.py XOM AAPL")
        sys.exit(1)

    symbols = sys.argv[1:]
    all_ok = True

    for symbol in symbols:
        ok, msg = check_ticker(symbol)
        status = "✓" if ok else "✗"
        print(f"{status} {symbol}: {msg}")
        if not ok:
            all_ok = False

    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
