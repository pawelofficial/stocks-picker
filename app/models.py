from sqlalchemy import Column, Integer, Text, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ── Static / Reference ───────────────────────────────────────────────────────


class Sector(Base):
    __tablename__ = "sectors"

    id = Column(Integer, primary_key=True)
    name = Column(Text, nullable=False, unique=True)
    description = Column(Text)

    tickers = relationship("Ticker", back_populates="sector")
    commodities = relationship("SectorCommodity", back_populates="sector")


class Ticker(Base):
    __tablename__ = "tickers"

    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False, unique=True)
    name = Column(Text, nullable=False)
    exchange = Column(Text)
    sector_id = Column(Integer, ForeignKey("sectors.id"))

    sector = relationship("Sector", back_populates="tickers")


class SectorCommodity(Base):
    __tablename__ = "sector_commodities"

    id = Column(Integer, primary_key=True)
    sector_id = Column(Integer, ForeignKey("sectors.id"))
    commodity_symbol = Column(Text, nullable=False)
    commodity_name = Column(Text, nullable=False)

    sector = relationship("Sector", back_populates="commodities")

    __table_args__ = (
        UniqueConstraint("sector_id", "commodity_symbol"),
    )


# ── Time Series ──────────────────────────────────────────────────────────────


class PriceHistory(Base):
    __tablename__ = "price_history"

    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False)
    date = Column(Text, nullable=False)       # ISO "YYYY-MM-DD"
    timeframe = Column(Text, nullable=False)   # 'D' | 'W' | 'M'
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)

    __table_args__ = (
        UniqueConstraint("symbol", "date", "timeframe"),
    )


# ── Fundamentals ─────────────────────────────────────────────────────────────


class Fundamental(Base):
    __tablename__ = "fundamentals"

    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False)
    report_date = Column(Text, nullable=False)
    pe_ratio = Column(Float)
    forward_pe = Column(Float)
    price_to_book = Column(Float)
    debt_to_equity = Column(Float)
    current_ratio = Column(Float)
    interest_coverage = Column(Float)
    roe = Column(Float)
    market_cap = Column(Float)
    revenue_ttm = Column(Float)
    net_income_ttm = Column(Float)
    total_debt = Column(Float)
    cash = Column(Float)

    __table_args__ = (
        UniqueConstraint("symbol", "report_date"),
    )


# ── Computed Indicators ──────────────────────────────────────────────────────


class TechnicalIndicator(Base):
    __tablename__ = "technical_indicators"

    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False)
    date = Column(Text, nullable=False)
    timeframe = Column(Text, nullable=False)
    rsi_14 = Column(Float)
    sma_20 = Column(Float)
    sma_50 = Column(Float)
    sma_200 = Column(Float)
    bb_upper = Column(Float)
    bb_middle = Column(Float)
    bb_lower = Column(Float)
    bb_pct = Column(Float)   # %B: (close - lower) / (upper - lower)

    __table_args__ = (
        UniqueConstraint("symbol", "date", "timeframe"),
    )


# ── Screening Scores ────────────────────────────────────────────────────────


class ScreeningScore(Base):
    __tablename__ = "screening_scores"

    id = Column(Integer, primary_key=True)
    symbol = Column(Text, nullable=False)
    strategy = Column(Text, nullable=False)    # strategy key used
    computed_at = Column(Text, nullable=False)  # ISO datetime
    price_score = Column(Float)
    bb_score = Column(Float)
    commodity_score = Column(Float)
    pe_score = Column(Float)
    balance_score = Column(Float)
    composite_score = Column(Float)

    __table_args__ = (
        UniqueConstraint("symbol", "strategy", "computed_at"),
    )
