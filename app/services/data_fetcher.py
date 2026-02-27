"""Fetch price history and fundamentals from Yahoo Finance via yfinance.

Implements incremental loading:
- For each (symbol, timeframe), looks up the latest date already in the DB.
- Only fetches rows *after* that date, so re-runs are cheap.
- Fundamentals are re-fetched at most once per day per symbol.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import pandas as pd
import yfinance as yf
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.orm import Session

from app.models import (
    Fundamental,
    PriceHistory,
    Sector,
    SectorCommodity,
    Ticker,
)

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# yfinance interval strings keyed by our timeframe codes
_YF_INTERVAL = {"D": "1d", "W": "1wk", "M": "1mo"}

# How far back to go on the very first (full) download
_INITIAL_PERIOD = {"D": "10y", "W": "10y", "M": "max"}

# Minimum gap before we bother fetching again for a timeframe
_MIN_REFRESH_DAYS = {"D": 0, "W": 5, "M": 25}


# ── Helpers ──────────────────────────────────────────────────────────────────


def _last_stored_date(session: Session, symbol: str, timeframe: str) -> date | None:
    """Return the most recent date we have for (symbol, timeframe), or None."""
    row = (
        session.query(func.max(PriceHistory.date))
        .filter_by(symbol=symbol, timeframe=timeframe)
        .scalar()
    )
    if row is None:
        return None
    return date.fromisoformat(row)


def _upsert_price_rows(session: Session, rows: list[dict]) -> int:
    """Bulk upsert into price_history. Returns number of rows touched."""
    if not rows:
        return 0
    stmt = sqlite_upsert(PriceHistory).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "date", "timeframe"],
        set_={
            "open": stmt.excluded.open,
            "high": stmt.excluded.high,
            "low": stmt.excluded.low,
            "close": stmt.excluded.close,
            "volume": stmt.excluded.volume,
        },
    )
    session.execute(stmt)
    return len(rows)


def _last_fundamental_date(session: Session, symbol: str) -> date | None:
    row = (
        session.query(func.max(Fundamental.report_date))
        .filter_by(symbol=symbol)
        .scalar()
    )
    if row is None:
        return None
    return date.fromisoformat(row)


# ── Core fetch functions ─────────────────────────────────────────────────────


def fetch_price_history(
    session: Session,
    symbol: str,
    timeframe: str = "D",
) -> int:
    """Fetch OHLCV for *symbol* at the given *timeframe* ('D', 'W', 'M').

    Incremental: only downloads data after the last stored date.
    Returns the number of new/updated rows written.
    """
    last = _last_stored_date(session, symbol, timeframe)

    if last is not None:
        gap = (date.today() - last).days
        if gap <= _MIN_REFRESH_DAYS[timeframe]:
            log.debug("%s/%s: up to date (last=%s, gap=%dd)", symbol, timeframe, last, gap)
            return 0
        # Fetch from the day after last stored date
        start = (last + timedelta(days=1)).isoformat()
        log.info("%s/%s: incremental from %s", symbol, timeframe, start)
        df = yf.Ticker(symbol).history(
            start=start,
            interval=_YF_INTERVAL[timeframe],
            auto_adjust=True,
        )
    else:
        # First-time full download
        log.info("%s/%s: full download (%s)", symbol, timeframe, _INITIAL_PERIOD[timeframe])
        df = yf.Ticker(symbol).history(
            period=_INITIAL_PERIOD[timeframe],
            interval=_YF_INTERVAL[timeframe],
            auto_adjust=True,
        )

    if df is None or df.empty:
        log.warning("%s/%s: no data returned", symbol, timeframe)
        return 0

    rows = []
    for ts, row in df.iterrows():
        dt = ts.date() if hasattr(ts, "date") else ts
        rows.append(
            {
                "symbol": symbol,
                "date": str(dt),
                "timeframe": timeframe,
                "open": float(row["Open"]) if pd.notna(row["Open"]) else None,
                "high": float(row["High"]) if pd.notna(row["High"]) else None,
                "low": float(row["Low"]) if pd.notna(row["Low"]) else None,
                "close": float(row["Close"]) if pd.notna(row["Close"]) else None,
                "volume": float(row["Volume"]) if pd.notna(row["Volume"]) else None,
            }
        )

    n = _upsert_price_rows(session, rows)
    session.commit()
    log.info("%s/%s: upserted %d rows", symbol, timeframe, n)
    return n


def fetch_fundamentals(session: Session, symbol: str) -> bool:
    """Fetch balance-sheet / valuation snapshot for *symbol*.

    Skips if we already have a snapshot from today.
    Returns True if new data was written.
    """
    today = date.today()
    last = _last_fundamental_date(session, symbol)
    if last is not None and last >= today:
        log.debug("%s: fundamentals up to date (last=%s)", symbol, last)
        return False

    log.info("%s: fetching fundamentals", symbol)
    try:
        info = yf.Ticker(symbol).info
    except Exception:
        log.exception("%s: failed to fetch info", symbol)
        return False

    if not info:
        log.warning("%s: empty info dict", symbol)
        return False

    # Compute interest coverage from EBITDA / interestExpense if available
    ebitda = info.get("ebitda")
    interest = info.get("interestExpense")
    if ebitda and interest and interest != 0:
        interest_coverage = abs(ebitda / interest)
    else:
        interest_coverage = None

    stmt = sqlite_upsert(Fundamental).values(
        symbol=symbol,
        report_date=today.isoformat(),
        pe_ratio=info.get("trailingPE"),
        forward_pe=info.get("forwardPE"),
        price_to_book=info.get("priceToBook"),
        debt_to_equity=_safe_div100(info.get("debtToEquity")),  # yfinance returns D/E as %
        current_ratio=info.get("currentRatio"),
        interest_coverage=interest_coverage,
        roe=info.get("returnOnEquity"),
        market_cap=info.get("marketCap"),
        revenue_ttm=info.get("totalRevenue"),
        net_income_ttm=info.get("netIncomeToCommon"),
        total_debt=info.get("totalDebt"),
        cash=info.get("totalCash"),
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "report_date"],
        set_={
            "pe_ratio": stmt.excluded.pe_ratio,
            "forward_pe": stmt.excluded.forward_pe,
            "price_to_book": stmt.excluded.price_to_book,
            "debt_to_equity": stmt.excluded.debt_to_equity,
            "current_ratio": stmt.excluded.current_ratio,
            "interest_coverage": stmt.excluded.interest_coverage,
            "roe": stmt.excluded.roe,
            "market_cap": stmt.excluded.market_cap,
            "revenue_ttm": stmt.excluded.revenue_ttm,
            "net_income_ttm": stmt.excluded.net_income_ttm,
            "total_debt": stmt.excluded.total_debt,
            "cash": stmt.excluded.cash,
        },
    )
    session.execute(stmt)
    session.commit()
    log.info("%s: fundamentals saved", symbol)
    return True


def _safe_div100(val: float | None) -> float | None:
    """yfinance returns debtToEquity as a percentage (e.g. 85.0 for 0.85)."""
    if val is None:
        return None
    return val / 100.0


# ── High-level orchestrators ─────────────────────────────────────────────────


@dataclass
class RefreshStats:
    symbols_processed: int = 0
    price_rows_written: int = 0
    fundamentals_updated: int = 0
    errors: list[str] | None = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []


def refresh_symbol(session: Session, symbol: str) -> RefreshStats:
    """Fetch all timeframes + fundamentals for a single symbol."""
    stats = RefreshStats()
    stats.symbols_processed = 1

    for tf in ("D", "W", "M"):
        try:
            n = fetch_price_history(session, symbol, tf)
            stats.price_rows_written += n
        except Exception as e:
            msg = f"{symbol}/{tf}: {e}"
            log.exception(msg)
            stats.errors.append(msg)

    try:
        if fetch_fundamentals(session, symbol):
            stats.fundamentals_updated += 1
    except Exception as e:
        msg = f"{symbol}/fundamentals: {e}"
        log.exception(msg)
        stats.errors.append(msg)

    return stats


def refresh_sector(session: Session, sector_id: int) -> RefreshStats:
    """Fetch all tickers + commodities for a sector. Incremental."""
    total = RefreshStats()

    # Stock tickers
    tickers = session.query(Ticker).filter_by(sector_id=sector_id).all()
    symbols = [t.symbol for t in tickers]

    # Commodity tickers for this sector
    commodities = (
        session.query(SectorCommodity.commodity_symbol)
        .filter_by(sector_id=sector_id)
        .all()
    )
    commodity_symbols = [c[0] for c in commodities]

    all_symbols = symbols + commodity_symbols
    log.info(
        "Refreshing sector %d: %d stock(s) + %d commodity(ies)",
        sector_id,
        len(symbols),
        len(commodity_symbols),
    )

    for sym in all_symbols:
        s = refresh_symbol(session, sym)
        total.symbols_processed += s.symbols_processed
        total.price_rows_written += s.price_rows_written
        total.fundamentals_updated += s.fundamentals_updated
        total.errors.extend(s.errors)

    log.info(
        "Sector %d done: %d symbols, %d price rows, %d fundamentals, %d errors",
        sector_id,
        total.symbols_processed,
        total.price_rows_written,
        total.fundamentals_updated,
        len(total.errors),
    )
    return total


def refresh_all(session: Session) -> RefreshStats:
    """Refresh every sector in the DB."""
    total = RefreshStats()
    sectors = session.query(Sector).all()
    for sector in sectors:
        s = refresh_sector(session, sector.id)
        total.symbols_processed += s.symbols_processed
        total.price_rows_written += s.price_rows_written
        total.fundamentals_updated += s.fundamentals_updated
        total.errors.extend(s.errors)
    return total
