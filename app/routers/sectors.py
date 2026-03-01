from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from sqlalchemy import func as sa_func

from app.database import get_db
from app.models import Sector, Ticker, SectorCommodity, ScreeningScore
from app.services.scorer import (
    STRATEGIES,
    Scorer,
    list_strategies,
)

router = APIRouter()

_VALID_TF = {"D", "W", "M", "All"}
_TF_LABELS = {"D": "Daily", "W": "Weekly", "M": "Monthly", "All": "All"}


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    strategy: str = "overall",
    top: int = 20,
    db: Session = Depends(get_db),
):
    strat = STRATEGIES.get(strategy) or STRATEGIES["overall"]
    if top < 1:
        top = 20

    all_sectors = db.query(Sector).order_by(Sector.name).all()
    results = []
    for tf in ("D", "W", "M"):
        scorer = Scorer(db, timeframe=tf)
        for sec in all_sectors:
            for r in scorer.screen_sector(sec.id, strat):
                r.timeframe = tf
                results.append(r)
    results.sort(key=lambda r: r.composite_score, reverse=True)
    total = len(results)
    top_results = results[:top]

    return request.app.state.templates.TemplateResponse(
        "home.html",
        {
            "request": request,
            "results": top_results,
            "total": total,
            "strategies": list_strategies(),
            "current_strategy": strategy,
            "top": top,
        },
    )


@router.get("/screener", response_class=HTMLResponse)
def global_screener(
    request: Request,
    strategy: str = "overall",
    tf: str = "D",
    sectors: Optional[str] = None,
    db: Session = Depends(get_db),
):
    strat = STRATEGIES.get(strategy) or STRATEGIES["overall"]
    if tf not in _VALID_TF:
        tf = "D"

    all_sectors = db.query(Sector).order_by(Sector.name).all()
    if sectors:
        selected_ids = set()
        for s in sectors.split(","):
            try:
                selected_ids.add(int(s))
            except ValueError:
                pass
    else:
        selected_ids = {s.id for s in all_sectors}

    timeframes = ("D", "W", "M") if tf == "All" else (tf,)
    results = []
    for t in timeframes:
        scorer = Scorer(db, timeframe=t)
        for sid in selected_ids:
            for r in scorer.screen_sector(sid, strat):
                r.timeframe = t
                results.append(r)
    results.sort(key=lambda r: r.composite_score, reverse=True)

    sector_agg = {}
    for r in results:
        key = (r.sector_name, r.timeframe)
        bucket = sector_agg.setdefault(key, {
            "sector_name": r.sector_name,
            "timeframe": r.timeframe,
            "count": 0,
            "price_score": 0.0,
            "bb_score": 0.0,
            "commodity_score": 0.0,
            "pe_score": 0.0,
            "balance_score": 0.0,
            "composite_score": 0.0,
        })
        bucket["count"] += 1
        for k in ("price_score", "bb_score", "commodity_score", "pe_score", "balance_score", "composite_score"):
            bucket[k] += getattr(r, k)

    sector_summaries = []
    for bucket in sector_agg.values():
        n = bucket["count"]
        sector_summaries.append({
            "sector_name": bucket["sector_name"],
            "timeframe": bucket["timeframe"],
            "ticker_count": n,
            "price_score": bucket["price_score"] / n,
            "bb_score": bucket["bb_score"] / n,
            "commodity_score": bucket["commodity_score"] / n,
            "pe_score": bucket["pe_score"] / n,
            "balance_score": bucket["balance_score"] / n,
            "composite_score": bucket["composite_score"] / n,
        })
    sector_summaries.sort(key=lambda s: s["composite_score"], reverse=True)

    return request.app.state.templates.TemplateResponse(
        "screener_global.html",
        {
            "request": request,
            "results": results,
            "sector_summaries": sector_summaries,
            "strategies": list_strategies(),
            "current_strategy": strategy,
            "current_tf": tf,
            "tf_labels": _TF_LABELS,
            "all_sectors": [{"id": s.id, "name": s.name} for s in all_sectors],
            "selected_sector_ids": selected_ids,
            "selected_sectors_csv": ",".join(str(i) for i in sorted(selected_ids)),
        },
    )


@router.get("/sectors", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    sectors = db.query(Sector).order_by(Sector.name).all()

    sector_symbols = {}
    for s in sectors:
        syms = [t.symbol for t in db.query(Ticker.symbol).filter_by(sector_id=s.id).all()]
        sector_symbols[s.id] = syms

    score_avgs = (
        db.query(
            Ticker.sector_id,
            sa_func.avg(ScreeningScore.composite_score),
            sa_func.avg(ScreeningScore.bb_score),
        )
        .join(Ticker, Ticker.symbol == ScreeningScore.symbol)
        .filter(ScreeningScore.strategy == "overall")
        .group_by(Ticker.sector_id)
        .all()
    )
    avg_map = {sid: (comp, bb) for sid, comp, bb in score_avgs}

    sector_data = []
    for s in sectors:
        ticker_count = len(sector_symbols.get(s.id, []))
        commodity_count = db.query(SectorCommodity).filter_by(sector_id=s.id).count()
        avgs = avg_map.get(s.id)
        sector_data.append({
            "id": s.id,
            "name": s.name,
            "description": s.description or "",
            "ticker_count": ticker_count,
            "commodity_count": commodity_count,
            "avg_composite": avgs[0] if avgs else None,
            "avg_bb": avgs[1] if avgs else None,
        })
    return request.app.state.templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "sectors": sector_data},
    )


@router.get("/api/sectors")
def list_sectors(db: Session = Depends(get_db)):
    sectors = db.query(Sector).order_by(Sector.name).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "ticker_count": db.query(Ticker).filter_by(sector_id=s.id).count(),
        }
        for s in sectors
    ]
