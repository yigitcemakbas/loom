# Loom

Loom is an equity research system. It ingests the material a public company produces, evaluates it, and reduces the result to a single assessment per company: the direction the evidence points, the factors driving that direction, and what has changed since the previous assessment.

The objective is to reduce the time required to review primary source material. Output is written for a reader who does not work in finance; terminology that assumes prior knowledge of filing structure is treated as a defect.

Loom does not forecast prices and does not issue buy or sell recommendations. The system contains no pricing model and no knowledge of the reader's position, so any recommendation would assert an authority it does not have. It presents the inputs to a decision and leaves the decision to the reader.

## Capabilities

### Assessment

- A single assessment per company, comprising a stance, a one-sentence rationale, the two or three factors driving it, and a summary of what has changed since the previous assessment.
- Companies are ordered by severity and separated into those requiring attention and those that do not.
- Every finding resolves to its source document and the verbatim passage that produced it.
- Assessment is computed deterministically from stored findings. It does not require a language model and remains available when the model provider is unreachable.

### Comparison

- Annual reports are compared against the prior year on risk factors.
- Quarterly reports are compared against the prior quarter on management's discussion of results.
- Disclosures occurring within a short window are evaluated jointly, so a risk disclosed in a filing and subsequently confirmed on an earnings call is reported as a single development rather than two independent findings.

### Structured data

- Insider transactions, with discretionary open-market trades separated from routine vesting, option exercises, and tax withholding. The latter categories are excluded from any conclusion about insider intent.
- Scheduled earnings dates and consensus estimates, promoted in the interface as a reporting date approaches.
- Threshold rules over structured data, such as multiple insiders selling within a defined window. These execute without a language model.

### Interface

- Full-text search across all ingested documents, returning the matching passage.
- Price history across intervals from one hour to one year.
- A comparison table presenting all tracked companies against sortable quantitative columns.
- Scheduled background refresh.

## Data sources

All sources are free of charge. No paid API tier is used.

| Source | Provides | Credential |
|---|---|---|
| SEC EDGAR | Filings (10-K, 10-Q, 8-K) with exhibits; insider transactions (Form 4) | None; identifying User-Agent required |
| Finnhub | Company news; earnings dates and consensus estimates | Free API key |
| Google Gemini | Extraction, comparison, and synthesis | Free API key |
| Motley Fool | Earnings call transcripts, retrieved under robots.txt | None |
| Yahoo | Price history | None |

## Requirements

Docker Desktop. No other software is required.

## Installation

```bash
git clone https://github.com/yigitcemakbas/loom.git
cd loom
docker compose up
```

The initial build takes several minutes. Subsequent starts complete in seconds.

The application is served at `http://localhost:5173`.

Three containers are started: PostgreSQL, the backend API, and the frontend. Database migrations are applied automatically at startup. No further configuration is required to run the system.

## Operation

The watchlist is initially empty. Entering a ticker resolves it against the SEC company directory and begins retrieving its filings, earnings call transcripts, insider transactions, and price history.

Retrieval of a company's full filing history takes several minutes. SEC rate limits constrain throughput, and the interface updates as records arrive.

Selecting a company presents its assessment, findings, insider record, price history, and complete document text.

## Credentials

Loom operates without credentials. Two capabilities require one, both free of charge. The interface reports which are absent.

| Capability | Credential | Registration |
|---|---|---|
| Reading and summarising filings | `gemini_api_key` | https://aistudio.google.com/apikey |
| Company news and earnings dates | `finnhub_api_key` | https://finnhub.io/register |

Credentials are supplied as files in the `secrets/` directory, one file per credential, containing the credential value only:

```bash
echo -n "your-gemini-key"  > secrets/gemini_api_key
echo -n "your-finnhub-key" > secrets/finnhub_api_key
docker compose restart backend
```

The directory is excluded from version control and mounted read-only into the backend container at `/run/secrets`. Values are read at process start.

Credentials are supplied as files rather than environment variables. An environment variable is reproduced in `docker inspect` output and in `/proc/1/environ`, and is therefore readable by any process with access to the Docker socket. A mounted file is read only by the process that requires it. Environment variables remain supported and take precedence, which accommodates continuous integration and short-lived runs.

Changing a credential requires `docker compose restart backend`. A rebuild is not required.

### Operation without credentials

Without a Gemini credential, Loom collects and presents source material but does not evaluate it. Filings, transcripts, insider records, price history, and full-text search are available; assessments are not. As assessment is the system's primary output, this credential is the one of consequence.

Without a Finnhub credential, company news and earnings dates are unavailable. No other capability is affected.

