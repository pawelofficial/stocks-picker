import logging
import uuid
from typing import Dict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db, SessionLocal
from app.models import Sector
from app.services.data_fetcher import refresh_sector as _refresh_sector, refresh_symbol
from app.services.indicators import compute_all_for_sector, compute_all_for_symbol
from app.services.scorer import invalidate_scorer_cache

router = APIRouter()
log = logging.getLogger(__name__)

_jobs: Dict[str, str] = {}


def _bg_refresh_sector(sector_id: int, full: bool, job_id: str):
    """Run in background thread — needs its own session."""
    session = SessionLocal()
    try:
        stats = _refresh_sector(session, sector_id, full=full)
        log.info("BG refresh sector %d (full=%s): %s rows, %s errors", sector_id, full, stats.price_rows_written, len(stats.errors))
        n = compute_all_for_sector(session, sector_id, force=full)
        log.info("BG indicators sector %d: %d rows", sector_id, n)
        invalidate_scorer_cache()
        _jobs[job_id] = "done"
    except Exception:
        log.exception("BG refresh sector %d failed", sector_id)
        _jobs[job_id] = "error"
    finally:
        session.close()


def _bg_refresh_symbol(symbol: str, full: bool, job_id: str):
    session = SessionLocal()
    try:
        stats = refresh_symbol(session, symbol, full=full)
        log.info("BG refresh %s (full=%s): %s rows", symbol, full, stats.price_rows_written)
        compute_all_for_symbol(session, symbol, force=full)
        invalidate_scorer_cache()
        _jobs[job_id] = "done"
    except Exception:
        log.exception("BG refresh %s failed", symbol)
        _jobs[job_id] = "error"
    finally:
        session.close()


def _bg_refresh_all(full: bool, job_id: str):
    """Refresh every sector sequentially in one background job."""
    session = SessionLocal()
    try:
        sector_ids = [s.id for s in session.query(Sector).order_by(Sector.id).all()]
        total_rows = 0
        total_errors = 0
        for sid in sector_ids:
            try:
                stats = _refresh_sector(session, sid, full=full)
                total_rows += stats.price_rows_written
                total_errors += len(stats.errors)
                n = compute_all_for_sector(session, sid, force=full)
                log.info(
                    "BG refresh-all: sector %d done (%d price rows, %d indicator rows, %d errors)",
                    sid, stats.price_rows_written, n, len(stats.errors),
                )
            except Exception:
                log.exception("BG refresh-all: sector %d failed", sid)
                total_errors += 1
        invalidate_scorer_cache()
        log.info(
            "BG refresh-all complete (full=%s): %d sectors, %d rows, %d errors",
            full, len(sector_ids), total_rows, total_errors,
        )
        _jobs[job_id] = "done"
    except Exception:
        log.exception("BG refresh-all failed")
        _jobs[job_id] = "error"
    finally:
        session.close()


@router.get("/api/refresh/status/{job_id}")
def refresh_status(job_id: str):
    status = _jobs.get(job_id, "running")
    if status in ("done", "error"):
        _jobs.pop(job_id, None)
    return {"job_id": job_id, "status": status}


@router.post("/api/refresh/sector/{sector_id}")
def trigger_sector_refresh(
    sector_id: int,
    background_tasks: BackgroundTasks,
    full: bool = False,
    db: Session = Depends(get_db),
):
    sector = db.query(Sector).get(sector_id)
    if not sector:
        raise HTTPException(status_code=404, detail="Sector not found")
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = "running"
    background_tasks.add_task(_bg_refresh_sector, sector_id, full, job_id)
    return {"status": "started", "sector": sector.name, "full": full, "job_id": job_id}


@router.post("/api/refresh/symbol/{symbol}")
def trigger_symbol_refresh(
    symbol: str,
    background_tasks: BackgroundTasks,
    full: bool = False,
):
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = "running"
    background_tasks.add_task(_bg_refresh_symbol, symbol.upper(), full, job_id)
    return {"status": "started", "symbol": symbol.upper(), "full": full, "job_id": job_id}


@router.post("/api/refresh/all")
def trigger_refresh_all(
    background_tasks: BackgroundTasks,
    full: bool = False,
    db: Session = Depends(get_db),
):
    sector_count = db.query(Sector).count()
    if sector_count == 0:
        raise HTTPException(status_code=404, detail="No sectors found. Seed the database first.")
    job_id = uuid.uuid4().hex[:12]
    _jobs[job_id] = "running"
    background_tasks.add_task(_bg_refresh_all, full, job_id)
    return {"status": "started", "scope": "all", "sectors": sector_count, "full": full, "job_id": job_id}
