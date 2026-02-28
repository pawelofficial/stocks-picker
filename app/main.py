"""FastAPI application factory."""

from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.logging_config import configure_logging
from app.routers import charts, refresh, sectors, stocks

configure_logging()

app = FastAPI(title="Stock Picker", version="0.1.0")

# ── Static files & templates ─────────────────────────────────────────────────

_HERE = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=_HERE / "static"), name="static")
templates = Jinja2Templates(directory=str(_HERE / "templates"))


# ── Template globals ─────────────────────────────────────────────────────────


def _score_class(score: float) -> str:
    if score >= 70:
        return "score-high"
    if score >= 40:
        return "score-mid"
    return "score-low"


def _fmt_large(val: float | None) -> str:
    if val is None:
        return "—"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1e12:
        return f"{sign}${abs_val / 1e12:.1f}T"
    if abs_val >= 1e9:
        return f"{sign}${abs_val / 1e9:.1f}B"
    if abs_val >= 1e6:
        return f"{sign}${abs_val / 1e6:.0f}M"
    return f"{sign}${abs_val:,.0f}"


templates.env.globals["score_class"] = _score_class
templates.env.globals["fmt_large"] = _fmt_large

app.state.templates = templates

# ── Routers ──────────────────────────────────────────────────────────────────

app.include_router(sectors.router)
app.include_router(stocks.router)
app.include_router(charts.router)
app.include_router(refresh.router)

# ── Startup ──────────────────────────────────────────────────────────────────


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/health")
def health():
    return {"status": "ok"}
