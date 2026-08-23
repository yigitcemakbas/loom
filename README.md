# Loom

Loom is a research tool for equity investors. It collects data from multiple sources for a set of tracked companies, extracts the information relevant to an investment decision, and presents it as a small number of ranked, source-linked signals, to serve as actionable information.

Loom does not forecast stock prices or issue buy or sell recommendations. Its purpose is to reduce the time required to review primary source material and to surface changes that would otherwise go unnoticed.

## Data sources

- News articles
- SEC filings
- Earnings call transcripts
- Quarterly earnings reports

## Core functions

- Maintains a watchlist of companies and a timeline of ingested documents for each one.
- Reads filings and transcripts in full and extracts sentiment, risk factors, notable quotes, and quarter over quarter changes.
- Distinguishes new information from information that repeats across prior filings.
- Links each signal to its source document and the exact quote that supports it.
- Ranks signals by priority so the most significant items appear first.

## System design

- Each data source is handled by a dedicated adapter responsible for fetching data and returning clean text. Adding a new source does not require changes to other components.
- Raw content is stored as an object separate from its metadata. Metadata is stored in the database.
- Extraction logic is deterministic wherever a fixed rule applies. The language model is used only for tasks that require judgment: sentiment analysis, quote selection, and explanation of changes.
- Each component has a single responsibility and does not access another component's internal state.

## Installation

```bash
docker compose up -d
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
python -m scripts.seed_companies
python -m scripts.ingest_once --ticker AAPL
uvicorn app.main:app --reload
```

Open a second terminal and run the frontend:

```bash
cd frontend
npm install
npm run dev
```

## Directory structure

```
loom/
├── docker-compose.yml       # Postgres
├── backend/                 # FastAPI + SQLAlchemy + Alembic
└── frontend/                 # Vite + React + TypeScript
```
