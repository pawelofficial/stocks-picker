# Stock Picker — Architecture & Workflow Design

## Overview

A sector-driven stock screening tool. The user selects a macro sector, the
tool pulls price history + fundamentals for all tickers in that sector, computes
technical indicators, and ranks stocks by a composite "cheapness" score that
combines price position, Bollinger Band position, underlying commodity price,
historical P/E, and balance sheet health.

**Stack:** Python · SQLite · FastAPI · Matplotlib · Jinja2

---

## 1. Database Schema (SQLite)

### 1.1 Static / Reference Tables

```sql
CREATE TABLE sectors (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    description TEXT
);

CREATE TABLE tickers (
    id        INTEGER PRIMARY KEY,
    symbol    TEXT NOT NULL UNIQUE,
    name      TEXT NOT NULL,
    exchange  TEXT,
    sector_id INTEGER REFERENCES sectors(id)
);

CREATE TABLE sector_commodities (
    id               INTEGER PRIMARY KEY,
    sector_id        INTEGER REFERENCES sectors(id),
    commodity_symbol TEXT NOT NULL,
    commodity_name   TEXT NOT NULL,
    UNIQUE(sector_id, commodity_symbol)
);
```

### 1.2 Time-Series Tables

```sql
CREATE TABLE price_history (
    id        INTEGER PRIMARY KEY,
    symbol    TEXT NOT NULL,
    date      TEXT NOT NULL,
    timeframe TEXT NOT NULL,            -- 'D' | 'W' | 'M'
    open      REAL, high REAL, low REAL, close REAL, volume REAL,
    UNIQUE(symbol, date, timeframe)
);
```

### 1.3 Fundamentals

```sql
CREATE TABLE fundamentals (
    id                INTEGER PRIMARY KEY,
    symbol            TEXT NOT NULL,
    report_date       TEXT NOT NULL,
    pe_ratio          REAL,
    forward_pe        REAL,
    price_to_book     REAL,
    debt_to_equity    REAL,
    current_ratio     REAL,
    interest_coverage REAL,
    roe               REAL,
    market_cap        REAL,
    revenue_ttm       REAL,
    net_income_ttm    REAL,
    total_debt        REAL,
    cash              REAL,
    UNIQUE(symbol, report_date)
);
```

### 1.4 Computed Indicators (cached)

```sql
CREATE TABLE technical_indicators (
    id         INTEGER PRIMARY KEY,
    symbol     TEXT NOT NULL,
    date       TEXT NOT NULL,
    timeframe  TEXT NOT NULL,
    rsi_14     REAL,
    sma_20     REAL,
    sma_50     REAL,
    sma_200    REAL,
    bb_upper   REAL,
    bb_middle  REAL,
    bb_lower   REAL,
    bb_pct     REAL,
    UNIQUE(symbol, date, timeframe)
);
```

### 1.5 Screening Scores (cached)

```sql
CREATE TABLE screening_scores (
    id                INTEGER PRIMARY KEY,
    symbol            TEXT NOT NULL,
    computed_at       TEXT NOT NULL,
    price_score       REAL,
    bb_score          REAL,
    commodity_score   REAL,
    pe_score          REAL,
    balance_score     REAL,
    composite_score   REAL,
    UNIQUE(symbol, computed_at)
);
```

---

## 2. Project Structure

```
stocks-picker/
├── app/
│   ├── main.py                  # FastAPI app, router registration
│   ├── database.py              # SQLite connection, session factory
│   ├── models.py                # SQLAlchemy ORM models
│   ├── schemas.py               # Pydantic request/response schemas
│   ├── routers/
│   │   ├── sectors.py           # sector list + sector detail
│   │   ├── stocks.py            # stock detail + screening table
│   │   ├── charts.py            # PNG chart endpoint
│   │   └── refresh.py           # data refresh trigger
│   ├── services/
│   │   ├── data_fetcher.py      # yfinance wrapper
│   │   ├── indicators.py        # RSI, SMA, Bollinger Band computation
│   │   ├── scorer.py            # composite screening score
│   │   └── chart_builder.py     # matplotlib chart generator
│   ├── templates/
│   │   ├── base.html
│   │   ├── dashboard.html
│   │   ├── screener.html
│   │   └── stock_detail.html
│   └── static/
│       └── style.css
├── data/
│   ├── seed/
│   │   ├── tickers.csv
│   │   └── commodities.csv
│   └── stocks.db
├── scripts/
│   ├── seed_db.py
│   └── refresh_all.py
└── requirements.txt
```

