# Loom

Loom is a research tool for equity investors. It collects everything a company publishes, reads it, and reduces it to a single plain-language verdict per company: which way the evidence leans, the two or three things driving that, and what has changed since you last looked.

The purpose is to save you reading primary source material. If you have to already know what a 10-Q is to understand the output, it has failed.

Loom does not forecast prices or issue buy/sell recommendations. Nothing in it prices a stock or knows your position, so a recommendation would be fabricated authority. It gives you the inputs to a decision at the moment they matter, and leaves the decision to you.

## What it does

**Reads and reduces**

- One verdict per company: a stance, a plain sentence explaining it, the drivers behind it, and what is new since the last read. This is the main output; everything else supports it.
- Ranks companies worst-first, split into "needs a look" and "nothing to act on".
- Every finding links back to the document and the verbatim sentence that produced it.

**Compares over time**

- Annual reports against the previous year, on risk factors.
- Quarterly reports against the previous quarter, on management's discussion of results.
- Across documents inside a short window, so a risk disclosed in a filing and then confirmed on an earnings call a week later is reported as one developing story rather than two unrelated findings.

**Tracks non-prose data**

- Insider share transactions, separating genuine open-market trades from routine vesting and option exercises. Most reported "insider selling" is tax withholding on vesting shares; Loom does not count it as a decision to sell.
- Earnings dates and consensus estimates, surfaced above everything else as a date approaches.
- Threshold rules over that data (for example several insiders selling in the same fortnight) that run without a language model at all.

**Everything else**

- Full-text search across every ingested document, showing the passage that matched.
- Price charts from 1H to 1Y, auto-rotating through the watchlist.
- A dense screener view for comparing companies side by side.
- Refreshes on a schedule so the dashboard stays current without you running anything.

## Data sources

All free. No paid API is used anywhere.

| Source | What it provides | Key needed |
|---|---|---|
| SEC EDGAR | Filings (10-K, 10-Q, 8-K) and their exhibits, insider transactions (Form 4) | No, just a User-Agent |
| Finnhub (free tier) | Company news, earnings dates and estimates | Yes, free |
| Google Gemini (free tier) | The language work: extraction, comparison, synthesis | Yes, free |
| Motley Fool | Earnings call transcripts (scraped, robots.txt respected) | No |
| Yahoo | Price history | No |

## Requirements

- **Python 3.12.** Not 3.13 or 3.14: some dependencies ship compiled extensions that do not build on newer versions yet. Check with `python3.12 --version`.
- **Node.js 20+**
- **Docker Desktop**, running. Postgres runs in a container.

## Setup

**1. Get two free API keys.** Both take about a minute.

- Gemini: https://aistudio.google.com/apikey
- Finnhub: https://finnhub.io/register

**2. Start the database.**

```bash
docker compose up -d
```

**3. Configure.**

```bash
cp .env.example backend/.env
```

Open `backend/.env` and set four values:

```
GEMINI_API_KEY=your-key-here
FINNHUB_API_KEY=your-key-here
SEC_EDGAR_USER_AGENT="Loom research-tool your-name your-email@example.com"
SCRAPER_USER_AGENT="Loom research-tool your-name your-email@example.com"
```

The two User-Agent strings are not optional. SEC requires a genuine identifying header on every request under its fair-access policy, and the transcript scraper sends one for the same reason. Put your real name and email in them.

**4. Install and run the backend.**

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

**5. In a second terminal, run the frontend.**

```bash
cd frontend
npm install
npm run dev
```

Open the URL Vite prints, normally `http://localhost:5173`. If that port is taken it will bind the next free one and print that instead; either works.

## First run

The app starts empty. Type a ticker into the box at the top right and press add. Loom resolves it against SEC's public company directory, then starts pulling its filings, transcripts, news, insider records, and earnings dates in the background.

Ingestion takes anywhere from a few seconds to about two minutes depending on how much the company has filed. The page updates itself as data arrives.

