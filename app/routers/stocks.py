from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Fundamental, Sector, SectorCommodity, Ticker
from app.schemas import CustomStrategyIn
from app.services.scorer import (
    STRATEGIES,
    Scorer,
    ScoringStrategy,
    list_strategies,
)

router = APIRouter()


# ── Screener page ────────────────────────────────────────────────────────────


_VALID_TF = {"D", "W", "M", "All"}
_TF_LABELS = {"D": "Daily", "W": "Weekly", "M": "Monthly", "All": "All"}


@router.get("/sectors/{sector_id}/screen", response_class=HTMLResponse)
def screener_page(
    request: Request,
    sector_id: int,
    strategy: str = "overall",
    tf: str = "D",
    db: Session = Depends(get_db),
):
    sector = db.query(Sector).get(sector_id)
    if not sector:
        return HTMLResponse("Sector not found", status_code=404)

    strat = STRATEGIES.get(strategy)
    if strat is None:
        return HTMLResponse(f"Unknown strategy: {strategy}", status_code=400)

    if tf not in _VALID_TF:
        tf = "D"

    if tf == "All":
        results = []
        for t in ("D", "W", "M"):
            scorer = Scorer(db, timeframe=t)
            for r in scorer.screen_sector(sector_id, strat):
                r.timeframe = t
                results.append(r)
        results.sort(key=lambda r: r.composite_score, reverse=True)
    else:
        scorer = Scorer(db, timeframe=tf)
        results = scorer.screen_sector(sector_id, strat)
        for r in results:
            r.timeframe = tf

    return request.app.state.templates.TemplateResponse(
        "screener.html",
        {
            "request": request,
            "sector": sector,
            "results": results,
            "strategies": list_strategies(),
            "current_strategy": strategy,
            "current_tf": tf,
            "tf_labels": _TF_LABELS,
        },
    )


@router.get("/api/sectors/{sector_id}/screen")
def screener_api(
    sector_id: int,
    strategy: str = "overall",
    tf: str = "D",
    db: Session = Depends(get_db),
):
    sector = db.query(Sector).get(sector_id)
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")

    strat = STRATEGIES.get(strategy)
    if strat is None:
        raise HTTPException(status_code=400, detail=f"Unknown strategy: {strategy}")

    if tf not in _VALID_TF:
        tf = "D"

    scorer = Scorer(db, timeframe=tf)
    results = scorer.screen_sector(sector_id, strat)
    return [
        {
            "symbol": r.symbol,
            "name": r.name,
            "exchange": r.exchange,
            "price": r.current_price,
            "price_score": r.price_score,
            "bb_score": r.bb_score,
            "commodity_score": r.commodity_score,
            "pe_score": r.pe_score,
            "balance_score": r.balance_score,
            "composite_score": r.composite_score,
        }
        for r in results
    ]


@router.post("/api/sectors/{sector_id}/screen")
def screener_custom_api(
    sector_id: int,
    body: CustomStrategyIn,
    db: Session = Depends(get_db),
):
    sector = db.query(Sector).get(sector_id)
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")

    strat = ScoringStrategy.custom(body.name, body.weights)
    scorer = Scorer(db)
    results = scorer.screen_sector(sector_id, strat)
    return [
        {
            "symbol": r.symbol,
            "name": r.name,
            "price": r.current_price,
            "price_score": r.price_score,
            "bb_score": r.bb_score,
            "commodity_score": r.commodity_score,
            "pe_score": r.pe_score,
            "balance_score": r.balance_score,
            "composite_score": r.composite_score,
        }
        for r in results
    ]


# ── Stock detail page ────────────────────────────────────────────────────────


@router.get("/stocks/{symbol}", response_class=HTMLResponse)
def stock_detail(
    request: Request,
    symbol: str,
    db: Session = Depends(get_db),
):
    ticker = db.query(Ticker).filter_by(symbol=symbol.upper()).first()
    if not ticker:
        return HTMLResponse(f"Ticker {symbol} not found", status_code=404)

    fund = (
        db.query(Fundamental)
        .filter_by(symbol=ticker.symbol)
        .order_by(Fundamental.report_date.desc())
        .first()
    )

    sector = db.query(Sector).get(ticker.sector_id) if ticker.sector_id else None

    # Commodity symbols for this sector (for commodity chart tabs)
    commodities = []
    if sector:
        commodities = (
            db.query(SectorCommodity)
            .filter_by(sector_id=sector.id)
            .all()
        )

    return request.app.state.templates.TemplateResponse(
        "stock_detail.html",
        {
            "request": request,
            "ticker": ticker,
            "fund": fund,
            "sector": sector,
            "commodities": commodities,
        },
    )


# ── Strategies list ──────────────────────────────────────────────────────────


@router.get("/api/strategies")
def get_strategies():
    return list_strategies()
