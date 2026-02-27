"""Extensible stock screening scorer.

The scoring system is built around *strategies* — named weight vectors that
control how the five sub-scores are blended into a composite.

Built-in strategies cover common use-cases.  Users can also pass ad-hoc
weights via the API (``CustomStrategyIn``).

Usage
-----
    from app.services.scorer import STRATEGIES, get_strategy, Scorer

    # Use a built-in preset
    strategy = get_strategy("bollinger")

    # Or create a custom one on the fly
    strategy = ScoringStrategy.custom("my_mix", {"bb": 0.6, "pe": 0.4})

    scorer = Scorer(db_session)
    results = scorer.screen_sector(sector_id=1, strategy=strategy)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Sequence

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import (
    Fundamental,
    PriceHistory,
    ScreeningScore,
    Sector,
    SectorCommodity,
    TechnicalIndicator,
    Ticker,
)

# ── Score component names ────────────────────────────────────────────────────

SCORE_COMPONENTS: tuple[str, ...] = ("price", "bb", "commodity", "pe", "balance")


# ── Strategy ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScoringStrategy:
    """A named weight vector over the five score components.

    Weights don't need to sum to 1 — they're normalised at composite time.
    Missing components default to 0.
    """

    key: str
    name: str
    description: str
    weights: dict[str, float]

    # ── helpers ───────────────────────────────────────────────────────────

    def normalized_weights(self) -> dict[str, float]:
        w = {k: self.weights.get(k, 0.0) for k in SCORE_COMPONENTS}
        total = sum(w.values())
        if total == 0:
            # safety fallback — equal weight
            return {k: 1.0 / len(SCORE_COMPONENTS) for k in SCORE_COMPONENTS}
        return {k: v / total for k, v in w.items()}

    def active_components(self) -> set[str]:
        """Components with non-zero weight (optimisation hint)."""
        return {k for k in SCORE_COMPONENTS if self.weights.get(k, 0) > 0}

    @staticmethod
    def custom(name: str, weights: dict[str, float]) -> ScoringStrategy:
        """Factory for ad-hoc strategies from user-supplied weights."""
        return ScoringStrategy(
            key="custom",
            name=name,
            description="Custom strategy",
            weights=weights,
        )


# ── Built-in presets ─────────────────────────────────────────────────────────

STRATEGIES: dict[str, ScoringStrategy] = {
    "overall": ScoringStrategy(
        key="overall",
        name="Overall",
        description="Balanced composite of all five factors",
        weights={"price": 0.25, "bb": 0.25, "commodity": 0.20, "pe": 0.15, "balance": 0.15},
    ),
    "bollinger": ScoringStrategy(
        key="bollinger",
        name="Bollinger Bands",
        description="Rank purely by Bollinger Band depression",
        weights={"bb": 1.0},
    ),
    "value": ScoringStrategy(
        key="value",
        name="Value",
        description="P/E and balance sheet health 50 / 50",
        weights={"pe": 0.5, "balance": 0.5},
    ),
    "deep_value": ScoringStrategy(
        key="deep_value",
        name="Deep Value",
        description="Historical cheapness combined with solid fundamentals",
        weights={"price": 0.4, "pe": 0.3, "balance": 0.3},
    ),
    "commodity_play": ScoringStrategy(
        key="commodity_play",
        name="Commodity Play",
        description="Cheap underlying commodity paired with depressed stock",
        weights={"commodity": 0.4, "price": 0.3, "bb": 0.3},
    ),
}


def get_strategy(key: str) -> ScoringStrategy:
    """Look up a built-in strategy by key.  Raises KeyError if unknown."""
    return STRATEGIES[key]


def list_strategies() -> list[dict]:
    """Return serialisable list of all built-in strategies."""
    return [
        {
            "key": s.key,
            "name": s.name,
            "description": s.description,
            "weights": s.weights,
        }
        for s in STRATEGIES.values()
    ]


# ── Sub-score helpers (pure functions, no DB) ────────────────────────────────


def _percentile_of(values: Sequence[float], score: float) -> float:
    """Percentage of *values* that are ≤ *score*.  Returns 0–100."""
    if not values:
        return 50.0
    count = sum(1 for v in values if v <= score)
    return 100.0 * count / len(values)


def compute_price_score(historical_closes: Sequence[float], current: float) -> float:
    """0–100.  High = price is historically cheap."""
    if not historical_closes or current is None:
        return 50.0
    pct = _percentile_of(historical_closes, current)
    return round(100.0 * (1.0 - pct / 100.0), 2)


def compute_bb_score(bb_pct: float | None) -> float:
    """0–100.  High = price is depressed relative to Bollinger Bands."""
    if bb_pct is None:
        return 50.0
    return round(max(0.0, min(100.0, 100.0 * (1.0 - bb_pct))), 2)


def compute_commodity_score(
    commodity_closes: Sequence[float],
    current_commodity: float | None,
) -> float:
    """0–100.  High = underlying commodity is historically cheap."""
    if not commodity_closes or current_commodity is None:
        return 50.0
    pct = _percentile_of(commodity_closes, current_commodity)
    return round(100.0 * (1.0 - pct / 100.0), 2)


def compute_pe_score(
    current_pe: float | None,
    historical_pes: Sequence[float],
) -> float:
    """0–100.  High = P/E is low relative to own history."""
    if current_pe is None or not historical_pes:
        return 50.0
    # Negative PE means company is unprofitable — penalise, don't reward
    if current_pe <= 0:
        return 0.0
    med = median(historical_pes)
    if med <= 0:
        return 50.0
    relative = current_pe / med
    return round(max(0.0, min(100.0, 100.0 * (2.0 - relative))), 2)


def compute_balance_score(
    debt_to_equity: float | None,
    current_ratio: float | None,
    interest_coverage: float | None,
    roe: float | None,
) -> float:
    """0–100.  High = healthy balance sheet."""
    parts: list[float] = []
    if debt_to_equity is not None:
        parts.append(max(0.0, 100.0 - debt_to_equity * 50.0))
    if current_ratio is not None:
        parts.append(min(100.0, current_ratio * 50.0))
    if interest_coverage is not None:
        parts.append(min(100.0, interest_coverage * 20.0))
    if not parts:
        return 50.0
    base = sum(parts) / len(parts)
    bonus = 10.0 if (roe is not None and roe > 0) else 0.0
    return round(min(100.0, base + bonus), 2)


def compute_composite(sub_scores: dict[str, float], strategy: ScoringStrategy) -> float:
    """Weighted blend of sub-scores using the strategy's weight vector."""
    w = strategy.normalized_weights()
    return round(sum(w[k] * sub_scores.get(k, 50.0) for k in SCORE_COMPONENTS), 2)


