from typing import Dict, Optional

from pydantic import BaseModel, model_validator


# ── Domain objects ───────────────────────────────────────────────────────────


class SectorOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    ticker_count: int = 0

    class Config:
        from_attributes = True


class TickerOut(BaseModel):
    id: int
    symbol: str
    name: str
    exchange: Optional[str] = None
    sector_id: Optional[int] = None

    class Config:
        from_attributes = True


class CommodityOut(BaseModel):
    commodity_symbol: str
    commodity_name: str

    class Config:
        from_attributes = True


# ── Scoring / Screening ─────────────────────────────────────────────────────


VALID_SCORE_COMPONENTS = {"price", "bb", "commodity", "pe", "balance"}


class StrategyInfo(BaseModel):
    """Read-only view of a built-in or custom strategy."""
    key: str
    name: str
    description: str
    weights: Dict[str, float]


class CustomStrategyIn(BaseModel):
    """User-supplied weights for an ad-hoc scoring run.

    Only the components you list get weight; missing ones default to 0.
    Weights are auto-normalised to sum to 1.0.
    """
    name: str = "custom"
    weights: Dict[str, float]

    @model_validator(mode="after")
    def _validate_weights(self) -> "CustomStrategyIn":
        bad = set(self.weights) - VALID_SCORE_COMPONENTS
        if bad:
            raise ValueError(
                f"Unknown weight keys: {bad}. "
                f"Valid: {VALID_SCORE_COMPONENTS}"
            )
        if all(v == 0 for v in self.weights.values()):
            raise ValueError("At least one weight must be > 0")
        return self


class StockScoreOut(BaseModel):
    """One row of the screener table."""
    symbol: str
    name: str
    exchange: Optional[str] = None
    price: Optional[float] = None
    price_score: float
    bb_score: float
    commodity_score: float
    pe_score: float
    balance_score: float
    composite_score: float
    strategy: str

    class Config:
        from_attributes = True
