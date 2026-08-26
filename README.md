# Loom

Loom is a research tool for equity investors. It collects data from multiple sources for a set of tracked companies, extracts the information relevant to an investment decision, and presents it as a small number of ranked, source-linked signals, to serve as actionable information.

Loom does not forecast stock prices or issue buy or sell recommendations. Its purpose is to reduce the time required to review primary source material and to surface changes that would otherwise go unnoticed.

## Data sources

- SEC filings (10-K, 10-Q, 8-K), including the exhibits attached to them, which is where an 8-K's earnings press release actually lives
- Earnings call transcripts
- News articles, filtered to items that are genuinely about the company rather than merely mentioning it
- Insider share transactions (SEC Form 4), separating genuine open-market trades from routine vesting and option exercises

## Core functions

- Produces a single read per company: a plain-language verdict, the two or three things driving it, and what is new since you last looked. This is the product's main output; everything else exists to support it.
- Maintains a watchlist of companies and a timeline of ingested documents for each one. Any valid ticker can be added directly. Loom resolves it and starts ingesting its filings automatically, with no manual setup required.
- Reads filings and transcripts in full and extracts sentiment, risk factors, notable quotes, and quarter over quarter changes.
- Compares each filing against the previous one of its kind: annual reports on risk factors, quarterly reports on management's discussion of results, so changes are caught at the cadence they happen rather than only once a year.
- Tracks when each company is due to report, what the market expects of it, and surfaces that above everything else as the date approaches.
- Distinguishes new information from information that repeats across prior filings.
- Synthesises across documents, not just within them. When several disclosures land inside a short window and together change the picture, Loom says what the combination means rather than leaving the reader to join up separate signals.
- Characterises the likely market reaction to each finding in qualitative terms (direction, magnitude, time horizon). It never states a percentage move or price target, because nothing in the pipeline is a pricing model.
- Links each signal to its source document and the exact quote that supports it.
- Applies plain arithmetic rules to filed data, such as several insiders selling in the same fortnight. These need no language model, so they keep working when the AI service is unavailable.
- Ranks signals by priority so the most significant items appear first.
- Searches the full text of every ingested document, not just titles and dates, and shows the passage that matched.
- Refreshes on a schedule, so the dashboard reflects current filings without anyone running a command.

## System design

- Each data source is handled by a dedicated adapter responsible for fetching data and returning clean text. Adding a new source does not require changes to other components.
- Raw content is stored as an object separate from its metadata. Metadata is stored in the database.
- Extraction logic is deterministic wherever a fixed rule applies. The language model is used only for tasks that require judgment: sentiment analysis, quote selection, and explanation of changes. Deterministic gates also decide *whether* a model call is warranted, so cost stays proportional to actual signal rather than to document volume.
- Each component has a single responsibility and does not access another component's internal state.
- Scrapers obey `robots.txt` on every request, rate limit per domain, and send a User-Agent that identifies Loom honestly rather than impersonating a browser.

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

Open `backend/.env` and set `SEC_EDGAR_USER_AGENT` and `SCRAPER_USER_AGENT` to strings identifying you, for example `"Loom your-name your-email@example.com"`. SEC requires a genuine identifying User-Agent on every request, and the transcript scraper sends one for the same reason; the app runs without this step, but requests should not be sent under a placeholder value.

Two settings are optional:

- `FINNHUB_API_KEY` enables the company-news source ([free tier](https://finnhub.io/register)). Left blank, Loom skips news and ingests filings and transcripts normally.
- `SCHEDULER_ENABLED` (default `true`) re-ingests and re-analyses the watchlist every `SCHEDULER_INTERVAL_MINUTES` (default 360). Set it to `false` if you would rather trigger analysis by hand, for instance to stay inside a metered LLM tier.

Open a second terminal and run the frontend:

```bash
cd frontend
npm install
npm run dev
```

## Usage

Open the URL Vite prints on startup, normally `http://localhost:5173`. If that port is already taken, Vite binds the next free one (5174, 5175, and so on) and prints that instead; the app works on any of them. A default watchlist is created automatically on first load.

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
