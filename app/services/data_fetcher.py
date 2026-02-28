"""Fetch price history and fundamentals via yfinance."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, timedelta

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
from app.services.data_providers import get_provider

log = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# How far back to go on the very first (full) download
_INITIAL_PERIOD = {"D": "max", "W": "max", "M": "max"}

# Minimum gap before we bother fetching again for a timeframe
_MIN_REFRESH_DAYS = {"D": 0, "W": 5, "M": 25}

# Delay between requests (seconds) — for yfinance; Polygon has its own throttle
_DELAY_BETWEEN_REQUESTS = 4
_RETRY_DELAY_NORMAL = (3, 5, 7)
_RETRY_DELAY_429 = (60, 120, 180)


def _is_rate_limited(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "429" in msg or "too many requests" in msg


def _retry_delay(exc: Exception, attempt: int) -> int:
    if _is_rate_limited(exc):
        return _RETRY_DELAY_429[min(attempt, len(_RETRY_DELAY_429) - 1)]
    return _RETRY_DELAY_NORMAL[min(attempt, len(_RETRY_DELAY_NORMAL) - 1)]


def _provider():
    return get_provider()


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
    # SQLite limits bind params (~999). Batch to avoid "too many SQL variables"
    BATCH_SIZE = 100  # 100 rows * 8 cols = 800 < 999
    for i in range(0, len(rows), BATCH_SIZE):
        batch = rows[i : i + BATCH_SIZE]
        stmt = sqlite_upsert(PriceHistory).values(batch)
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


def _delete_price_history(session: Session, symbol: str, timeframe: str) -> None:
    """Delete all stored price rows for (symbol, timeframe)."""
    session.query(PriceHistory).filter_by(symbol=symbol, timeframe=timeframe).delete()
    session.commit()


def fetch_price_history(
    session: Session,
    symbol: str,
    timeframe: str = "D",
    period: str | None = None,
    full: bool = False,
) -> int:
    """Fetch OHLCV for *symbol* at the given *timeframe* ('D', 'W', 'M').

    Incremental: only downloads data after the last stored date.
    If *full* is True, wipes existing data and re-downloads everything.
    Returns the number of new/updated rows written.
    """
    if full:
        _delete_price_history(session, symbol, timeframe)

    last = _last_stored_date(session, symbol, timeframe)

    if last is not None:
        gap = (date.today() - last).days
        if gap <= _MIN_REFRESH_DAYS[timeframe]:
            log.debug("%s/%s: up to date (last=%s, gap=%dd)", symbol, timeframe, last, gap)
            return 0
        start = last + timedelta(days=1)
        end = date.today()
        log.info("%s/%s: incremental from %s", symbol, timeframe, start.isoformat())
        full_history = False
    else:
        period_used = period or _INITIAL_PERIOD.get(timeframe, "max")
        log.info("%s/%s: full download (%s)", symbol, timeframe, period_used)
        start = None
        end = date.today()
        full_history = True

    provider = _provider()
    period_arg = (period or _INITIAL_PERIOD.get(timeframe, "max")) if full_history else None
    rows_raw = []
    for attempt in range(3):
        try:
            rows_raw = provider.fetch_price_history(
                symbol,
                timeframe,
                start=start,
                end=end,
                full_history=full_history,
                period=period_arg,
            )
            break
        except Exception as e:
            if attempt < 2:
                delay = _retry_delay(e, attempt)
                rate_msg = " (rate limited)" if _is_rate_limited(e) else ""
                log.warning("%s/%s: fetch failed (attempt %d/3)%s, retrying in %ds: %s", symbol, timeframe, attempt + 1, rate_msg, delay, e)
                time.sleep(delay)
            else:
                log.exception("%s/%s: failed after 3 attempts", symbol, timeframe)
                return 0

    if not rows_raw:
        log.warning("%s/%s: no data returned", symbol, timeframe)
        return 0

    rows = [
        {
            "symbol": symbol,
            "date": r["date"],
            "timeframe": timeframe,
            "open": r.get("open"),
            "high": r.get("high"),
            "low": r.get("low"),
            "close": r.get("close"),
            "volume": r.get("volume"),
        }
        for r in rows_raw
    ]

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
    provider = _provider()
    info = None
    for attempt in range(3):
        try:
            info = provider.fetch_fundamentals(symbol)
            break
        except Exception as e:
            if attempt < 2:
                delay = _retry_delay(e, attempt)
                rate_msg = " (rate limited)" if _is_rate_limited(e) else ""
                log.warning("%s: fetch failed (attempt %d/3)%s, retrying in %ds: %s", symbol, attempt + 1, rate_msg, delay, e)
                time.sleep(delay)
            else:
                log.exception("%s: failed to fetch info after 3 attempts", symbol)
                return False

    if not info:
        log.warning("%s: empty info dict", symbol)
        return False

    # debtToEquity: both providers return ratio (yfinance converts % in provider)
    debt_to_equity = info.get("debtToEquity")

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
        debt_to_equity=debt_to_equity,
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


def refresh_symbol(session: Session, symbol: str, period: str | None = None, full: bool = False) -> RefreshStats:
    """Fetch all timeframes + fundamentals for a single symbol."""
    stats = RefreshStats()
    stats.symbols_processed = 1

    for tf in ("D", "W", "M"):
        try:
            n = fetch_price_history(session, symbol, tf, period=period, full=full)
            stats.price_rows_written += n
        except Exception as e:
            msg = f"{symbol}/{tf}: {e}"
            log.exception(msg)
            stats.errors.append(msg)
        time.sleep(_DELAY_BETWEEN_REQUESTS)

    try:
        if fetch_fundamentals(session, symbol):
            stats.fundamentals_updated += 1
    except Exception as e:
        msg = f"{symbol}/fundamentals: {e}"
        log.exception(msg)
        stats.errors.append(msg)

    return stats


def refresh_sector(session: Session, sector_id: int, period: str | None = None, full: bool = False) -> RefreshStats:
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
        s = refresh_symbol(session, sym, period=period, full=full)
        total.symbols_processed += s.symbols_processed
        total.price_rows_written += s.price_rows_written
        total.fundamentals_updated += s.fundamentals_updated
        total.errors.extend(s.errors)
        time.sleep(_DELAY_BETWEEN_REQUESTS)

    log.info(
        "Sector %d done: %d symbols, %d price rows, %d fundamentals, %d errors",
        sector_id,
        total.symbols_processed,
        total.price_rows_written,
        total.fundamentals_updated,
        len(total.errors),
    )
    return total


def refresh_all(session: Session, period: str | None = None, full: bool = False) -> RefreshStats:
    """Refresh every sector in the DB."""
    total = RefreshStats()
    sectors = session.query(Sector).all()
    for sector in sectors:
        s = refresh_sector(session, sector.id, period=period, full=full)
        total.symbols_processed += s.symbols_processed
        total.price_rows_written += s.price_rows_written
        total.fundamentals_updated += s.fundamentals_updated
        total.errors.extend(s.errors)
    return total
