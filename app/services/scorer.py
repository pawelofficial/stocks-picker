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

import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from statistics import median
from typing import Dict, List, Optional, Sequence, Tuple

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
    timeframe: str = "D"
    sector_name: str = ""


_sector_cache: Dict[Tuple, Tuple[float, List[StockResult]]] = {}
_CACHE_TTL = 900  # seconds


def _cache_key(sector_id: int, tf: str, strategy_key: str) -> Tuple:
    return (sector_id, tf, strategy_key)


def invalidate_scorer_cache():
    """Call after data refresh to clear cached scores."""
    _sector_cache.clear()


class Scorer:
    """Pulls data from the DB and scores every ticker in a sector.

    Uses batch queries (one per data type per sector) instead of N+1,
    and caches results for ``_CACHE_TTL`` seconds.
    """

    def __init__(self, session: Session, timeframe: str = "D"):
        self.session = session
        self.timeframe = timeframe

    # ── batch data loaders ────────────────────────────────────────────

    def _batch_closes(self, symbols: Sequence[str]) -> Dict[str, List[float]]:
        rows = (
            self.session.query(PriceHistory.symbol, PriceHistory.close, PriceHistory.date)
            .filter(PriceHistory.symbol.in_(symbols), PriceHistory.timeframe == self.timeframe)
            .order_by(PriceHistory.symbol, PriceHistory.date)
            .all()
        )
        out: Dict[str, List[float]] = defaultdict(list)
        for sym, close, _ in rows:
            if close is not None:
                out[sym].append(close)
        return dict(out)

    def _batch_latest_bb_pct(self, symbols: Sequence[str]) -> Dict[str, Optional[float]]:
        subq = (
            self.session.query(
                TechnicalIndicator.symbol,
                func.max(TechnicalIndicator.date).label("max_date"),
            )
            .filter(TechnicalIndicator.symbol.in_(symbols), TechnicalIndicator.timeframe == self.timeframe)
            .group_by(TechnicalIndicator.symbol)
            .subquery()
        )
        rows = (
            self.session.query(TechnicalIndicator.symbol, TechnicalIndicator.bb_pct)
            .join(subq, (TechnicalIndicator.symbol == subq.c.symbol) & (TechnicalIndicator.date == subq.c.max_date))
            .filter(TechnicalIndicator.timeframe == self.timeframe)
            .all()
        )
        return {sym: bb for sym, bb in rows}

    def _batch_fundamentals(self, symbols: Sequence[str]) -> Dict[str, Fundamental]:
        subq = (
            self.session.query(
                Fundamental.symbol,
                func.max(Fundamental.report_date).label("max_date"),
            )
            .filter(Fundamental.symbol.in_(symbols))
            .group_by(Fundamental.symbol)
            .subquery()
        )
        rows = (
            self.session.query(Fundamental)
            .join(subq, (Fundamental.symbol == subq.c.symbol) & (Fundamental.report_date == subq.c.max_date))
            .all()
        )
        return {f.symbol: f for f in rows}

    def _batch_historical_pes(self, symbols: Sequence[str]) -> Dict[str, List[float]]:
        rows = (
            self.session.query(Fundamental.symbol, Fundamental.pe_ratio)
            .filter(
                Fundamental.symbol.in_(symbols),
                Fundamental.pe_ratio.isnot(None),
                Fundamental.pe_ratio > 0,
            )
            .all()
        )
        out: Dict[str, List[float]] = defaultdict(list)
        for sym, pe in rows:
            out[sym].append(pe)
        return dict(out)

    def _commodity_closes(self, sector_id: int) -> Tuple[List[float], Optional[float]]:
        commodities = (
            self.session.query(SectorCommodity.commodity_symbol)
            .filter_by(sector_id=sector_id)
            .all()
        )
        if not commodities:
            return [], None

        syms = [s for (s,) in commodities]
        rows = (
            self.session.query(PriceHistory.symbol, PriceHistory.close)
            .filter(PriceHistory.symbol.in_(syms), PriceHistory.timeframe == self.timeframe)
            .order_by(PriceHistory.symbol, PriceHistory.date)
            .all()
        )
        per_sym: Dict[str, List[float]] = defaultdict(list)
        for sym, close in rows:
            if close is not None:
                per_sym[sym].append(close)

        all_closes: List[float] = []
        latest_values: List[float] = []
        for closes in per_sym.values():
            if closes:
                all_closes.extend(closes)
                latest_values.append(closes[-1])

        latest = sum(latest_values) / len(latest_values) if latest_values else None
        return all_closes, latest

    # ── scoring ──────────────────────────────────────────────────────────

    def screen_sector(
        self,
        sector_id: int,
        strategy: ScoringStrategy,
    ) -> List[StockResult]:
        """Score all tickers in a sector, return sorted by composite desc."""
        key = _cache_key(sector_id, self.timeframe, strategy.key)
        cached = _sector_cache.get(key)
        if cached:
            ts, results = cached
            if time.monotonic() - ts < _CACHE_TTL:
                return [r for r in results]  # shallow copy

        sector = self.session.query(Sector).get(sector_id)
        sector_name = sector.name if sector else ""

        tickers = (
            self.session.query(Ticker)
            .filter_by(sector_id=sector_id)
            .all()
        )
        if not tickers:
            return []

        symbols = [t.symbol for t in tickers]
        active = strategy.active_components()

        closes_map = self._batch_closes(symbols)
        bb_map = self._batch_latest_bb_pct(symbols) if "bb" in active else {}
        fund_map = self._batch_fundamentals(symbols) if ("pe" in active or "balance" in active) else {}
        pes_map = self._batch_historical_pes(symbols) if "pe" in active else {}
        commodity_data = self._commodity_closes(sector_id) if "commodity" in active else ([], None)

        results: List[StockResult] = []
        for t in tickers:
            closes = closes_map.get(t.symbol, [])
            current_price = closes[-1] if closes else None

            price_sc = compute_price_score(closes, current_price) if "price" in active else 50.0

            bb_pct = bb_map.get(t.symbol) if "bb" in active else None
            bb_sc = compute_bb_score(bb_pct)

            if "commodity" in active and commodity_data[0]:
                c_closes, c_latest = commodity_data
                commodity_sc = compute_commodity_score(c_closes, c_latest)
            else:
                commodity_sc = 50.0

            fund = fund_map.get(t.symbol)
            pe_sc = compute_pe_score(fund.pe_ratio, pes_map.get(t.symbol, [])) if ("pe" in active and fund) else 50.0

            if "balance" in active and fund:
                balance_sc = compute_balance_score(
                    fund.debt_to_equity, fund.current_ratio,
                    fund.interest_coverage, fund.roe,
                )
            else:
                balance_sc = 50.0

            sub = {"price": price_sc, "bb": bb_sc, "commodity": commodity_sc, "pe": pe_sc, "balance": balance_sc}
            composite = compute_composite(sub, strategy)

            results.append(StockResult(
                symbol=t.symbol,
                name=t.name,
                exchange=t.exchange,
                current_price=current_price,
                price_score=price_sc,
                bb_score=bb_sc,
                commodity_score=commodity_sc,
                pe_score=pe_sc,
                balance_score=balance_sc,
                composite_score=composite,
                strategy_key=strategy.key,
                sector_name=sector_name,
            ))

        results.sort(key=lambda r: r.composite_score, reverse=True)
        _sector_cache[key] = (time.monotonic(), results)

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
