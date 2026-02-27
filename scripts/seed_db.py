#!/usr/bin/env python3
"""Seed the database with static mapping data from CSVs.

Usage:
    python -m scripts.seed_db          # from repo root
    python scripts/seed_db.py          # also works
"""

import csv
import os
import sys

# Allow running as `python scripts/seed_db.py` from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.database import init_db, SessionLocal
from app.models import Sector, Ticker, SectorCommodity

SEED_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "seed")


def seed():
    init_db()
    db = SessionLocal()

    try:
        # ── Tickers (+ implicit sector creation) ─────────────────────────
        sector_cache: dict[str, Sector] = {}

        with open(os.path.join(SEED_DIR, "tickers.csv"), newline="") as f:
            for row in csv.DictReader(f):
                sector_name = row["sector_name"].strip()

                if sector_name not in sector_cache:
                    sector = db.query(Sector).filter_by(name=sector_name).first()
                    if not sector:
                        sector = Sector(name=sector_name)
                        db.add(sector)
                        db.flush()
                    sector_cache[sector_name] = sector

                symbol = row["symbol"].strip()
                existing = db.query(Ticker).filter_by(symbol=symbol).first()
                if existing:
                    # Update in case name/exchange/sector changed in CSV
                    existing.name = row["name"].strip()
                    existing.exchange = row["exchange"].strip()
                    existing.sector_id = sector_cache[sector_name].id
                else:
                    db.add(Ticker(
                        symbol=symbol,
                        name=row["name"].strip(),
                        exchange=row["exchange"].strip(),
                        sector_id=sector_cache[sector_name].id,
                    ))

        db.flush()

        # ── Commodities ──────────────────────────────────────────────────

        with open(os.path.join(SEED_DIR, "commodities.csv"), newline="") as f:
            for row in csv.DictReader(f):
                sector_name = row["sector_name"].strip()
                sector = sector_cache.get(sector_name)
                if not sector:
                    sector = db.query(Sector).filter_by(name=sector_name).first()
                if not sector:
                    print(f"  WARN: sector '{sector_name}' not found, skipping commodity")
                    continue

                csym = row["commodity_symbol"].strip()
                existing = (
                    db.query(SectorCommodity)
                    .filter_by(sector_id=sector.id, commodity_symbol=csym)
                    .first()
                )
                if existing:
                    existing.commodity_name = row["commodity_name"].strip()
                else:
                    db.add(SectorCommodity(
                        sector_id=sector.id,
                        commodity_symbol=csym,
                        commodity_name=row["commodity_name"].strip(),
                    ))

        db.commit()

        # ── Summary ──────────────────────────────────────────────────────
        n_sectors = db.query(Sector).count()
        n_tickers = db.query(Ticker).count()
        n_commod = db.query(SectorCommodity).count()
        print(f"Seeded: {n_sectors} sectors, {n_tickers} tickers, {n_commod} commodity mappings")

        # Print sector breakdown
        for s in db.query(Sector).order_by(Sector.name).all():
            tickers = db.query(Ticker).filter_by(sector_id=s.id).all()
            comms = db.query(SectorCommodity).filter_by(sector_id=s.id).all()
            syms = ", ".join(t.symbol for t in tickers)
            csyms = ", ".join(c.commodity_symbol for c in comms)
            print(f"  {s.name}: {len(tickers)} tickers [{syms}] | commodities: [{csyms}]")

    finally:
        db.close()


if __name__ == "__main__":
    seed()
