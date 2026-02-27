"""Compute technical indicators from stored price history.

All maths is done in pure pandas — no external TA library required.

Indicators computed per (symbol, timeframe):
    RSI(14)             — Wilder's smoothed Relative Strength Index
    SMA(20, 50, 200)    — Simple Moving Averages
    Bollinger Bands(20, 2) — Upper, Middle (SMA-20), Lower
    %B                  — position within the bands
"""

from __future__ import annotations

import logging

import pandas as pd
from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert as sqlite_upsert
from sqlalchemy.orm import Session

from app.models import (
    PriceHistory,
    Sector,
    SectorCommodity,
    TechnicalIndicator,
    Ticker,
)

log = logging.getLogger(__name__)

# ── Pure pandas indicator functions ──────────────────────────────────────────


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. Returns Series of same length (NaN for warmup)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilder's exponential moving average (equivalent to EMA with alpha=1/period)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def bollinger_bands(
    series: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Return (upper, middle, lower, pct_b)."""
    middle = sma(series, period)
    std = series.rolling(window=period, min_periods=period).std()
    upper = middle + num_std * std
    lower = middle - num_std * std
    width = upper - lower
    # %B: 0 at lower band, 1 at upper band, <0 below, >1 above
    pct_b = (series - lower) / width.replace(0, float("nan"))
    return upper, middle, lower, pct_b


# ── DB-aware computation ─────────────────────────────────────────────────────


def _load_closes(session: Session, symbol: str, timeframe: str) -> pd.DataFrame:
    """Load OHLCV from price_history into a DatetimeIndex DataFrame."""
    rows = (
        session.query(
            PriceHistory.date,
            PriceHistory.open,
            PriceHistory.high,
            PriceHistory.low,
            PriceHistory.close,
            PriceHistory.volume,
        )
        .filter_by(symbol=symbol, timeframe=timeframe)
        .order_by(PriceHistory.date)
        .all()
    )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    df.set_index("date", inplace=True)
    return df


def _last_indicator_date(session: Session, symbol: str, timeframe: str) -> str | None:
    return (
        session.query(func.max(TechnicalIndicator.date))
        .filter_by(symbol=symbol, timeframe=timeframe)
        .scalar()
    )


def compute_indicators(
    session: Session,
    symbol: str,
    timeframe: str,
    force: bool = False,
) -> int:
    """Compute and upsert indicators for (symbol, timeframe).

    Incremental: only writes rows whose date > last stored indicator date,
    unless *force=True*.

    We always recompute the full series (indicators need lookback) but only
    upsert the tail that's actually new.

    Returns number of rows upserted.
    """
    df = _load_closes(session, symbol, timeframe)
    if df.empty:
        log.debug("%s/%s: no price data, skipping", symbol, timeframe)
        return 0

    # Compute full series
    close = df["close"]
    rsi_14 = rsi(close, 14)
    sma_20 = sma(close, 20)
    sma_50 = sma(close, 50)
    sma_200 = sma(close, 200)
    bb_upper, bb_middle, bb_lower, bb_pct = bollinger_bands(close, 20, 2.0)

    # Determine which rows are new
    if not force:
        last = _last_indicator_date(session, symbol, timeframe)
    else:
        last = None

    rows = []
    for i, (dt, _) in enumerate(df.iterrows()):
        dt_str = dt.strftime("%Y-%m-%d")
        if last is not None and dt_str <= last:
            continue
        # Skip rows where everything is NaN (warmup period)
        if pd.isna(rsi_14.iloc[i]) and pd.isna(sma_20.iloc[i]):
            continue
        rows.append(
            {
                "symbol": symbol,
                "date": dt_str,
                "timeframe": timeframe,
                "rsi_14": _f(rsi_14.iloc[i]),
                "sma_20": _f(sma_20.iloc[i]),
                "sma_50": _f(sma_50.iloc[i]),
                "sma_200": _f(sma_200.iloc[i]),
                "bb_upper": _f(bb_upper.iloc[i]),
                "bb_middle": _f(bb_middle.iloc[i]),
                "bb_lower": _f(bb_lower.iloc[i]),
                "bb_pct": _f(bb_pct.iloc[i]),
            }
        )

    if not rows:
        log.debug("%s/%s: indicators up to date", symbol, timeframe)
        return 0

    stmt = sqlite_upsert(TechnicalIndicator).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["symbol", "date", "timeframe"],
        set_={
            "rsi_14": stmt.excluded.rsi_14,
            "sma_20": stmt.excluded.sma_20,
            "sma_50": stmt.excluded.sma_50,
            "sma_200": stmt.excluded.sma_200,
            "bb_upper": stmt.excluded.bb_upper,
            "bb_middle": stmt.excluded.bb_middle,
            "bb_lower": stmt.excluded.bb_lower,
            "bb_pct": stmt.excluded.bb_pct,
        },
    )
    session.execute(stmt)
    session.commit()
    log.info("%s/%s: upserted %d indicator rows", symbol, timeframe, len(rows))
    return len(rows)


def _f(val) -> float | None:
    """Convert numpy/pandas float to Python float, NaN → None."""
    if pd.isna(val):
        return None
    return round(float(val), 6)


# ── High-level orchestrators ─────────────────────────────────────────────────


def compute_all_for_symbol(session: Session, symbol: str, force: bool = False) -> int:
    """Compute D/W/M indicators for a single symbol."""
    total = 0
    for tf in ("D", "W", "M"):
        total += compute_indicators(session, symbol, tf, force=force)
    return total


def compute_all_for_sector(session: Session, sector_id: int, force: bool = False) -> int:
    """Compute indicators for every ticker + commodity in a sector."""
    tickers = session.query(Ticker).filter_by(sector_id=sector_id).all()
    commodities = (
        session.query(SectorCommodity.commodity_symbol)
        .filter_by(sector_id=sector_id)
        .all()
    )
    symbols = [t.symbol for t in tickers] + [c[0] for c in commodities]
    total = 0
    for sym in symbols:
        n = compute_all_for_symbol(session, sym, force=force)
        total += n
        if n:
            log.info("%s: %d indicator rows", sym, n)
    return total


def compute_all(session: Session, force: bool = False) -> int:
    """Compute indicators for every sector."""
    total = 0
    for sector in session.query(Sector).all():
        total += compute_all_for_sector(session, sector.id, force=force)
    return total
