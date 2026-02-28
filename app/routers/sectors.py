from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Sector, Ticker, SectorCommodity

router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def home(request: Request):
    return request.app.state.templates.TemplateResponse(
        "home.html", {"request": request},
    )


@router.get("/sectors", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    sectors = db.query(Sector).order_by(Sector.name).all()
    sector_data = []
    for s in sectors:
        ticker_count = db.query(Ticker).filter_by(sector_id=s.id).count()
        commodity_count = db.query(SectorCommodity).filter_by(sector_id=s.id).count()
        sector_data.append({
            "id": s.id,
            "name": s.name,
            "description": s.description or "",
            "ticker_count": ticker_count,
            "commodity_count": commodity_count,
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
