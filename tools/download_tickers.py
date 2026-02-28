#!/usr/bin/env python3
"""Download all available US stock tickers from NASDAQ symbol directory.

Fetches nasdaqtraded.txt (all US equities traded on NASDAQ, NYSE, etc.)
and outputs a CSV compatible with data/seed/tickers.csv format.

Usage:
    python tools/download_tickers.py                    # all tickers
    python tools/download_tickers.py -o data/seed/all_tickers.csv
    python tools/download_tickers.py --stocks-only      # exclude ETFs
    python tools/download_tickers.py --exchange NYSE    # filter by exchange
"""

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional
from urllib.request import urlopen

# Allow imports from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# NASDAQ symbol directory URL (updated daily)
NASDAQ_TRADED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqtraded.txt"

# Listing exchange codes -> display name
EXCHANGE_MAP = {
    "N": "NYSE",
    "Q": "NASDAQ",
    "P": "NYSE ARCA",
    "Z": "BATS",
    "A": "NYSE MKT",
}


def _clean_name(raw: str) -> str:
    """Strip common suffixes from security names."""
    # Remove trailing " - Common Stock", " Common Stock", etc.
    raw = re.sub(r"\s*[-–]\s*Common Stock\.?$", "", raw, flags=re.I)
    raw = re.sub(r"\s+Common Stock\.?$", "", raw, flags=re.I)
    raw = re.sub(r"\s*[-–]\s*Units?$", "", raw, flags=re.I)
    raw = re.sub(r"\s*[-–]\s*Class [AB] .*$", "", raw, flags=re.I)
    return raw.strip() or "Unknown"


def fetch_nasdaq_traded() -> str:
    """Download nasdaqtraded.txt content."""
    with urlopen(NASDAQ_TRADED_URL, timeout=30) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_nasdaq_traded(
    content: str,
    stocks_only: bool = False,
    exchange_filter: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Parse nasdaqtraded.txt into list of {symbol, name, exchange, sector_name}."""
    lines = content.strip().splitlines()
    if not lines:
        return []

    header = lines[0]
    if "|" not in header:
        raise ValueError("Unexpected file format")

    # Find column indices
    parts = [p.strip() for p in header.split("|")]
    try:
        idx_symbol = parts.index("Symbol")
        idx_name = parts.index("Security Name")
        idx_exchange = parts.index("Listing Exchange")
        idx_etf = parts.index("ETF")
        idx_test = parts.index("Test Issue")
    except ValueError as e:
        raise ValueError(f"Missing expected column: {e}") from e

    rows = []
    for line in lines[1:]:
        if line.startswith("File Creation Time"):
            break
        fields = [f.strip() for f in line.split("|")]
        if len(fields) <= max(idx_symbol, idx_name, idx_exchange, idx_etf, idx_test):
            continue

        symbol = fields[idx_symbol].strip()
        if not symbol:
            continue

        # Skip test issues
        test_issue = fields[idx_test].upper() if idx_test < len(fields) else "N"
        if test_issue == "Y":
            continue

        # Optionally skip ETFs
        if stocks_only:
            etf = fields[idx_etf].upper() if idx_etf < len(fields) else "N"
            if etf == "Y":
                continue

        # Map exchange code
        ex_code = fields[idx_exchange].strip() if idx_exchange < len(fields) else ""
        exchange = EXCHANGE_MAP.get(ex_code, ex_code or "NASDAQ")

        if exchange_filter and exchange.upper() != exchange_filter.upper():
            continue

        name = _clean_name(fields[idx_name]) if idx_name < len(fields) else symbol

        rows.append({
            "symbol": symbol,
            "name": name,
            "exchange": exchange,
            "sector_name": "Uncategorized",
        })

    return rows


def main():
    parser = argparse.ArgumentParser(
        description="Download all US stock tickers from NASDAQ symbol directory"
    )
    parser.add_argument(
        "-o", "--output",
        type=Path,
        default=Path("data/seed/all_tickers.csv"),
        help="Output CSV path (default: data/seed/all_tickers.csv)",
    )
    parser.add_argument(
        "--stocks-only",
        action="store_true",
        help="Exclude ETFs",
    )
    parser.add_argument(
        "--exchange",
        type=str,
        choices=["NYSE", "NASDAQ", "BATS", "NYSE ARCA", "NYSE MKT"],
        help="Only include tickers from this exchange",
    )
    args = parser.parse_args()

    print("Downloading nasdaqtraded.txt...")
    try:
        content = fetch_nasdaq_traded()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print("Parsing...")
    rows = parse_nasdaq_traded(
        content,
        stocks_only=args.stocks_only,
        exchange_filter=args.exchange,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "name", "exchange", "sector_name"])
        w.writeheader()
        w.writerows(rows)

    print(f"Wrote {len(rows)} tickers to {args.output}")
    print("Tip: Review and assign sectors, then run: python scripts/seed_db.py")
    print("     (Or merge with data/seed/tickers.csv and re-seed)")


if __name__ == "__main__":
    main()
