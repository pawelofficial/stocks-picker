from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Fundamental, Sector, Ticker
from app.schemas import CustomStrategyIn
from app.services.scorer import (
    STRATEGIES,
    Scorer,
    ScoringStrategy,
    list_strategies,
)

router = APIRouter()


# ── Screener page ────────────────────────────────────────────────────────────


@router.get("/sectors/{sector_id}/screen", response_class=HTMLResponse)
def screener_page(
    request: Request,
    sector_id: int,
    strategy: str = "overall",
    db: Session = Depends(get_db),
):
    sector = db.query(Sector).get(sector_id)
    if not sector:
        return HTMLResponse("Sector not found", status_code=404)

    strat = STRATEGIES.get(strategy)
    if strat is None:
        return HTMLResponse(f"Unknown strategy: {strategy}", status_code=400)

    scorer = Scorer(db)
    results = scorer.screen_sector(sector_id, strat)

    return request.app.state.templates.TemplateResponse(
        "screener.html",
        {
            "request": request,
            "sector": sector,
            "results": results,
            "strategies": list_strategies(),
            "current_strategy": strategy,
        },
    )


@router.get("/api/sectors/{sector_id}/screen")
def screener_api(
    sector_id: int,
    strategy: str = "overall",
    db: Session = Depends(get_db),
):
    strat = STRATEGIES.get(strategy)
    if strat is None:
        return {"error": f"Unknown strategy: {strategy}"}

    scorer = Scorer(db)
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

    return request.app.state.templates.TemplateResponse(
        "stock_detail.html",
        {
            "request": request,
            "ticker": ticker,
            "fund": fund,
            "sector": sector,
        },
    )


# ── Strategies list ──────────────────────────────────────────────────────────


@router.get("/api/strategies")
def get_strategies():
    return list_strategies()
