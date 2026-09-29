# Loom

Loom is an equity research system. It ingests the material a public company produces, evaluates it against measured expectations, and reduces the result to a small number of reviewable outputs: what changed, what the evidence supports, where the evidence disagrees with itself, and how the company ranks against comparable peers.

The objective is to reduce the time required to review primary source material. Every conclusion resolves to the source document and the verbatim passage that produced it. Output is written to be as digestible as possible, for investors of any level; terminology that assumes prior knowledge of filing structure is treated as a defect.

Loom contains no pricing model, and issues no buy or sell recommendations. Loom isn't supposed to replace the trader; it tries to improve the quality of information entering a human decision.

Loom is consumed three ways: a web interface for human readers, an authenticated HTTP API for programmatic access, and an evidence API designed for language model agents.

---

## What Loom produces

### Assessment

A single assessment per company, comprising a stance, a one-sentence rationale, the findings driving it, and a summary of what changed since the previous assessment. Companies are ordered by severity and separated into those requiring attention and those that do not.

Assessment is computed deterministically from stored findings. It requires no language model call and remains available when the model provider is unreachable.

Where the evidence is insufficient to support a direction, Loom reports that state and names what is missing.

### Findings

Extracted disclosures, each carrying a verbatim quote, its source document, a materiality grade, and a direction. Direction describes what the document did: negative where a risk appeared, positive where one was withdrawn or the company's own tone improved, unassessed where the document stated no direction. Direction is a property of the disclosure and carries no forecast about the share price.

Each finding also carries how it was established, from a deterministic comparison of two filings through to a language judgement about tone. This allows a reader or a downstream agent to weight findings by how far they can be independently checked.

### Filing comparison

Annual reports are compared against the prior year on risk factors. Quarterly reports are compared against the prior quarter on management's discussion of results. The comparison runs in both directions in one pass, so a withdrawn disclosure is reported alongside a new one.

Candidate paragraphs are ranked by novelty against the filer's own prior filing, with language rarity against the full stored corpus as a secondary term. Novelty against the company's own history is weighted above corpus rarity, so a risk written in the industry's standard vocabulary remains visible in the year it first appears for a given filer.

### Disclosure norms

Loom measures what a document of a given type, sector, and genre normally contains, then reports how far a specific document departs from that baseline. Expectations are built with hierarchical empirical Bayes shrinkage across company, sector, genre, and document type, so a thinly observed group inherits its parent's expectation rather than overfitting its own.

Two independent baselines are maintained. One is measured from extracted findings. The second is measured from raw filing text across every company held, which allows it to disagree with the extractor rather than confirm it.

### Contradictions

Where Loom's own sources disagree about the same company, the disagreement is served directly with both sides named and sourced. Contradictions are reported separately from the assessment so that a tension between two sources is not averaged into a single stance.

### Case file

A per-company argument assembling findings, factor ranks, price context, disclosed dependencies, and staleness into one ordered structure, with each point weighted and attributed.

### Quantitative layer

A factor library over reported financials, with cross-sectional ranking against sector peers, composite folding, and percentile output. The layer includes its own backtest and measures whether the factors rank returns before their scores are used. Composite scores are withheld where the constituent coverage is too thin to support one.

### Evidence API

A dedicated surface for language model agents, served under `/v1/evidence`. It provides Loom's findings, contradictions, filing changes, peer ranks, observed and expected disclosure counts, dependency edges, and coverage depth. It does not serve the assessment.

The omission is structural. The module implements no path to the assessment layer, and the packet schema types the field so that populating it is a validation error. Endpoints accept an `as_of` parameter and return only what was on file at that date, which allows an agent to evaluate its own past reasoning against the evidence available at the time.

Disclosure volume is served as observed and peer-expected counts rather than as a derived residual, leaving the comparison to the caller. Every response states how much Loom has read for the company, so the depth behind a packet is explicit.