### Key Dependencies

```
fastapi
uvicorn
sqlalchemy
yfinance
pandas
pandas-ta
matplotlib
mplfinance
jinja2
python-multipart
```

---

## 3. Workflows

### 3.1 Bootstrap (one-time)

```
scripts/seed_db.py
  1. Create all tables (via SQLAlchemy metadata.create_all)
  2. Read data/seed/tickers.csv  → insert sectors + tickers
  3. Read data/seed/commodities.csv → insert sector_commodities
```

### 3.2 Data Refresh

Triggered by `POST /refresh/{sector_id}` or `scripts/refresh_all.py`.

```
services/data_fetcher.py
  For each ticker in sector:
    yf.Ticker(symbol).history(period="10y")
      → resample to D / W / M
      → upsert into price_history
    yf.Ticker(symbol).info
      → extract PE, D/E, current ratio, etc.
      → upsert into fundamentals

  For each commodity in sector:
    yf.Ticker(commodity_symbol).history(period="10y")
      → resample to D / W / M
      → upsert into price_history

services/indicators.py
  For each (symbol, timeframe):
    Load price_history into DataFrame
    Compute via pandas_ta:
      RSI(14)
      SMA(20), SMA(50), SMA(200)
      Bollinger Bands(20, std=2)
      %B = (close - bb_lower) / (bb_upper - bb_lower)
    Upsert into technical_indicators

services/scorer.py
  compute_scores(sector_id)  → see §4
  Upsert into screening_scores
```

### 3.3 Chart Generation

```
GET /charts/{ticker}?tf=D

services/chart_builder.py
  1. Load price_history + technical_indicators
  2. Build matplotlib figure:
       Panel 1 (70%): candlestick + SMA lines + BB fill
       Panel 2 (30%): RSI + overbought/oversold lines
  3. Return PNG via StreamingResponse
```

---

## 4. Screening & Scoring Algorithm

All sub-scores 0–100. Higher = more attractive (cheaper / healthier).

### 4.1 Price Percentile Score (weight: 25%)

```python
percentile = percentileofscore(five_year_closes, current_close)
price_score = 100 * (1 - percentile / 100)
# At 5yr low → ~100.  At 5yr high → ~0.
```

### 4.2 Bollinger Band Score (weight: 25%)

```python
# %B: 0 = at lower band, 1 = at upper band, <0 = below lower
bb_score = max(0, min(100, 100 * (1 - bb_pct)))
```

### 4.3 Commodity Score (weight: 20%)

Same percentile logic as 4.1, applied to the sector's commodity price.
Multiple commodities per sector → average their scores.

### 4.4 P/E Score (weight: 15%)

```python
relative_pe = current_pe / median_5yr_pe
pe_score = max(0, min(100, 100 * (2 - relative_pe)))
# relative_pe 0.5 → 100, 1.0 → 100, 1.5 → 50, 2.0 → 0
```

If P/E unavailable (negative earnings) → score = 50 (neutral).

### 4.5 Balance Sheet Score (weight: 15%)

| Metric            | Scoring                             |
|-------------------|-------------------------------------|
| Debt/Equity       | `max(0, 100 - D/E * 50)`           |
| Current Ratio     | `min(100, current_ratio * 50)`      |
| Interest Coverage | `min(100, coverage * 20)`           |
| ROE               | `+10 bonus if ROE > 0`             |

`balance_score = mean(de, cr, ic) + roe_bonus`, clamped to [0, 100].

