from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.chart_builder import build_chart

router = APIRouter()


@router.get("/charts/{symbol}")
def chart_png(
    symbol: str,
    tf: str = Query("D", pattern="^[DWM]$"),
    bars: int | None = Query(None, ge=50, le=2000),
    db: Session = Depends(get_db),
):
    """Return a PNG chart for the given symbol and timeframe."""
    png = build_chart(db, symbol.upper(), timeframe=tf, bars=bars)
    return Response(content=png, media_type="image/png")