Endpoints, parameters, and worked examples are documented under [Evidence API reference](#evidence-api-reference).

### Structured data

- Insider transactions, with discretionary open-market trades separated from routine vesting, option exercises, and tax withholding. The latter categories are excluded from any conclusion about insider intent.
- Scheduled earnings dates and consensus estimates, promoted in the interface as a reporting date approaches.
- Threshold rules over structured data, such as multiple insiders selling within a defined window. These execute without a language model.
- A dependency graph built from companies naming each other in their own filings, used to identify read-across candidates when one of them files.

### Interface

- Full-text search across all ingested documents, returning the matching passage.
- Price history across intervals from one hour to one year.
- A comparison table presenting all tracked companies against sortable quantitative columns.
- A running event feed, a daily view, a risk tracker, and a portfolio view.

---

## How Loom works

### Pipeline

Ingestion adapters retrieve documents and structured records. Section extraction is deterministic. Deterministic gates then decide whether a document warrants a model call, so model cost tracks material signal rather than document volume. Extraction makes one structured call per document. Findings are written by a single module, ranked deterministically, clustered across short windows so that a risk disclosed in a filing and confirmed on a later earnings call reports as one development, and synthesised into an assessment without a further model call.

### Point-in-time correctness

Financial series resolve to the figure that was on file as of a requested date, distinguishing filing date from period end so that a later restatement does not alter a historical view. Price history reads strictly backwards from a given date. Factor scores are stored dated rather than recomputed, so a query about a past date returns the ranking that existed then.

### Real-time path

A standing prior is computed per company in advance, describing what would be material about that company if it filed. A separate loop polls the SEC acceptance feed and joins new filings against those priors.

The cycle is ordered so that expensive work only occurs for filings that survive every cheap filter: one feed request covering the entire market, rejection of anything already assessed from a single query, rejection of anything outside the tracked universe from an in-memory index, and only then retrieval of the filing text and scoring against the stored prior. Latency is bounded by the poll interval rather than by the work performed.

### Measurement and feedback

Loom measures whether its own findings predicted anything, using benchmark-adjusted returns, entry strictly after the event, and one directional call per company per day to avoid counting correlated findings as independent evidence. Significance is assessed with Newey-West standard errors and multiple-testing correction.

Those measurements feed back into finding ranking through a bounded multiplier per finding type. A type's multiplier remains at unity until it clears a sample floor and a t-statistic threshold, and moves within a fixed band once it does. Model judgements are persisted whether accepted or rejected, which retains the negative examples required to evaluate the extraction layer.

### Scheduled work

Eleven background jobs run on independent intervals, staggered at startup. Watchlist re-ingestion and re-assessment run on a configurable timer. Filing backfill runs hourly and is bounded per run, prioritising companies whose corpus is too shallow for year-over-year comparison. Price refresh and digests run daily and hourly. Prior replay runs six-hourly. Factor scoring, corpus rebuild, reliability re-measurement, and expectation tables run weekly.

---

## Data sources

All sources are free of charge. No paid API tier is used.

| Source | Provides | Credential |
|---|---|---|
| SEC EDGAR | Filings (10-K, 10-Q, 8-K) with exhibits; insider transactions (Form 4); full-text search; acceptance feed | None; identifying User-Agent required |
| Finnhub | Company news; earnings dates and consensus estimates | Free API key |
| Google Gemini | Extraction, comparison, and synthesis | Free API key |
| Motley Fool | Earnings call transcripts, retrieved under robots.txt | None |
| Yahoo | Price history | None |

---

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

Selecting a company presents its assessment, findings, contradictions, insider record, factor ranks, price history, and complete document text.

---

## API access

The backend is served at `http://localhost:8000`. Interactive documentation is generated from the route definitions and available at `/docs`, with the raw specification at `/openapi.json`.

### Authentication

Loom authenticates with a bearer token. Tokens are issued on sign-in and are valid for 90 days.

Create an account, which sends a six-digit verification code to the supplied address:

```bash
curl -X POST http://localhost:8000/auth/signup \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","username":"you","password":"your-password"}'
```

Exchange the code for a token:

```bash
curl -X POST http://localhost:8000/auth/verify \
  -H 'Content-Type: application/json' \
  -d '{"email":"you@example.com","code":"123456"}'
```

```json
{ "token": "...", "email": "you@example.com", "username": "you" }
```

Subsequent sign-ins accept either the email address or the username:

```bash
curl -X POST http://localhost:8000/auth/signin \
  -H 'Content-Type: application/json' \
  -d '{"identifier":"you","password":"your-password"}'
```

Present the token on every request:

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/auth/me
```

`POST /auth/request-code` reissues a code. `POST /auth/logout` invalidates the token immediately.

**Retrieving the verification code.** The compose stack includes a local mail catcher. Codes are held rather than delivered, and are readable at `http://localhost:8025`. If an SMTP server is configured, the code is delivered normally. If neither is available, the code is written to the backend log and can be read with `docker compose logs backend | tail -20`.

### Access control

| Route group | Token |
|---|---|
| `/v1/evidence/*` | Required on every endpoint |
| `/positions`, `/digest` | Required |
| `/auth/me`, `/auth/logout` | Required |
| `/admin/*` | Required, admin account |
| Research read routes (companies, signals, briefs, factors, documents, tape, changes, priors, exposure) | Optional; responses personalise when a token is present |
| `/health`, `/status`, `/capabilities` | Open |

### Evidence API reference

The agent-facing surface. Every endpoint requires a token and accepts `as_of` as an ISO date, which restricts the response to what was on file at the end of that day.

| Endpoint | Parameters | Returns |
|---|---|---|
| `GET /v1/evidence/capabilities` | | Machine-readable description of the surface, the fields it withholds, and how each finding kind is established |
| `GET /v1/evidence/coverage` | `as_of`, `limit` (50) | Companies held, ordered by reading depth |
| `GET /v1/evidence/{ticker}` | `as_of`, `findings_limit` (40) | Full packet: coverage, findings, contradictions, disclosure volume, peer ranks, dependents |
| `GET /v1/evidence/{ticker}/findings` | `as_of`, `limit` (50), `offset` (0), `kind` | Paginated findings |
| `GET /v1/evidence/{ticker}/changes` | `as_of`, `section` (`1A`) | Paragraphs added to and withdrawn from the latest filing |

`kind` accepts one of `new_risk_factor`, `resolved_risk_factor`, `qoq_anomaly`, `guidance_change`, `insider_activity`, `short_interest_spike`, `emerging_pattern`, `notable_quote`, `sentiment_shift`. An unrecognised value returns 400 with the valid set, so a misspelled filter does not present as an absence of findings.

Retrieve a company packet as it stood on a past date:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/v1/evidence/AAPL?as_of=2026-06-30&findings_limit=10"
```

Retrieve risk factor additions only:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/v1/evidence/AAPL/findings?kind=new_risk_factor&limit=5"
```

```json
{
  "ticker": "AAPL",
  "offset": 0,
  "returned": 1,
  "findings": [
    {
      "id": "12f2d679-530b-4d68-bb22-b2255138cd92",
      "kind": "new_risk_factor",
      "direction": "negative",
      "occurred_at": "2026-07-31T00:00:00Z",
      "summary": "European regulatory delays on artificial intelligence features...",
      "quote": "Interoperability and other requirements have in the past, and may in the future, cause the Company to not launch or maintain products, services and features, such as Siri AI, in certain jurisdictions.",
      "source": {
        "document_id": "30cddec1-033c-4817-ba30-0ca0360757d6",
        "compared_document_id": null,
        "form": "10-Q",
        "published": "2026-07-31",
        "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/aapl-20260627.htm"
      },
      "materiality": "minor",
      "confidence": 0.9,
      "how_established": "deterministic comparison of two filings; checkable against the source text"
    }
  ]
}
```

Agents should begin at `/v1/evidence/capabilities`, which describes the surface and its omissions without requiring this document.

### Application API

The interface is built on the same HTTP API, which is available directly.

| Group | Endpoints |
|---|---|
| Companies | `GET /companies`, `GET /companies/{ticker}`, `GET /companies/{ticker}/timeline` |
| Assessment | `GET /briefs`, `GET /companies/{ticker}/brief`, `GET /companies/{ticker}/brief/horizon`, `POST /companies/{ticker}/brief/refresh` |
| Findings | `GET /signals`, `GET /signals/{id}`, `POST /signals/{id}/dismiss`, `POST /signals/{id}/note`, `POST /companies/{ticker}/analyze` |
| Contradictions | `GET /companies/{ticker}/contradictions` |
| Case file | `GET /companies/{ticker}/case` |
| Changes | `GET /changes` |
| Quantitative | `GET /factors/leaderboard`, `GET /companies/{ticker}/factors` |
| Facts | `GET /companies/{ticker}/facts`, `GET /companies/{ticker}/insider-activity` |
| Earnings | `GET /earnings/upcoming`, `GET /companies/{ticker}/earnings` |
| Documents | `GET /documents`, `GET /documents/search`, `GET /documents/{id}` |
| Prices | `GET /companies/{ticker}/prices`, `GET /ranges` |
| Dependencies | `GET /exposure` |
| Priors | `GET /priors`, `GET /companies/{ticker}/prior` |
| Watchlists | `GET`/`POST /watchlists`, `GET`/`POST /watchlists/{id}/items`, `DELETE /watchlists/{id}/items/{company_id}` |
| Positions | `GET /positions`, `PUT`/`DELETE /positions/{ticker}`, `GET`/`PUT /digest` |
| Feed | `GET /tape`, `GET /dashboard` |
| Instance | `GET /health`, `GET /status`, `GET /capabilities` |
| Administration | `GET /admin/engine`, `GET /admin/accounts`, `POST /admin/accounts/{username}/revoke-sessions`, `DELETE /admin/accounts/{username}` |

Adding a company to a watchlist begins ingestion. Watchlists are identified by UUID, retrievable from `GET /watchlists`:

```bash
WATCHLIST=$(curl -s http://localhost:8000/watchlists | python3 -c 'import sys,json;print(json.load(sys.stdin)[0]["id"])')

curl -X POST "http://localhost:8000/watchlists/$WATCHLIST/items" \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"ticker":"AAPL"}'
```

---

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

Without a Gemini credential, Loom collects and presents source material without evaluating it. Filings, transcripts, insider records, price history, factor ranks, and full-text search remain available. Assessment requires this credential.

Without a Finnhub credential, company news and earnings dates are unavailable. No other capability is affected.

### SEC identification

SEC enforces its fair-access policy through the User-Agent header. The header must contain a contact email address and must not contain a URL; requests that do not comply are refused. The supplied default satisfies these constraints. Operators making sustained use of the system should substitute their own contact details in a `.env` file adjacent to `docker-compose.yml`:

```
SEC_EDGAR_USER_AGENT="Name email@example.com"
SCRAPER_USER_AGENT="Name email@example.com"
```

---

## Operational characteristics

**Analysis throughput is bounded by the free model tier.** Gemini's free tier permits a limited number of requests per minute against a daily ceiling; evaluating one document consumes one request. Loom paces requests to remain within the limit, retries transient failures, falls back across models, and terminates cleanly on quota exhaustion. Evaluating a full watchlist therefore spans more than one session. On a paid tier, set `LLM_MIN_CALL_INTERVAL_SECONDS=0` to remove pacing.

**Background refresh is disabled by default under Docker.** An initial run does not consume model quota until explicitly enabled. Set `SCHEDULER_ENABLED=true` to enable periodic re-ingestion and re-assessment.

**The real-time filing watcher is disabled by default.** Set `WATCHER_ENABLED=true` to enable it. The poll interval is configurable and determines detection latency.

**Companies without sufficient evaluated material report that state explicitly.** Loom withholds an assessment it cannot support with evidence.

---

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
| `SCHEDULER_INTERVAL_MINUTES` | `360` | Watchlist refresh interval |
| `SCHEDULER_STARTUP_DELAY_SECONDS` | `120` | Delay before the first scheduled run |
| `WATCHER_ENABLED` | `false` | Real-time filing detection |
| `WATCHER_INTERVAL_SECONDS` | `60` | Acceptance feed poll interval |
| `INGEST_MAX_WORKERS` | `4` | Concurrent ingestion workers |
| `DATABASE_URL` | set by compose | Required only outside Docker |
| `BLOB_STORE_DIR` | `data/blobs` | Document text storage location |

---

## Architecture

**Backend.** Python 3.12, FastAPI, SQLAlchemy, Alembic, PostgreSQL 16, APScheduler.

**Frontend.** React 19, TypeScript, Vite, TanStack Query, served by nginx with a same-origin proxy to the API.

### Design constraints

**Source adapters.** Each data source is a single class producing plain data structures. Adding a source requires implementing one adapter and registering it; no other component changes.

**Repository isolation.** Database access is confined to repository classes, with one owner per table. Routes and the analysis engine issue no queries of their own.

**Purity boundaries.** Measurement and scoring modules hold no database session and are called with plain data. This makes them testable without a database and reusable outside the request path.

**Storage separation.** PostgreSQL holds metadata and relationships. Document text is written through a `BlobStore` interface, permitting relocation to object storage without modifying ingestion or analysis. Stored references are relative, so the data set is portable across hosts and containers.

**Deterministic by default.** The language model is applied only where linguistic judgement is required: sentiment, passage selection, and characterising change. Deterministic gates decide whether a model call is warranted before it is made. Section extraction, ranking, threshold rules, the quantitative layer, and the assessment layer are fully deterministic.

**Bounded feedback.** Measured results adjust ranking within fixed bounds and only after clearing sample and significance thresholds. Unmeasured or thinly measured components retain their declared weights.

**Authenticated access.** Session-based authentication is enforced at the dependency layer. The evidence API applies it on every endpoint.

**Retrieval conduct.** robots.txt is evaluated before each request, requests are rate limited per domain, and the User-Agent identifies the system rather than impersonating a browser.

---

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
pytest                  # 733 tests
ruff check .
alembic check           # confirms models and migrations agree
```

```bash
cd frontend
npm run build           # type check and production build
```

---

## Repository structure

```
loom/
├── docker-compose.yml        # PostgreSQL, backend, frontend
├── secrets/                  # credential files, excluded from version control
├── backend/
│   ├── app/
│   │   ├── ingestion/        # source adapters
│   │   ├── engine/           # extraction, comparison, norms, assessment
│   │   │   └── quant/        # factor library, cross-section, backtest
│   │   ├── scheduling/       # background jobs, real-time watcher
│   │   ├── repositories/     # database access
│   │   ├── api/routes/       # HTTP layer, including the evidence API
│   │   └── models/           # schema definitions
│   ├── alembic/              # migrations
│   └── tests/                # 733 tests
├── frontend/src/
│   ├── components/           # presentation
│   ├── hooks/                # data access
│   ├── api/                  # HTTP client
│   └── pages/                # composition
├── experiments/              # evaluation harnesses, run outside the application
└── docs/plan.md              # design record
```
