"""Generate multi-panel stock charts as PNG images.

Layout per chart:
    Panel 1 (75%): Candlestick + SMA(20/50/200) + Bollinger Band fill
    Panel 2 (25%): RSI(14) with overbought/oversold lines

Uses mplfinance for candlestick rendering, matplotlib for the RSI subplot.
"""

from __future__ import annotations

import io
import logging

import matplotlib
matplotlib.use("Agg")  # non-interactive backend

import matplotlib.pyplot as plt
import mplfinance as mpf
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from app.models import PriceHistory, TechnicalIndicator

log = logging.getLogger(__name__)

# ── Lookback defaults per timeframe ──────────────────────────────────────────

_DEFAULT_BARS: dict[str, int] = {"D": 500, "W": 260, "M": 120}

# ── Colours ──────────────────────────────────────────────────────────────────

_SMA_COLORS = {20: "#2196F3", 50: "#FF9800", 200: "#F44336"}  # blue, orange, red
_BB_FILL = "#90CAF9"
_RSI_COLOR = "#7E57C2"


# ── Data loading ─────────────────────────────────────────────────────────────


def _load_chart_data(
    session: Session,
    symbol: str,
    timeframe: str,
    bars: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (price_df, indicator_df) with DatetimeIndex, last *bars* rows."""
    if bars is None:
        bars = _DEFAULT_BARS.get(timeframe, 500)

    prices = (
        session.query(
            PriceHistory.date,
            PriceHistory.open,
            PriceHistory.high,
            PriceHistory.low,
            PriceHistory.close,
            PriceHistory.volume,
        )
        .filter_by(symbol=symbol, timeframe=timeframe)
        .order_by(PriceHistory.date.desc())
        .limit(bars)
        .all()
    )
    if not prices:
        return pd.DataFrame(), pd.DataFrame()

    pdf = pd.DataFrame(prices, columns=["date", "Open", "High", "Low", "Close", "Volume"])
    pdf["date"] = pd.to_datetime(pdf["date"])
    pdf.sort_values("date", inplace=True)
    pdf.set_index("date", inplace=True)

    # Indicators for same date range
    min_date = pdf.index.min().strftime("%Y-%m-%d")
    indicators = (
        session.query(
            TechnicalIndicator.date,
            TechnicalIndicator.rsi_14,
            TechnicalIndicator.sma_20,
            TechnicalIndicator.sma_50,
            TechnicalIndicator.sma_200,
            TechnicalIndicator.bb_upper,
            TechnicalIndicator.bb_lower,
        )
        .filter_by(symbol=symbol, timeframe=timeframe)
        .filter(TechnicalIndicator.date >= min_date)
        .order_by(TechnicalIndicator.date)
        .all()
    )
    if indicators:
        idf = pd.DataFrame(
            indicators,
            columns=["date", "rsi_14", "sma_20", "sma_50", "sma_200", "bb_upper", "bb_lower"],
        )
        idf["date"] = pd.to_datetime(idf["date"])
        idf.set_index("date", inplace=True)
    else:
        idf = pd.DataFrame()

    return pdf, idf


# ── Chart rendering ──────────────────────────────────────────────────────────

_TF_LABEL = {"D": "Daily", "W": "Weekly", "M": "Monthly"}


def build_chart(
    session: Session,
    symbol: str,
    timeframe: str = "D",
    bars: int | None = None,
    width: int = 14,
    height: int = 8,
) -> bytes:
    """Render a two-panel chart and return PNG bytes."""
    pdf, idf = _load_chart_data(session, symbol, timeframe, bars)
    if pdf.empty:
        return _empty_chart(symbol, timeframe)

    # Align indicators to price index
    if not idf.empty:
        idf = idf.reindex(pdf.index)

    # ── Build addplot overlays for mplfinance ────────────────────────────
    addplots = []

    if not idf.empty:
        # SMA lines on main panel
        for period, col in [(20, "sma_20"), (50, "sma_50"), (200, "sma_200")]:
            if col in idf.columns and idf[col].notna().any():
                addplots.append(mpf.make_addplot(
                    idf[col], panel=0, color=_SMA_COLORS[period],
                    width=1.0, label=f"SMA {period}",
                ))

        # Bollinger Band lines on main panel
        if "bb_upper" in idf.columns and idf["bb_upper"].notna().any():
            addplots.append(mpf.make_addplot(
                idf["bb_upper"], panel=0, color=_BB_FILL,
                width=0.7, linestyle="--",
            ))
            addplots.append(mpf.make_addplot(
                idf["bb_lower"], panel=0, color=_BB_FILL,
                width=0.7, linestyle="--",
            ))
            # Fill between is done post-render below

        # RSI on panel 1
        if "rsi_14" in idf.columns and idf["rsi_14"].notna().any():
            addplots.append(mpf.make_addplot(
                idf["rsi_14"], panel=1, color=_RSI_COLOR,
                width=1.2, ylabel="RSI",
            ))

    # ── Render with mplfinance ───────────────────────────────────────────
    style = mpf.make_mpf_style(
        base_mpf_style="charles",
        rc={"font.size": 9},
    )

    kwargs = dict(
        type="candle",
        style=style,
        volume=False,
        figsize=(width, height),
        panel_ratios=(3, 1) if addplots else (1,),
        returnfig=True,
        tight_layout=True,
    )
    if addplots:
        kwargs["addplot"] = addplots

    fig, axes = mpf.plot(pdf, **kwargs)

    # ── Post-render: BB fill + RSI reference lines ───────────────────────
    ax_main = axes[0]

    if not idf.empty and "bb_upper" in idf.columns:
        x = np.arange(len(pdf))
        upper = idf["bb_upper"].values
        lower = idf["bb_lower"].values
        mask = ~(np.isnan(upper) | np.isnan(lower))
        ax_main.fill_between(
            x, upper, lower, where=mask,
            alpha=0.12, color=_BB_FILL, zorder=0,
        )

    # Title
    tf_label = _TF_LABEL.get(timeframe, timeframe)
    ax_main.set_title(f"{symbol} — {tf_label}", fontsize=13, fontweight="bold", loc="left")

    # SMA legend
    sma_labels = []
    for period, col in [(20, "sma_20"), (50, "sma_50"), (200, "sma_200")]:
        if not idf.empty and col in idf.columns and idf[col].notna().any():
            sma_labels.append(f"SMA{period}")
    if sma_labels:
        ax_main.legend(sma_labels, loc="upper left", fontsize=8, framealpha=0.7)

    # RSI reference lines — find the RSI axis by ylabel
    ax_rsi = None
    for ax in axes:
        if ax.get_ylabel() == "RSI":
            ax_rsi = ax
            break
    if ax_rsi is not None:
        ax_rsi.axhline(70, color="red", linewidth=0.6, linestyle="--", alpha=0.7)
        ax_rsi.axhline(30, color="green", linewidth=0.6, linestyle="--", alpha=0.7)
        ax_rsi.axhline(50, color="gray", linewidth=0.4, linestyle=":", alpha=0.5)
        ax_rsi.set_ylim(0, 100)
        ax_rsi.set_ylabel("RSI(14)", fontsize=9)

    # ── Export to PNG bytes ───────────────────────────────────────────────
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _empty_chart(symbol: str, timeframe: str) -> bytes:
    """Return a placeholder PNG when no data is available."""
    fig, ax = plt.subplots(figsize=(10, 4))
    tf_label = _TF_LABEL.get(timeframe, timeframe)
    ax.text(
        0.5, 0.5,
        f"No data for {symbol} ({tf_label})\nRun refresh first.",
        ha="center", va="center", fontsize=14, color="gray",
        transform=ax.transAxes,
    )
    ax.set_axis_off()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=80)
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
