# Loom

Loom is a research tool for equity investors. It collects data from multiple sources for a set of tracked companies, extracts the information relevant to an investment decision, and presents it as a small number of ranked, source-linked signals, to serve as actionable information.

Loom does not forecast stock prices or issue buy or sell recommendations. Its purpose is to reduce the time required to review primary source material and to surface changes that would otherwise go unnoticed.

## Data sources

- News articles
- SEC filings
- Earnings call transcripts
- Quarterly earnings reports

## Core functions

- Maintains a watchlist of companies and a timeline of ingested documents for each one. Any valid ticker can be added directly. Loom resolves it and starts ingesting its filings automatically, with no manual setup required.
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

Requires Python 3.12, Node.js, and Docker Desktop running. Newer Python versions are not yet supported: some dependencies (pydantic-core, in particular) ship compiled extensions that do not build on Python 3.14 yet. Run `python3.12 --version` first to confirm it is installed; if not, install it before continuing.

```bash
docker compose up -d
cp .env.example backend/.env
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

Open `backend/.env` and set `SEC_EDGAR_USER_AGENT` to a string identifying you, for example `"Loom your-name your-email@example.com"`. SEC requires a genuine identifying User-Agent on every request; the app runs without this step, but requests should not be sent under a placeholder value.

Open a second terminal and run the frontend:

```bash
cd frontend
npm install
npm run dev
```

## Usage

Open `http://localhost:5173`. A default watchlist is created automatically on first load.

To track a company, type its ticker into the field on the watchlist page and click "Add ticker." Loom resolves it against SEC's public company directory, adds it, and starts ingesting its filings in the background. This takes anywhere from a few seconds to about two minutes, depending on how many filings the company has on record.

Click a ticker to open its detail page. The timeline lists filings as they are ingested, most recent first, each linking to the source document on sec.gov. A ticker with no filings yet shows a status message until ingestion finishes; the page refreshes on its own.

Click "Remove" next to a ticker on the watchlist page to stop tracking it.

`backend/scripts/seed_companies.py` is an optional shortcut that pre-populates AAPL and MSFT; it is not required for normal use.

## Directory structure

```
loom/
├── docker-compose.yml       # Postgres
├── backend/                 # FastAPI + SQLAlchemy + Alembic
└── frontend/                 # Vite + React + TypeScript
```