### SEC identification

SEC enforces its fair-access policy through the User-Agent header. The header must contain a contact email address and must not contain a URL; requests that do not comply are refused. The supplied default satisfies these constraints. Operators making sustained use of the system should substitute their own contact details in a `.env` file adjacent to `docker-compose.yml`:

```
SEC_EDGAR_USER_AGENT="Name email@example.com"
SCRAPER_USER_AGENT="Name email@example.com"
```

## Operational characteristics

**Analysis throughput is bounded by the free model tier.** Gemini's free tier permits approximately 20 requests per minute against a daily ceiling; evaluating one document consumes one request. Loom paces requests to remain within the limit, retries transient failures, falls back across models, and terminates cleanly on quota exhaustion. Evaluating a full watchlist therefore spans more than one session. On a paid tier, set `LLM_MIN_CALL_INTERVAL_SECONDS=0` to remove pacing.

**Background refresh is disabled by default under Docker.** An initial run does not consume model quota until explicitly enabled. Set `SCHEDULER_ENABLED=true` to enable periodic re-ingestion and re-evaluation.

**Companies without sufficient evaluated material report that state explicitly** rather than presenting an assessment unsupported by evidence.

## Configuration

Under Docker, configuration is read from a `.env` file adjacent to `docker-compose.yml`. Outside Docker, from `backend/.env`. Credentials are read from `secrets/`. `.env.example` documents the full set.

| Setting | Default | Purpose |
|---|---|---|
| `gemini_api_key` | empty | Analysis. Supplied via `secrets/gemini_api_key` |
| `finnhub_api_key` | empty | News and earnings data. Supplied via `secrets/finnhub_api_key` |
| `SEC_EDGAR_USER_AGENT` | project default | SEC identification |
| `SCRAPER_USER_AGENT` | project default | Transcript retrieval identification |
| `LLM_PROVIDER` | `gemini` | `gemini` or `anthropic` |
| `LLM_MIN_CALL_INTERVAL_SECONDS` | `6.5` | Request pacing; `0` disables |
| `SCHEDULER_ENABLED` | `false` under Docker | Background refresh |
| `SCHEDULER_INTERVAL_MINUTES` | `360` | Refresh interval |
| `DATABASE_URL` | set by compose | Required only outside Docker |
| `BLOB_STORE_DIR` | `data/blobs` | Document text storage location |

## Architecture

**Backend.** Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16.

**Frontend.** React 19, TypeScript, Vite, TanStack Query, served by nginx with a same-origin proxy to the API.

**Design constraints.**

- *Source adapters.* Each data source is a single class producing plain data structures. Adding a source requires implementing one adapter and registering it; no other component changes.
- *Repository isolation.* Database access is confined to repository classes. Routes and the analysis engine do not issue queries.
- *Storage separation.* PostgreSQL holds metadata and relationships. Document text is written through a `BlobStore` interface, permitting relocation to object storage without modifying ingestion or analysis. Stored references are relative, so the data set is portable across hosts and containers.
- *Deterministic by default.* The language model is applied only where linguistic judgement is required: sentiment, passage selection, and characterising change. Deterministic gates additionally decide whether a model call is warranted, so cost tracks material signal rather than document volume. The assessment layer is fully deterministic.
- *Retrieval conduct.* robots.txt is evaluated before each request, requests are rate limited per domain, and the User-Agent identifies the system rather than impersonating a browser.

## Development

Running the services directly requires Python 3.12 and Node.js 20 or later. Python 3.13 and 3.14 are not supported; several dependencies distribute compiled extensions that do not build against them.

```bash
docker compose up -d postgres
cp .env.example backend/.env

cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

```bash
cd frontend
npm install
npm run dev
```

### Verification

```bash
cd backend && source .venv/bin/activate
pytest                  # 143 tests
ruff check .
alembic check           # confirms models and migrations agree
```

```bash
cd frontend
npm run build           # type check and production build
```

## Repository structure

```
loom/
├── docker-compose.yml        # PostgreSQL, backend, frontend
├── secrets/                  # credential files, excluded from version control
├── backend/
│   ├── app/
│   │   ├── ingestion/        # source adapters
│   │   ├── engine/           # extraction, comparison, rules, assessment
│   │   ├── repositories/     # database access
│   │   ├── api/routes/       # HTTP layer
│   │   └── models/           # schema definitions
│   └── alembic/              # migrations
├── frontend/src/
│   ├── components/           # presentation
│   ├── hooks/                # data access
│   ├── api/                  # HTTP client
│   └── pages/                # composition
└── docs/plan.md              # design record
```