Analysis then runs automatically, and this is the slow part. Reading filings costs one AI request each, and Gemini's free tier allows roughly 20 requests per minute. A company with a full set of filings takes a few minutes to work through. Loom paces its own requests to stay under the limit and picks up where it left off if it is interrupted.

Until a company has been analysed, its card honestly says "not enough data yet" rather than pretending to a view.

## Things worth knowing

**The free AI tier is the main constraint.** Analysis is rate limited to about 20 requests per minute and has a daily ceiling. Loom handles this gracefully: it paces requests, retries transient failures, falls back across models, and stops cleanly when quota is exhausted rather than failing loudly. But a full watchlist takes more than one sitting to analyse. If you have a paid key, set `LLM_MIN_CALL_INTERVAL_SECONDS=0` in `.env` to remove the pacing.

**It works without an AI key, partially.** Ingestion, search, insider tracking, earnings dates, price charts, and the threshold rules all run with no language model. Only the reading and synthesis need one. Leaving `GEMINI_API_KEY` blank gives you a working document and data browser.

**Leaving `FINNHUB_API_KEY` blank** disables news and earnings dates. Filings and transcripts still work.

**The scheduler** re-ingests and re-analyses the whole watchlist every 6 hours by default. Set `SCHEDULER_ENABLED=false` if you would rather trigger analysis by hand, which is worth doing while you are actively developing.

## Configuration

Everything lives in `backend/.env`. See `.env.example` for the full list with comments.

| Setting | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | empty | Enables analysis |
| `FINNHUB_API_KEY` | empty | Enables news and earnings dates |
| `SEC_EDGAR_USER_AGENT` | placeholder | Required by SEC, set it to something real |
| `SCRAPER_USER_AGENT` | placeholder | Sent by the transcript scraper |
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic` |
| `LLM_MIN_CALL_INTERVAL_SECONDS` | `6.5` | Request pacing; set 0 on a paid tier |
| `SCHEDULER_ENABLED` | `true` | Background refresh |
| `SCHEDULER_INTERVAL_MINUTES` | `360` | How often it refreshes |
| `DATABASE_URL` | local Postgres | Matches docker-compose |
| `BLOB_STORE_DIR` | `./data/blobs` | Where document text is stored on disk |

## Design

- **Adapters per source.** Each data source is one class producing plain data. Adding a source means writing one adapter and registering it; nothing else changes.
- **Repositories per table.** Only repositories touch the database. Routes and the engine call them.
- **Storage split by responsibility.** Postgres holds metadata and relationships; document text goes to a `BlobStore` behind an interface, so it can move to object storage without touching ingestion or the engine.
- **Deterministic wherever a fixed rule applies.** The language model is used only for genuine language judgement: sentiment, quote selection, explaining what changed. Deterministic gates also decide *whether* a model call is worth making, so cost tracks real signal rather than document volume. The verdict itself is computed, not generated, which is why it still works when the AI quota is gone.
- **Scrapers behave.** robots.txt is checked before every request, requests are rate limited per domain, and the User-Agent identifies Loom honestly rather than impersonating a browser.

## Development

```bash
cd backend && source .venv/bin/activate
pytest                  # 137 tests
ruff check .            # lint
alembic check           # confirms models and migrations agree
```

```bash
cd frontend
npx tsc --noEmit        # type check
npm run build           # production build
```

## Project layout

```
loom/
├── docker-compose.yml        # Postgres
├── backend/
│   ├── app/
│   │   ├── ingestion/        # one adapter per data source
│   │   ├── engine/           # extraction, comparison, rules, synthesis
│   │   ├── repositories/     # the only code touching the database
│   │   ├── api/routes/       # thin HTTP layer
│   │   └── models/           # SQLAlchemy tables
│   └── alembic/              # migrations
├── frontend/src/
│   ├── components/           # presentational only, never fetch
│   ├── hooks/                # data fetching
│   ├── api/                  # HTTP client
│   └── pages/                # compose hooks and components
└── docs/plan.md              # design decisions and why
```