# ── Scorer (DB-aware orchestrator) ───────────────────────────────────────────


@dataclass
class StockResult:
    """Fully scored stock ready for ranking."""

    symbol: str
    name: str
    exchange: str | None
    current_price: float | None
    price_score: float
    bb_score: float
    commodity_score: float
    pe_score: float
    balance_score: float
    composite_score: float
    strategy_key: str


class Scorer:
    """Pulls data from the DB and scores every ticker in a sector."""

    def __init__(self, session: Session):
        self.session = session

    # ── data loaders ─────────────────────────────────────────────────────

    def _daily_closes(self, symbol: str) -> list[float]:
        rows = (
            self.session.query(PriceHistory.close)
            .filter_by(symbol=symbol, timeframe="D")
            .order_by(PriceHistory.date)
            .all()
        )
        return [r[0] for r in rows if r[0] is not None]

    def _latest_bb_pct(self, symbol: str) -> float | None:
        row = (
            self.session.query(TechnicalIndicator.bb_pct)
            .filter_by(symbol=symbol, timeframe="D")
            .order_by(TechnicalIndicator.date.desc())
            .first()
        )
        return row[0] if row else None

    def _commodity_closes(self, sector_id: int) -> tuple[list[float], float | None]:
        """Return (all_closes, latest_close) averaged across sector commodities."""
        commodities = (
            self.session.query(SectorCommodity.commodity_symbol)
            .filter_by(sector_id=sector_id)
            .all()
        )
        if not commodities:
            return [], None

        all_closes: list[float] = []
        latest_values: list[float] = []
        for (sym,) in commodities:
            rows = (
                self.session.query(PriceHistory.close)
                .filter_by(symbol=sym, timeframe="D")
                .order_by(PriceHistory.date)
                .all()
            )
            closes = [r[0] for r in rows if r[0] is not None]
            if closes:
                all_closes.extend(closes)
                latest_values.append(closes[-1])

        latest = sum(latest_values) / len(latest_values) if latest_values else None
        return all_closes, latest

    def _latest_fundamentals(self, symbol: str) -> Fundamental | None:
        return (
            self.session.query(Fundamental)
            .filter_by(symbol=symbol)
            .order_by(Fundamental.report_date.desc())
            .first()
        )

    def _historical_pes(self, symbol: str) -> list[float]:
        rows = (
            self.session.query(Fundamental.pe_ratio)
            .filter_by(symbol=symbol)
            .filter(Fundamental.pe_ratio.isnot(None))
            .filter(Fundamental.pe_ratio > 0)
            .all()
        )
        return [r[0] for r in rows]

    # ── scoring ──────────────────────────────────────────────────────────

    def score_stock(
        self,
        ticker: Ticker,
        strategy: ScoringStrategy,
        commodity_data: tuple[list[float], float | None] | None = None,
    ) -> StockResult:
        """Score a single ticker under the given strategy."""
        active = strategy.active_components()

        # Always load closes — needed for display (current_price) even if
        # "price" component has zero weight.
        closes = self._daily_closes(ticker.symbol)
        current_price = closes[-1] if closes else None

        price_sc = compute_price_score(closes, current_price) if ("price" in active) else 50.0

        bb_pct = self._latest_bb_pct(ticker.symbol) if ("bb" in active) else None
        bb_sc = compute_bb_score(bb_pct)

        if "commodity" in active and commodity_data is not None:
            c_closes, c_latest = commodity_data
            commodity_sc = compute_commodity_score(c_closes, c_latest)
        else:
            commodity_sc = 50.0

        fund = self._latest_fundamentals(ticker.symbol)

        if "pe" in active and fund:
            hist_pes = self._historical_pes(ticker.symbol)
            pe_sc = compute_pe_score(fund.pe_ratio, hist_pes)
        else:
            pe_sc = 50.0

        if "balance" in active and fund:
            balance_sc = compute_balance_score(
                fund.debt_to_equity,
                fund.current_ratio,
                fund.interest_coverage,
                fund.roe,
            )
        else:
            balance_sc = 50.0

        sub = {"price": price_sc, "bb": bb_sc, "commodity": commodity_sc, "pe": pe_sc, "balance": balance_sc}
        composite = compute_composite(sub, strategy)

        return StockResult(
            symbol=ticker.symbol,
            name=ticker.name,
            exchange=ticker.exchange,
            current_price=current_price,
            price_score=price_sc,
            bb_score=bb_sc,
            commodity_score=commodity_sc,
            pe_score=pe_sc,
            balance_score=balance_sc,
            composite_score=composite,
            strategy_key=strategy.key,
        )

    def screen_sector(
        self,
        sector_id: int,
        strategy: ScoringStrategy,
    ) -> list[StockResult]:
        """Score all tickers in a sector, return sorted by composite desc."""
        tickers = (
            self.session.query(Ticker)
            .filter_by(sector_id=sector_id)
            .all()
        )
        if not tickers:
            return []

        # Pre-fetch commodity data once for the whole sector
        commodity_data = self._commodity_closes(sector_id)

        results = [
            self.score_stock(t, strategy, commodity_data)
            for t in tickers
        ]
        results.sort(key=lambda r: r.composite_score, reverse=True)

        # Persist to screening_scores
        now = datetime.now(timezone.utc).isoformat()
        for r in results:
            self.session.merge(
                ScreeningScore(
                    symbol=r.symbol,
                    strategy=strategy.key,
                    computed_at=now,
                    price_score=r.price_score,
                    bb_score=r.bb_score,
                    commodity_score=r.commodity_score,
                    pe_score=r.pe_score,
                    balance_score=r.balance_score,
                    composite_score=r.composite_score,
                )
            )
        self.session.commit()

        return results
