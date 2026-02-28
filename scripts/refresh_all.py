#!/usr/bin/env python3
"""Full pipeline: fetch price data + fundamentals → compute indicators.

Usage:
    python scripts/refresh_all.py                      # all sectors
    python scripts/refresh_all.py --sector "Oil & Gas"  # one sector
    python scripts/refresh_all.py --symbol XOM          # one ticker
    python scripts/refresh_all.py --symbol XOM --period 1y   # 1-year lookback
    python scripts/refresh_all.py --symbol X --full         # wipe & re-download all history
    python scripts/refresh_all.py --force-indicators    # recompute all indicators from scratch, no download
    python scripts/refresh_all.py --clean               # truncate all tables
"""

import argparse
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from app.database import init_db, SessionLocal
from app.logging_config import configure_logging
from app.models import Base, Sector, Ticker
from app.services.data_fetcher import refresh_all, refresh_sector, refresh_symbol
from scripts.seed_db import seed
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
        "--clean",
        action="store_true",
        help="Truncate all tables in the database and exit",
    )
    parser.add_argument(
        "--force-indicators",
        action="store_true",
        help="Recompute all indicators from scratch (ignore incremental cache)",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Wipe existing price data and re-download from scratch (default period: max)",
    )
    parser.add_argument(
        "--period",
        type=str,
        default=None,
        help="Lookback period for full download (e.g. 1y, 2y, 5y, 10y, max). Default: max",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    configure_logging(level=logging.DEBUG if args.verbose else logging.INFO)

    init_db()
    session = SessionLocal()

    try:
        if args.clean:
            print("=== Truncating all tables ===")
            for table in reversed(Base.metadata.sorted_tables):
                session.execute(table.delete())
                print(f"  {table.name}: cleared")
            session.commit()
            print("Done.")
            return

        seed()

        if args.symbol:
            # Single symbol
            print(f"=== Refreshing {args.symbol} ===")
            stats = refresh_symbol(session, args.symbol, period=args.period, full=args.full)
            print(f"  Price rows: {stats.price_rows_written}, Fundamentals: {stats.fundamentals_updated}")
            if stats.errors:
                print(f"  Errors: {stats.errors}")
            print(f"=== Computing indicators for {args.symbol} ===")
            n = compute_all_for_symbol(session, args.symbol, force=args.full or args.force_indicators)
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
            stats = refresh_sector(session, sector.id, period=args.period, full=args.full)
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
            n = compute_all_for_sector(session, sector.id, force=args.full or args.force_indicators)
            print(f"  Indicator rows: {n}")

        else:
            # Everything
            print("=== Refreshing ALL sectors ===")
            stats = refresh_all(session, period=args.period, full=args.full)
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
            n = compute_all(session, force=args.full or args.force_indicators)
            print(f"  Total indicator rows: {n}")

        print("Done.")

    finally:
        session.close()


if __name__ == "__main__":
    main()