### 4.6 Composite

```python
composite = (0.25 * price_score
           + 0.25 * bb_score
           + 0.20 * commodity_score
           + 0.15 * pe_score
           + 0.15 * balance_score)
```

Ranked descending on the screener page.

---

## 5. API Endpoints

| Method | Path                          | Returns              |
|--------|-------------------------------|----------------------|
| GET    | `/`                           | Dashboard HTML       |
| GET    | `/sectors`                    | Sector list JSON     |
| GET    | `/sectors/{id}/screen`        | Screener HTML/JSON   |
| GET    | `/stocks/{ticker}`            | Detail HTML          |
| GET    | `/charts/{ticker}?tf=D\|W\|M` | PNG image            |
| POST   | `/refresh/{sector_id}`        | Triggers background refresh |
| GET    | `/health`                     | Health check JSON    |

---

## 6. Frontend Pages (Jinja2 Templates)

### Dashboard (`/`)
Sector cards, each showing name + last-refresh timestamp + "Screen Now" button.

### Screener (`/sectors/{id}/screen`)
Table sorted by composite_score desc:
`Rank | Ticker | Price | %B | RSI | P/E vs Hist | Balance | Commodity | Score`

### Stock Detail (`/stocks/{ticker}`)
- Three chart tabs: Daily · Weekly · Monthly (each an `<img>` from `/charts/`)
- Fundamentals panel: P/E, P/B, D/E, Current Ratio, Interest Coverage, ROE
- Score breakdown: each sub-score as a progress bar
- Commodity chart panel for the sector's underlying commodity

---

## 7. Chart Layout (Matplotlib)

```
┌──────────────────────────────────────────────────┐
│  XOM — Daily (2 years)                           │
│                                                  │
│  ░░ BB upper ────────────────────────────        │
│     SMA200 ┄┄┄┄┄┄┄┄ (red)                       │
│     SMA50  ──────── (orange)                     │
│  │█│ candles   SMA20 ──────── (blue)             │
│  ░░ BB lower ────────────────────────────        │
│                                       (70%)      │
├──────────────────────────────────────────────────┤
│  RSI(14)                                         │
│  ── 70 overbought                                │
│  ~~ RSI line                                     │
│  ── 30 oversold                                  │
│                                       (30%)      │
└──────────────────────────────────────────────────┘
```

---

## 8. Seed Data Examples

**tickers.csv**
```csv
symbol,name,exchange,sector_name
XOM,ExxonMobil,NYSE,Oil & Gas
CVX,Chevron,NYSE,Oil & Gas
COP,ConocoPhillips,NYSE,Oil & Gas
SLB,SLB (Schlumberger),NYSE,Oil & Gas
HAL,Halliburton,NYSE,Oil & Gas
OXY,Occidental Petroleum,NYSE,Oil & Gas
EOG,EOG Resources,NYSE,Oil & Gas
PXD,Pioneer Natural Resources,NYSE,Oil & Gas
FCX,Freeport-McMoRan,NYSE,Copper & Mining
SCCO,Southern Copper,NYSE,Copper & Mining
NEM,Newmont,NYSE,Gold Mining
GOLD,Barrick Gold,NYSE,Gold Mining
```

**commodities.csv**
```csv
sector_name,commodity_symbol,commodity_name
Oil & Gas,CL=F,WTI Crude Oil
Oil & Gas,NG=F,Natural Gas
Copper & Mining,HG=F,Copper Futures
Gold Mining,GC=F,Gold Futures
```

---

## 9. Implementation Order

1. DB schema + seed scripts — `models.py`, `database.py`, `seed_db.py`
2. Data fetcher — yfinance wrapper for price + fundamentals ingestion
3. Indicator calculator — pandas-ta pipeline
4. Scorer — sub-scores + composite
5. Chart builder — matplotlib figure factory
6. FastAPI routers — wire services to endpoints
7. Templates — dashboard → screener → stock detail
8. Async refresh — `BackgroundTasks` for non-blocking data refresh
