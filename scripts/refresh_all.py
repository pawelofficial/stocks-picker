#!/usr/bin/env python3
"""Full pipeline: fetch price data + fundamentals → compute indicators.

Usage:
    python scripts/refresh_all.py                      # all sectors
    python scripts/refresh_all.py --sector "Oil & Gas"  # one sector
    python scripts/refresh_all.py --symbol XOM          # one ticker
    python scripts/refresh_all.py --force-indicators    # recompute all indicators from scratch
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import init_db, SessionLocal
from app.models import Sector, Ticker
from app.services.data_fetcher import refresh_all, refresh_sector, refresh_symbol
from app.services.indicators import (
    compute_all,
    compute_all_for_sector,
    compute_all_for_symbol,
)


def main():
    parser = argparse.ArgumentParser(description="Fetch data & compute indicators")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--sector", type=str, help="Sector name to refresh")
    group.add_argument("--symbol", type=str, help="Single ticker symbol to refresh")
    parser.add_argument(
        "--force-indicators",
        action="store_true",
        help="Recompute all indicators from scratch (ignore incremental cache)",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    init_db()
    session = SessionLocal()

    try:
        if args.symbol:
            # Single symbol
            print(f"=== Refreshing {args.symbol} ===")
            stats = refresh_symbol(session, args.symbol)
            print(f"  Price rows: {stats.price_rows_written}, Fundamentals: {stats.fundamentals_updated}")
            if stats.errors:
                print(f"  Errors: {stats.errors}")
            print(f"=== Computing indicators for {args.symbol} ===")
            n = compute_all_for_symbol(session, args.symbol, force=args.force_indicators)
            print(f"  Indicator rows: {n}")

        elif args.sector:
            # Single sector
            sector = session.query(Sector).filter_by(name=args.sector).first()
            if not sector:
                print(f"ERROR: sector '{args.sector}' not found")
                available = [s.name for s in session.query(Sector).all()]
                print(f"Available: {available}")
                sys.exit(1)
            print(f"=== Refreshing sector: {sector.name} (id={sector.id}) ===")
            stats = refresh_sector(session, sector.id)
            print(
                f"  Symbols: {stats.symbols_processed}, "
                f"Price rows: {stats.price_rows_written}, "
                f"Fundamentals: {stats.fundamentals_updated}"
            )
            if stats.errors:
                print(f"  Errors ({len(stats.errors)}):")
                for e in stats.errors:
                    print(f"    - {e}")
            print(f"=== Computing indicators for {sector.name} ===")
            n = compute_all_for_sector(session, sector.id, force=args.force_indicators)
            print(f"  Indicator rows: {n}")

        else:
            # Everything
            print("=== Refreshing ALL sectors ===")
            stats = refresh_all(session)
            print(
                f"  Symbols: {stats.symbols_processed}, "
                f"Price rows: {stats.price_rows_written}, "
                f"Fundamentals: {stats.fundamentals_updated}"
            )
            if stats.errors:
                print(f"  Errors ({len(stats.errors)}):")
                for e in stats.errors[:20]:
                    print(f"    - {e}")
                if len(stats.errors) > 20:
                    print(f"    ... and {len(stats.errors) - 20} more")
            print("=== Computing ALL indicators ===")
            n = compute_all(session, force=args.force_indicators)
            print(f"  Total indicator rows: {n}")

        print("Done.")

    finally:
        session.close()


if __name__ == "__main__":
    main()
