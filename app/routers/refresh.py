import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Sector
from app.services.data_fetcher import refresh_sector as _refresh_sector, refresh_symbol
from app.services.indicators import compute_all_for_sector, compute_all_for_symbol

router = APIRouter()
log = logging.getLogger(__name__)


def _bg_refresh_sector(sector_id: int):
    """Run in background thread — needs its own session."""
    session = SessionLocal()
    try:
        stats = _refresh_sector(session, sector_id)
        log.info("BG refresh sector %d: %s rows, %s errors", sector_id, stats.price_rows_written, len(stats.errors))
        n = compute_all_for_sector(session, sector_id)
        log.info("BG indicators sector %d: %d rows", sector_id, n)
    except Exception:
        log.exception("BG refresh sector %d failed", sector_id)
    finally:
        session.close()


def _bg_refresh_symbol(symbol: str):
    session = SessionLocal()
    try:
        stats = refresh_symbol(session, symbol)
        log.info("BG refresh %s: %s rows", symbol, stats.price_rows_written)
        compute_all_for_symbol(session, symbol)
    except Exception:
        log.exception("BG refresh %s failed", symbol)
    finally:
        session.close()


@router.post("/api/refresh/sector/{sector_id}")
def trigger_sector_refresh(
    sector_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    sector = db.query(Sector).get(sector_id)
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    background_tasks.add_task(_bg_refresh_sector, sector_id)
    return {"status": "started", "sector": sector.name}


@router.post("/api/refresh/symbol/{symbol}")
def trigger_symbol_refresh(
    symbol: str,
    background_tasks: BackgroundTasks,
):
    background_tasks.add_task(_bg_refresh_symbol, symbol.upper())
    return {"status": "started", "symbol": symbol.upper()}
