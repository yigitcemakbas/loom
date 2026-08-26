# Loom, Palantir-Gotham-style Investing Dashboard

## Context

You want a decision-support dashboard for equity investing modeled on Palantir Gotham: ingest messy heterogeneous data (news, SEC filings, earnings call transcripts, quarterly earnings reports), "weaving" multiple data sources into one thread, run it through an engine, and surface simple, actionable signals for a human, not a numeric price predictor. This is a brand-new, greenfield project called **Loom**, with no ties to the current `ebpf_packet_router` repo; it will live in a fresh directory on the Desktop (`~/Desktop/loom`).

Confirmed decisions from discussion:
- **Engine type**: NLP/LLM signal extraction, summarization, sentiment, risk-factor extraction, quarter-over-quarter "what changed" diffing. Explicitly not a quantitative price/earnings forecasting model.
- **Data sourcing**: both official APIs *and* direct web scraping, used as parallel first-class source types, not one as primary/fallback for the other. **Breadth is an explicit goal**: mirror Gotham's "ingest anything" posture by covering as many source *categories* as practical, core text sources (news, filings, transcripts, earnings PRs), regulatory/institutional data (insider transactions, institutional holdings, short interest), and alt-data signals (patents, search trends, app rankings, hiring activity), see "Source Catalog" below.
- **Cost constraint**: no paid API usage. Every source, official-API or scraped, must be free, this actively rules out some categories (e.g. Glassdoor, SimilarWeb) that only offer real data behind a paid tier; see "Source Catalog" for what's included/excluded and why.
- **Stack**: Python (FastAPI) backend, Postgres, React + TypeScript frontend.
- **Storage shape**: hybrid, not pure rows-and-columns, see "Storage Design" below. With the broadened source list, this now has **two** physical shapes (documents and structured facts), not one, see "Structured Facts" below.
- **Non-negotiable engineering constraint**: every part of the system is built for single responsibility, separation of concerns, and modularity, easy to modify, extend, and fix in isolation. This is enforced throughout via the concrete patterns in "Architecture Principles," not treated as a slogan.
- **Git workflow constraint**: do not run `git init`, `git commit`, or `git push` at any point during implementation. Write and edit files only, the user reviews the working tree themselves and handles all commits/pushes.

## Architecture Principles (applies to every phase)

These are the concrete mechanisms that make SRP / separation of concerns / modularity real rather than aspirational, every phase below is built through these seams, not around them:

- **Adapter pattern for ingestion**, one class per data source, all implementing the same `SourceAdapter` interface. A source's job is only "produce `RawDocumentDTO`s"; it knows nothing about Postgres, the engine, or other sources. Adding/removing/breaking one source never touches another.
- **Strategy pattern for engine steps**, sentiment extraction, risk-factor extraction, notable-quote extraction, and QoQ diffing are each an independent, independently-testable function/class with one job, orchestrated (not implemented) by `pipeline.py`. Prompts live separately from the LLM call code, so changing a prompt never touches API-call plumbing.
- **Repository pattern for data access**, `CompanyRepository`, `DocumentRepository`, `SignalRepository`, `WatchlistRepository` are the *only* code that knows SQL/ORM details. API routes and the engine call repositories, never raw sessions/queries directly, so the storage backend (see below) can change without rippling into business logic.
- **Thin routes, logic in services**, FastAPI route handlers only parse request → call a service/repository → serialize response. No business logic inline in routes.
- **Blob storage isolated behind an interface**, see "Storage Design": raw content persistence is a separate concern from structured querying, with its own interface (`BlobStore`) so the two can evolve (and be swapped) independently.
- **Config centralized**, one `config.py` (pydantic `Settings`) is the only place reading environment variables; nothing else calls `os.environ` directly.
- **Frontend mirrors the same split**, presentational components (`components/`) never fetch data directly; data-fetching hooks (`hooks/`) own that, calling a thin `api/` client layer. Pages compose hooks + components, no fetch logic in pages either.

## Storage Design: hybrid, not pure rows-and-columns

**The question worth answering explicitly: relational tables, a document store, or both?**

What the data actually looks like:
- Raw ingested content (10-K text, news articles, transcripts) is large, unstructured, and *shaped differently per source*, a transcript has speaker turns, a filing has sections, a news article has a byline. Forcing every source into fixed relational columns means either a wall of nullable fields or a blob column anyway.
- LLM extraction output arrives as JSON, storing it as JSON is the path of least friction.
- But the dashboard's actual query needs, "timeline for AAPL," "all high-confidence signals across my watchlist this week," sentiment trend over time, are joins, filters, and aggregations across companies/watchlists/signals/documents. This is exactly what SQL is good at and a pure document database is comparatively bad at (joins become application-level loops).

**Verdict: hybrid, split by responsibility, not a wholesale move to a document DB.** A pure document store (Mongo etc.) would sacrifice the relational query power the dashboard depends on, and adds a second database system to operate for a solo-dev MVP, not worth it. But treating "store the metadata" and "store the raw bytes" as one undifferentiated concern (a giant `raw_text` column in a relational row) *is* worth fixing now, since it's exactly the kind of seam SRP asks for:

1. **Structured/relational layer (Postgres)**, companies, watchlists, signals, entities, and each document's *metadata* (ticker, source_type, published_at, content_hash, doc_subtype) as real typed columns. This is what needs integrity, foreign keys, filtering, joins.
2. **Object layer (`BlobStore` interface)**, the actual raw content (full filing text, transcript JSON with speaker turns, article HTML) is *not* a giant column on the relational row. It's put through a small interface:
   ```python
   class BlobStore(ABC):
       def put(self, key: str, content: bytes, content_type: str) -> str: ...  # returns a URI
       def get(self, uri: str) -> bytes: ...
   ```
   Phase 1 implementation is `LocalFileBlobStore` (writes to `./data/blobs/` on disk), zero operational overhead. `raw_documents` keeps a `blob_uri` column pointing at it instead of storing text inline. When/if this needs to scale (many large transcripts, multi-machine deployment), an `S3BlobStore`/MinIO implementation drops in behind the same interface with **no changes to ingestion adapters or the engine**, this is the concrete payoff of separating the concern now.
3. **Semi-structured metadata (Postgres JSONB)** stays for genuinely variable, low-query-need extras, adapter-specific fields (SEC accession number, Finnhub article ID), the raw LLM JSON response + prompt version on each signal. JSONB is the right middle ground: queryable via GIN index if ever needed, but doesn't force a schema migration for every new source's quirks.

This gives nearly all the flexibility benefit "object storage" was reaching for, without giving up Postgres's relational integrity/query power or standing up a second database.

**Why not go further and mirror Gotham's full Ontology (generalized objects/properties/links)?** Gotham's object layer is itself a semantic API sitting on top of still-mostly-columnar/blob physical storage (Spark/Parquet, raw files), it's not "objects instead of rows," it's "objects as an abstraction over heterogeneous rows/blobs." Palantir needs that generalized abstraction because Foundry is a platform resold into arbitrary future customer domains with data shapes they don't control in advance (video, sensor telemetry, legacy relational schemas, geospatial), plus cross-source entity resolution (the same person/vehicle appearing in unrelated datasets). Loom keeps a bounded version of the same idea, not the fully generic one: with the source list now deliberately widened (see "Source Catalog"), Loom's raw inputs split into exactly **two** physical shapes, free-text documents and structured numeric/tabular facts (see "Structured Facts" below), not an open-ended variety of unknown future formats. Two known shapes plus a small, known entity-resolution surface (company/person/sector) is a fundamentally smaller problem than Gotham's fully generic meta-object framework, and is what's built here. Revisit going further only if Loom later takes on genuinely unpredictable input shapes (e.g. call audio/video, or domains far outside equities), not before.

### Structured Facts, the second physical shape

Broadening sources into regulatory/institutional data (insider transactions, institutional holdings, short interest) and alt-data (patent filings, search-trend indices, app-store rankings, job-posting counts) introduces data that is **not document-shaped**, "insider Jane Doe bought 5,000 shares at $142.30 on 2026-08-20" is a row of structured facts, not a blob of prose. Modeling it as `raw_documents` text would be a bad fit and would defeat the point of keeping the LLM engine focused on genuinely unstructured extraction. Instead, these sources land in a parallel table:

```sql
CREATE TYPE fact_type AS ENUM (
    'insider_transaction', 'institutional_holding', 'short_interest',
    'patent_filing', 'search_trend_index', 'job_posting_count', 'app_store_ranking'
);

CREATE TABLE structured_facts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id      UUID NOT NULL REFERENCES companies(id),
    fact_type       fact_type NOT NULL,
    source_name     TEXT NOT NULL,               -- 'sec-edgar-form4', 'finra', 'uspto', 'google-trends', ...
    source_url      TEXT,
    as_of_date      DATE NOT NULL,                -- the date the fact pertains to
    value           NUMERIC,                      -- primary numeric value (shares, %, index, count)
    unit            TEXT,                         -- 'shares', 'usd', 'percent', 'index', 'count'
    attributes      JSONB,                        -- fact-specific fields: insider name/role, 13F filer name, etc.
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash    TEXT NOT NULL,
    UNIQUE (company_id, fact_type, as_of_date, content_hash)
);
CREATE INDEX idx_structured_facts_company_type_date ON structured_facts (company_id, fact_type, as_of_date DESC);
```

This keeps the same SRP discipline as the document side: a `FactRepository` is the only thing that touches this table, and, like `raw_documents`, it's a single unified shape that every fact-producing adapter conforms to, regardless of whether the underlying source is an official filing or a scrape. The engine can later treat facts as additional evidence for signals (e.g. "cluster of insider buying + a new risk factor in the same week" is a stronger `qoq_anomaly` than either alone) without needing to touch how facts are ingested or stored.

## Repo / Directory Structure

New monorepo at `~/Desktop/loom/`, two apps (`backend/`, `frontend/`), root `docker-compose.yml` (Postgres). Pin backend to **Python 3.11 or 3.12** in a virtualenv (this machine has Python 3.14 installed, but NLP/scraping deps lag behind brand-new Python releases). Docker/Compose already available for Postgres, so no local Postgres install needed.

```
loom/
├── docker-compose.yml            # postgres
├── Makefile                      # make up / ingest / migrate / test
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app + APScheduler startup
│   │   ├── config.py               # pydantic Settings, sole source of env vars
│   │   ├── models/                 # SQLAlchemy: company, watchlist, document, structured_fact, signal, entity
│   │   ├── schemas/                 # Pydantic request/response models
│   │   ├── repositories/           # CompanyRepository, DocumentRepository, FactRepository, SignalRepository, WatchlistRepository
│   │   ├── api/routes/             # thin routes: watchlists, companies, signals, documents, facts, search
│   │   ├── storage/
│   │   │   └── blob_store.py        # BlobStore ABC + LocalFileBlobStore (S3BlobStore later)
│   │   ├── ingestion/
│   │   │   ├── base.py              # DocumentSourceAdapter/FactSourceAdapter ABCs + DTOs
│   │   │   ├── sec_edgar.py         # official API adapter (10-K/10-Q/8-K/DEF 14A text)
│   │   │   ├── news_api.py          # official API adapter (Finnhub)
│   │   │   ├── facts/
│   │   │   │   ├── sec_form4.py     # insider transactions (facts)
│   │   │   │   ├── sec_13f.py       # institutional holdings (facts)
│   │   │   │   ├── finra_short_interest.py
│   │   │   │   ├── uspto_patents.py
│   │   │   │   └── google_trends.py
│   │   │   ├── scrapers/
│   │   │   │   ├── base_scraper.py  # rate limit, robots.txt, honest UA
│   │   │   │   ├── earnings_transcript_motley_fool.py
│   │   │   │   ├── earnings_pr_scraper.py
│   │   │   │   ├── careers_page_scraper.py
│   │   │   │   └── app_store_scraper.py
│   │   │   └── registry.py          # source_type -> adapter (documents + facts)
│   │   ├── engine/
│   │   │   ├── llm_client.py        # Anthropic SDK wrapper, only place that calls the API
│   │   │   ├── prompts/             # sentiment.py, risk_extraction.py, notable_quotes.py, diff_summary.py
│   │   │   ├── pipeline.py          # orchestrates engine steps, contains no extraction logic itself
│   │   │   ├── diffing.py           # QoQ risk-factor diffing
│   │   │   └── signal_writer.py
│   │   └── scheduling/             # APScheduler jobs
│   ├── scripts/                    # seed_companies.py, ingest_once.py, run_engine_once.py
│   ├── alembic/                    # migrations
│   └── tests/                      # one test module per module above, enforced by the same seams
└── frontend/                        # Vite + React + TS
    └── src/
        ├── pages/                   # WatchlistPage, CompanyDetailPage, SignalFeedPage, composition only
        ├── components/               # TickerTable, TimelinePanel, SentimentTrendChart, RiskFactorDiffCard, SignalCard, presentational
        ├── hooks/                    # TanStack Query hooks, the only place that calls api/
        ├── api/                      # thin fetch client, no UI logic
        └── styles/theme.css          # dark Gotham-esque palette
```

## Data Model (Postgres)

One unified `raw_documents` table across all four source kinds is the key relational design choice, every ingestion adapter just needs to produce rows of this shape (with content routed through `BlobStore`, not inlined), which is what makes the pluggable-adapter pattern work cleanly:

- `companies` (ticker, name, cik, sector, exchange)
- `watchlists` / `watchlist_items`
- `raw_documents` (company_id, `source_type` enum: `sec_edgar_filing | news_api | scraped_transcript | scraped_earnings_report`, source_name, source_url, doc_subtype like `10-K`/`8-K`/`earnings_call`, `blob_uri` → points into `BlobStore` for the actual content, metadata JSONB for adapter-specific extras, `content_hash` unique per company for dedupe)
- `entities` / `entity_links`, simple join-table entity mentions (person/company/sector), no graph DB needed at this scale
- `signals`, the actionable output: `signal_type` enum (`sentiment_shift | new_risk_factor | notable_quote | qoq_anomaly | guidance_change`), summary, sentiment_score (-1..1), confidence (0..1), `evidence_quote` (verbatim excerpt), `source_document_id`, `compared_document_id` (for QoQ diffs), metadata JSONB (raw LLM response + prompt version)

Every signal must be traceable back to an exact document/quote, this provenance requirement is central to the Gotham-style "why does this matter" feel.

## Ingestion Layer

Two adapter interfaces, split by output shape (SRP: an adapter has exactly one job, produce documents, or produce facts, never both):

```python
class DocumentSourceAdapter(ABC):
    def fetch(self, ticker: str, since: datetime | None) -> list[RawDocumentDTO]: ...

class FactSourceAdapter(ABC):
    def fetch(self, ticker: str, since: datetime | None) -> list[StructuredFactDTO]: ...
```

Both register in `ingestion/registry.py` keyed by `source_type`. A generic `ingest_all(ticker)` loop calls every registered adapter, routes `RawDocumentDTO`s through `BlobStore.put()` + `DocumentRepository`, and routes `StructuredFactDTO`s through `FactRepository`, adding a new source of either shape later means writing one adapter and registering it, with no changes to storage or the engine.

### Source Catalog

All sources below are **free** (no paid API tier used anywhere) and are wired in as parallel, first-class sources, none is a fallback for another. Grouped by category and the phase that introduces them:

**Core text sources (Phase 1–4, already planned):**
| Source | Type | Adapter | Notes |
|---|---|---|---|
| SEC EDGAR (10-K/10-Q/8-K) | Official API | `sec_edgar.py` | Free, no key, just an honest `User-Agent` header. `data.sec.gov` submissions + full-text search. |
| Finnhub company news | Official API | `news_api.py` | Free tier, ticker-scoped `/company-news`. |
| Earnings call transcripts | Scrape | `scrapers/earnings_transcript_motley_fool.py` | Real ToS exposure, see existing note below; rate-limited, robots.txt-checked. |
| Earnings press releases | Scrape | `scrapers/earnings_pr_scraper.py` | Company IR pages / BusinessWire; company-authored public releases, pairs with the matching 8-K. |

**Regulatory/institutional (Phase 5, new, all official SEC/FINRA data, free):**
| Source | Type | Adapter | Notes |
|---|---|---|---|
| SEC Form 4 (insider transactions) | Official API | `facts/sec_form4.py` | Same EDGAR base client, new doc type; outputs `StructuredFactDTO` (`insider_transaction`), buyer/seller, role, shares, price, date. |
| SEC 13F (institutional holdings) | Official API | `facts/sec_13f.py` | Quarterly institutional position filings via EDGAR; outputs `institutional_holding` facts (holder name, shares, value). |
| SEC DEF 14A (proxy statements) | Official API | `sec_edgar.py` (new doc_subtype) | Text document, board/exec comp disclosures; fits `raw_documents`, not facts. |
| FINRA short interest | Official, free download | `facts/finra_short_interest.py` | FINRA publishes bi-monthly short-interest files publicly; outputs `short_interest` facts (% of float, days-to-cover). |

**Alt-data signals (Phase 6, new, free APIs/scrapes only):**
| Source | Type | Adapter | Notes |
|---|---|---|---|
| USPTO PatentsView | Official API | `facts/uspto_patents.py` | Free, official government API; `patent_filing` facts (filing/grant counts, tech category) as an innovation-activity proxy. |
| Google Trends | Scrape (public, unofficial) | `facts/google_trends.py` | Via the public Trends interface (e.g. `pytrends`), no login/paywall, low legal risk since it's just querying a public consumer tool; `search_trend_index` facts. |
| Company careers pages | Scrape | `scrapers/careers_page_scraper.py` | First-party (each company's own site), lowest third-party-ToS risk of the scrapers; `job_posting_count` facts as a hiring-trend proxy. Most fragile to maintain (per-company HTML), so scoped last. |
| App Store / Google Play listings | Scrape | `scrapers/app_store_scraper.py` | Public consumer-facing listing pages, no login; `app_store_ranking` facts (category rank, rating, review count) for companies with consumer apps. |

**Explicitly excluded for now, flagged, not silently dropped:** Glassdoor and SimilarWeb were considered for alt-data but are excluded because meaningful data from either sits behind a **paid** API, which conflicts with the no-paid-API constraint, their free public pages give only token/heavily-limited data, and Glassdoor's ToS + anti-scraping measures make scraping it a poor risk/reward trade. Revisit only if the no-paid-API constraint changes. Market/analyst data (analyst ratings, options flow) and social/retail sentiment (Reddit, X/Twitter, StockTwits) were also discussed but deprioritized by you in favor of regulatory/institutional and alt-data, they remain a natural Phase 7+ if wanted later, noting that most quality options-flow/analyst-ratings feeds are paid, and social scraping (Reddit/X) carries the heaviest ToS/rate-limit friction of any category here.

Shared scraper infrastructure (`scrapers/base_scraper.py`): robots.txt check per domain, per-domain rate limiting, an honest identifying User-Agent (never spoofing a browser to evade blocks), `httpx` + `selectolax`/BeautifulSoup parsing, and per-scraper try/except isolation so one broken selector never takes down any other source, increasingly important as the source count grows.

## Engine (raw documents → signals)

The engine runs in two stages: **per-document extraction** (triggered as each document/fact is ingested) and a **scheduled correlation sweep** (periodic, cross-document). Both follow one guiding principle throughout: **deterministic code handles anything that's arithmetic or structural comparison; the LLM is reserved for what genuinely requires language judgment** (sentiment nuance, quote selection, narrative synthesis). This keeps cost proportional to actual signal, keeps output auditable, and keeps each step small enough to satisfy SRP.

### Stage 1, per-document extraction

`engine/pipeline.py` routes each new document/fact through independent, single-purpose steps: sentiment extraction, risk-factor extraction (if filing), notable-quote extraction (if transcript/news), and, for 10-K/10-Q, a diff against the prior same-subtype filing; for `structured_facts`, simple threshold rules (e.g. short interest +20% MoM, ≥3 insider sells in a week) run with no LLM involved at all. `pipeline.py` only orchestrates; each step is its own function/module, independently testable/replaceable.

- **Chunking is structural, not arbitrary**: long filings are split by their actual section headings (`Item 1A`, `Item 7`), transcripts by speaker turn, so no prompt ever sees a mid-sentence cut; very long documents get a map-reduce pass (summarize each section, then one pass over the summaries).
- LLM calls go through `engine/llm_client.py` (Anthropic SDK), always requesting schema-constrained structured output parsed into Pydantic models, with one retry on a parse failure before logging and dropping. This is the *only* module that talks to the Anthropic API. **Model routing by task**: a faster/cheaper model for high-volume routine news sentiment, the stronger model for filings/transcripts/diffing/synthesis where nuance matters more, a cost lever as much as a quality one.
- **QoQ "what changed" diffing** (`engine/diffing.py`), the flagship feature, is two-stage rather than "feed both filings to the LLM": (1) deterministic section extraction + paragraph-level similarity matching (difflib/TF-IDF) to cheaply pair "same risk, reworded" vs. "genuinely new/dropped"; (2) LLM pass only on the paragraphs that didn't match cleanly, asking whether it's new/reworded/dropped and why it matters, producing an auditable `qoq_anomaly`/`new_risk_factor` signal pointing at both documents. Every other future text-comparison need should follow this same two-stage template, not a one-shot LLM call.
- `sentiment_score` (-1..1) and `confidence` (0..1, blending the LLM's self-reported confidence with a heuristic discount for e.g. truncated/chunked text) are stored per signal.
- `signal_writer.py` is the only module that persists engine output, it calls `SignalRepository`/`FactRepository`, so extraction steps never touch the database directly.
- **Idempotency**: each engine run is keyed by `(document_id, prompt_version)`, re-running with an unchanged prompt version is a no-op; deliberately bumping `prompt_version` (stored in `signals.metadata`) is the only way to force reprocessing, so prompt iteration never silently duplicates signals.

### Stage 2, correlation sweep (the actual "Gotham" behavior)

A single signal in isolation is weak evidence. The real value is noticing when independent weak signals line up, a cluster of insider selling, a negative sentiment shift, and a new risk factor all inside the same window is much stronger evidence than any one alone. `engine/correlation.py` runs on a schedule (not per-document), scanning each watchlist ticker's recent `signals` + `structured_facts` in a rolling window (e.g. 7–14 days):
1. **Deterministic rule matching** (cheap, runs every sweep for every ticker) decides *whether* a cluster is interesting, e.g. "≥2 signal types + ≥1 corroborating fact type within N days."
2. **Only when a rule fires**, an LLM synthesis call writes the composite "why this matters" narrative, explicitly citing the underlying signal/fact IDs it ties together, producing a new composite signal (or an alert distinct from raw signals) pointing at its sources.

This keeps LLM spend proportional to how much is actually happening on a given ticker, not to how many tickers are watched.

### Ranking

Every signal gets a `priority` score for the dashboard feed (beyond `confidence` alone), combining: confidence, a per-signal-type weight (a deterministic threshold-rule fact is more trustworthy than an LLM sentiment read, and should rank accordingly), recency, and corroboration count (composite signals from Stage 2 rank above lone signals). This is what keeps the Signal Feed a ranked short list rather than a firehose of everything the engine noticed.

### Evaluation

Because LLM output quality is inherently fuzzy, keep a small hand-labeled golden set of real documents (a 10-K with known risk changes, a transcript with a known tone shift) from Phase 2 onward, and run it as a regression check before shipping any prompt change, cheap insurance against silently degrading extraction quality over time.

## Backend API (FastAPI)

`watchlists` CRUD, `companies` list/detail, `companies/{ticker}/timeline` (merged documents + signals), `companies/{ticker}/sentiment` (time series), `signals` feed with filters (ticker/type/since/min_confidence), `documents/{id}` (source drill-down, fetches content via `BlobStore`), `search`, plus dev-convenience `admin/ingest/{ticker}` and `admin/engine/run/{ticker}` to trigger runs manually. Single-user MVP: no auth, bind to localhost; `owner_email` on watchlists left in place for later multi-user support. Every route calls a repository, none touch SQLAlchemy sessions directly.

## Frontend (React + TypeScript)

Vite + React + TS, TanStack Query for server state (fits a fetch-and-display dashboard, no need for Redux), plain CSS variables for a dark, high-contrast "Gotham" theme (monospace for data, cyan/amber accents), deliberately closer to an analyst terminal than a consumer app. Desktop-first for the MVP; mobile is an explicit non-goal for now, not an oversight. Data-fetching lives only in `hooks/`; `components/` stay presentational and reusable.

### UX Flow, what an investor sees and does

**`WatchlistPage` (home)**: one row per tracked ticker, ticker/name, sector, a sentiment sparkline (~90 days), and a **priority badge** (driven by the engine's `priority` score) showing whether anything noteworthy happened since last visit. This is the "scan in seconds, act on what matters" home view the whole product exists to produce. "Add ticker" is a simple search-and-add modal; a freshly-added ticker shows a "gathering data…" state while first ingestion/engine runs complete, rather than a blank page.

**`CompanyDetailPage`**: `SentimentTrendChart` at top (sentiment over time with markers per contributing signal, clickable), a merged `TimelinePanel` below (filings/news/transcripts/press-releases/signals interleaved chronologically, source-type icon + sentiment color per card), a `RiskFactorDiffCard` whenever a new 10-K/10-Q lands (plain-English "N new, M dropped" risk factors, each with verbatim excerpt + why it matters), and, once Phase 5 ships, an Activity side panel surfacing recent insider transactions/short-interest trend, visually distinct from the LLM-derived signals so the investor knows which kind of evidence they're looking at.

**`SignalFeedPage`**: the cross-ticker alerts inbox, ranked by `priority` (not chronological), filterable by ticker/type/min-confidence/date. Composite alerts from the correlation sweep are visually distinguished and rank above lone signals. Every card, no exception, expands to its exact evidence: verbatim quote, source link, timestamp, confidence. **Nothing is ever presented without a receipt**; the investor is always one click from verifying a claim rather than trusting the model. Reviewed signals can be marked/dismissed so the feed doesn't restate the same thing daily.

**Concrete actions available**: add/remove watchlist tickers; scan the ranked home view instead of reading everything; open any signal/diff card to inspect its evidence and judge it themselves; filter the Signal Feed on demand; manually trigger a fresh ingest/engine run per ticker (surfaced as a "refresh" button over the `admin/ingest`/`admin/engine/run` endpoints) rather than waiting for the schedule; search by ticker/company/exec name and jump straight there.

## Scheduling

**APScheduler running inside the FastAPI process**, not Celery/Redis, is the right call for a single user with a modest watchlist and infrequent ingestion (every few hours). Jobs registered in `scheduling/jobs.py`, started from `main.py`'s lifespan hook, calling the same ingestion/engine entry points the CLI scripts and admin routes use, one code path, three triggers. If this later needs to scale to many users/tickers, swap to Celery + Beat without touching ingestion/engine interfaces, not needed now.

## Phased Build Plan

**Phase 1, Scaffold + SEC ingestion + raw data shell (no LLM yet).** Prove ingestion → BlobStore/Postgres → API → frontend end-to-end with real SEC data: docker-compose (Postgres), migrations for `companies`/`watchlists`/`watchlist_items`/`raw_documents`, `storage/blob_store.py` (`LocalFileBlobStore`), `ingestion/base.py` + `sec_edgar.py`, `repositories/`, seed/ingest CLI scripts, read-only API routes, a plain frontend list view (no charts/signals yet). Fully verifiable without an Anthropic API key.

**Phase 2, LLM signal engine + alerts feed.** `engine/` module (llm_client, prompts, pipeline, diffing, signal_writer), migration adding `signals`/`entities`/`entity_links`, `SignalRepository`, `signals` API route, frontend `SignalFeedPage`/`SignalCard`/`SentimentTrendChart`/`RiskFactorDiffCard`. QoQ risk-factor diffing is the flagship deliverable here. Include a basic `priority` score on signals from the start (confidence + type weight + recency) so the feed is ranked, not a raw list. `engine/correlation.py` (the cross-signal sweep) can follow once Phase 5 gives it structured facts worth correlating against, noted here so it isn't lost, but its natural home is after Phase 5.

**Phase 3, News API + first scraper (earnings call transcripts). SHIPPED.** `news_api.py` (Finnhub), `scrapers/base_scraper.py`, `earnings_transcript_motley_fool.py`, wired into the registry; APScheduler jobs turned on so ingestion/engine run automatically instead of only via CLI.

As built, with the decisions worth remembering:

- **Transcript discovery goes through fool.com's monthly sitemaps**, not a per-ticker index. No per-ticker transcript listing survives without JavaScript, but the sitemaps are linked from robots.txt (so fetching them is explicitly sanctioned) and transcript slugs carry the ticker and date, so one sitemap fetch per month yields `(ticker, date, url)` for every transcript that month. The parsed index is cached per adapter instance, because ingestion runs ticker by ticker and would otherwise refetch the same sitemaps once per ticker.
- **`BaseScraper` enforces robots.txt rather than consulting it**, with no override flag: an override that exists is an override that eventually gets used. Rate limiting is keyed by domain rather than by scraper, so two scrapers pointed at one host still cooperate.
- **Finnhub is optional.** No key means the adapter returns nothing and logs once; filings and transcripts still ingest. A missing optional source must never take down a run that the other sources would complete fine.
- **Per-source-type bounds in `select_recent_documents`.** News arrives at a completely different cadence from filings, so it gets both a shorter window and a hard count cap. Treating every source alike would let the noisiest and least individually valuable one crowd out the filings.

Also in this phase: **short-window comparison analysis** (`engine/clustering.py` plus the `emerging_pattern` signal type). The engine's only comparison logic was the year-over-year 10-K risk diff; nothing compared a company's recent 8-Ks/news against each other, so a real same-day or same-week shift produced isolated signals rather than a synthesised one. Now a deterministic gate decides whether a window of disclosures is worth a model call at all (two or more distinct documents, plus at least one of: three findings aligned on a direction, a major finding corroborated by a second document, or a tone swing against the prior 90 days), and only then does the LLM synthesise what the combination means. On a quiet week the gate never fires and nothing is spent.

Two design points that were not obvious up front:

- **The window anchors on the most recent disclosure, not on the clock.** Anchoring on "now" made a genuine three-day burst invisible the following week, which is the exact case the feature exists to catch. How much an old pattern still matters is `priority.py`'s recency decay, not the cluster cutoff.
- **The anchor quote is re-verified against the quotes the model was handed.** This is the one prompt that picks among existing quotes rather than producing them, so it is the one place a paraphrase could enter the evidence chain unnoticed. A non-matching quote is dropped: a pattern with no receipt is still useful, one with a fabricated receipt is not.

**Phase 4, Earnings press releases + full-text search. SHIPPED, with one deliberate departure from the original plan.**

**The press-release scraper was not built, and should not be.** The plan called for `earnings_pr_scraper.py` against IR pages and BusinessWire, and flagged it as the most fragile scraper in the project. Investigating it revealed the premise was wrong: the authoritative earnings press release is already inside the 8-K that announces it, as Exhibit 99.1 of the same accession. Loom was fetching only each 8-K's *primary* document, which for an 8-K is a cover sheet that names the item numbers and then says "see Exhibit 99.1". Measured on Apple's 2026-07-30 8-K, that meant storing 3,475 characters of boilerplate and discarding 10,463 characters of actual results; the engine dutifully reported the filing as "purely administrative" at 17% confidence, which was correct about what it had been given and useless to a reader. All 842 stored 8-Ks were affected.

So `sec_edgar.py` now reads the accession's typed exhibit list and appends EX-99* exhibits to the document text. This is strictly better than the planned scraper on every axis that matters: it is an official keyless source already covered by the existing rate limit, there is no per-company HTML to break, it is the authoritative copy rather than a wire-service reproduction, and "cross-reference the press release with its corresponding 8-K" is free because they are the same filing. IR/BusinessWire scraping remains available later if non-earnings press releases are ever wanted; nothing here forecloses it.

Two things this exposed that were worth fixing properly:

- **Dedupe now keys on `source_url`, not `content_hash`.** Enriching the 8-Ks changed the bytes of every earnings filing, which under a content-hash-only check would have re-ingested each one as a second row beside its own cover sheet. A document's identity is its URL; hashing answers "have I stored these exact bytes", which is the wrong question every time extraction improves. Verified idempotent across all three sources.
- **Exhibit fetching never raises.** An 8-K whose exhibits cannot be read is still stored as its cover sheet, which is exactly what was stored before the feature existed.

**Full-text search** landed as the plan intended, a real index rather than a grep-on-request hack: a `document_search_index` table holding one weighted Postgres `tsvector` per document (title terms above body terms), populated at ingestion, with a GIN index. Two properties worth keeping:

- **The index stores the vector, never the text.** Content stays in the BlobStore and is read back only for the handful of results actually being returned, so search adds an index rather than a second copy of every filing. Snippets are therefore built in Python instead of with `ts_headline`, which would have required the text in the database.
- **Snippets anchor on the densest cluster of query terms.** The obvious implementation, jumping to the first occurrence of any term, reliably landed on financial tables, because filings repeat words like "margin" and "cost" hundreds of times in them. Scoring each occurrence by how many *distinct* query terms sit near it fixed it; an exact quoted phrase overrides the heuristic.

Also in this phase: source-type colour-coding on the timeline, which only became meaningful once filings, transcripts, and news were all flowing into the same list, and a relevance gate on Finnhub news (see below).

**News relevance gating**, added when the API key was first supplied. Finnhub's `/company-news` returns anything that merely mentions the ticker: 106 of 176 items in a live AAPL pull never said "Apple" at all, and 11 of the 15 most recent were about Nvidia, Alibaba, or Samsung. Since `select_recent_documents` takes the most recent news items, the engine would have analysed those and attributed them to Apple. An item is now kept only if the company is named in its headline, which is the deterministic form of "is this actually about them" and removes roughly 60-90% of the feed depending on ticker.

**News is analysed as one batched digest per ticker**, not one call per item (`engine/prompts/news_digest.py`, `pipeline.analyze_recent_news`). Filings earn a call each because each is a substantial document; news items run a couple of hundred characters. Measured on the real watchlist, per-item analysis made news roughly two thirds of the engine's entire call budget for a ticker (AMD needed 23 calls, 15 of them news) while producing its least informative output, and it was what actually exhausted the free-tier quota mid-backfill. Batching took AAPL from 20 calls to 6 and AMD from 23 to 9.

It is also the better analysis, which is why it was worth doing rather than simply capping the item count. One item reporting a supplier price rise is barely a signal; five across a week saying it is one, and only a reader who sees the run can tell the difference. Live on 15 AAPL items, the digest returned two material findings (a Siri/Vision Pro reorganisation, Walmart enabling Apple Pay) and a tone read, rather than fifteen shallow per-item analyses.

Batching does put pressure on the project's core invariant, that every signal points at the document it came from, since one call now covers many documents. Two guards: each finding carries the item index it came from, and its quote must be found verbatim in *that* item's text before the signal is written. A finding with an out-of-range index or an unverifiable quote is dropped rather than attached to whichever document is nearest, because a signal on the wrong document is worse than a missing one, it sends the reader to a source that never said it.

**Timeouts became retryable** (`engine/llm_client.py`). The retry loop handled rate limits and server errors but let network-level timeouts through untouched, so a 503 got nine attempts across three models while a read timeout got exactly one. The year-over-year diff sends the largest prompt in the engine and was the first place this surfaced. Timeouts now get the same backoff and model fallback, and the failure message distinguishes quota exhaustion, provider load, and network failure, because each calls for a different response from whoever reads the log.

**Phase 5, Regulatory/institutional breadth. PARTIALLY SHIPPED.** Done: the `structured_facts` table and `FactRepository`, `facts/sec_form4.py`, the threshold-rule engine (`engine/fact_rules.py`) with its signal types and priority weights, the `facts` API routes, and the activity panel on `CompanyDetailPage`. Still outstanding: `facts/sec_13f.py`, `facts/finra_short_interest.py`, and DEF 14A as a new `sec_edgar.py` doc_subtype. The short-interest rule and its signal type are written and tested but cannot fire until the FINRA adapter feeds them.

These are **the first signals in the project that involve no model call at all**, which also makes them the only analysis that keeps working when the provider is rate limited or unconfigured. Insider transactions arrive already structured; asking a model to re-derive "four insiders sold within a fortnight" from XML would be slower, costlier, and less reliable than counting.

**Transaction codes are the whole story, and this is where insider data usually goes wrong.** A Form 4 reporting that an officer disposed of shares is usually not a decision to sell: vesting restricted stock triggers automatic withholding to cover income tax (code F), and exercising options (code M) appears as an acquisition followed by a disposal. Both are mechanical consequences of a compensation schedule agreed years earlier. Measured on real data, an Apple officer's largest single "disposal" was 16,238 shares worth $4.8m of tax withholding, sitting alongside genuine open-market sales eleven times smaller; a board member's 65,000-share disposal was a gift. Across Alphabet's stored filings only 64 of 256 transactions were open-market. A rule that counted all disposals alike would fire on nearly every large company every quarter, which is indistinguishable from never firing.

So only codes S and P count toward a rule. Everything is still stored, because the full record is what makes the data auditable, and the UI shows the split explicitly ("56 of 104 filings discretionary") rather than a single number that would read as "executives are dumping stock".

Other decisions worth remembering:

- **Buying and selling are separate rules.** Insiders sell for diversification, tax bills, and houses; they buy for essentially one reason. Averaging the two would discard the stronger signal.
- **Insider findings are never reported as "major".** They corroborate a thesis rather than being one, and overstating them is exactly how this data misleads.
- **Fact signals carry no `evidence_quote`, and that is correct rather than a gap.** The receipt for a cluster is the filings themselves, linked from metadata. The verbatim-quote rule exists to stop a model paraphrasing prose, and no model was involved.
- **Form 4 XML filenames vary by filing agent** (`form4.xml` for some, `wk-form4_<id>.xml` for others). The filename is derived by stripping EDGAR's XSL renderer prefix from `primaryDocument`; hardcoding it silently 404s for roughly half the watchlist.
- **Fact dedupe is computed generically in the registry**, not per adapter, so a new fact source inherits it instead of inventing its own key and getting it subtly wrong.

**Phase 6, Alt-data breadth + correlation sweep.** `facts/uspto_patents.py`, `facts/google_trends.py`, `scrapers/app_store_scraper.py`, `scrapers/careers_page_scraper.py` (built last, most fragile, per-company HTML). This is also the natural point to add `engine/correlation.py` (Stage 2 of the engine design above): with a real mix of signals and facts now flowing in, the scheduled cross-signal sweep has enough to correlate against, deterministic rule matching for "is this cluster interesting," LLM synthesis only when a rule fires, producing composite alerts that outrank lone signals.

## Verification (Phase 1)

1. `docker compose up -d`, Postgres up, confirm via `docker compose ps`.
2. `alembic upgrade head`, apply migrations, confirm tables via `psql ... -c '\dt'`.
3. `python -m scripts.seed_companies`, seed a couple of tickers (e.g. AAPL, MSFT) + default watchlist.
4. `python -m scripts.ingest_once --ticker AAPL`, live SEC EDGAR fetch, confirm rows land in `raw_documents` and files land in `./data/blobs/`.
5. `uvicorn app.main:app --reload` then `curl localhost:8000/companies/AAPL/timeline`, confirm ingested filings return as JSON with correct `source_url`/`published_at`.
6. `npm run dev` in `frontend/`, browse to the watchlist page, open AAPL, confirm the same filings render with working "view source" links to SEC EDGAR.
7. Re-run `ingest_once` and confirm no duplicate rows (`content_hash` dedupe), the one non-obvious failure mode worth checking before Phase 2.

## Critical Files

- `backend/app/ingestion/base.py`, the `SourceAdapter`/`RawDocumentDTO` contract every source (SEC, news, scrapers) must conform to.
- `backend/app/storage/blob_store.py`, the `BlobStore` interface separating raw content persistence from relational metadata; the concrete seam that makes the storage design swappable.
- `backend/app/repositories/`, the only layer allowed to touch SQLAlchemy directly; keeps routes/engine decoupled from storage details.
- `backend/app/models/document.py` + its migration, the unified `raw_documents` metadata shape all sources land in.
- `backend/app/engine/pipeline.py` and `backend/app/engine/diffing.py`, the core signal-extraction logic and the QoQ diffing that's the product's signature feature.
- `backend/app/ingestion/sec_edgar.py`, first concrete adapter, unblocks all of Phase 1.
- `backend/app/models/structured_fact.py` + its migration, the second physical shape (Phase 5+), parallel to `raw_documents`, that every regulatory/alt-data fact source lands in.
- `frontend/src/pages/CompanyDetailPage.tsx` (with `TimelinePanel`, `SentimentTrendChart`, `RiskFactorDiffCard`), the page most representative of the target UX.

## Note on scraping risk

Scraping earnings call transcripts and press releases (per your preference, as first-class sources rather than fallback) carries real ToS and fragility risk depending on the target site, this plan isolates that risk behind a shared, rate-limited, robots.txt-aware scraper base so individual sources can be swapped or dropped without redesigning ingestion, but the legal exposure itself is a judgment call only you can make per source when the time comes.


## The brief: many findings in, one answer out

Added after a plain user test that the product failed. The report was: "I get
on and I understand nothing. I see a bunch of sentences that ultimately mean
nothing to me. The driving idea, taking multiple sources and producing a single
source of truth so it is easier to make a decision, is amiss."

That was correct, and it was not a styling problem. Every screen showed
*findings*: forty-five sentences per company, each accurate, each weighted the
same, none of them answering "so what should I think about this company". The
pipeline had been built end to end without ever producing the thing it existed
to produce.

`engine/brief.py` closes that gap. One row per company: a stance, one plain
sentence, the two or three things driving it, and what is new since the last
read. Decisions worth keeping:

- **It is deterministic, with no model call.** The judgement is arithmetic:
  which way does weighted evidence lean, do independent sources agree, what is
  new. Computing that is more reliable than asking for it, it stays auditable
  (a reader can be shown exactly which findings produced a stance), and, not
  least, the single most important screen in the product must not go blank when
  a free tier runs out.
- **Unassessed is not the same as calm.** Findings analysed before market-impact
  assessment existed carry no direction. Treating those as "neutral" reported
  companies with twenty open findings as having nothing notable. The brief now
  says "20 findings collected, but 19 have not been assessed yet, so no view is
  offered", which is the honest answer.
- **No view means no confidence.** A stance of "no view offered" printed beside
  "83% confident" reads as a contradiction and devalues every other number.
- **Breadth is required before a verdict.** One insider cluster from one source
  was reporting "serious concerns". A confident stance now needs several
  findings agreeing across more than one kind of source, otherwise it is
  softened a step. Overclaiming is the fastest way to make a tool like this
  untrustworthy.
- **One story told five times uses one slot.** A theme appearing in a filing, a
  call, and three news items would otherwise fill every driver slot and hide
  everything else; near-duplicates are folded in as extra support instead, which
  is also what lets a driver say "seen in: quarterly report, earnings call, news".

**Plain language became a house rule** (`engine/prompts/plain_language.py`,
shared by every extraction prompt). The findings were accurate and unreadable:
"DMA and regulatory feature withholding", "component cost inflation may degrade
hardware unit economics". A tool whose purpose is to spare someone reading
primary sources has failed if its output needs the same background the sources
did. Acronyms are now expanded on first use, form numbers never appear in a
finding, and jargon is replaced with cause and effect ("memory chips cost more,
so each phone earns less profit").

**The dashboard leads with briefs**, worst first, split into "needs a look" and
"nothing to act on". The dense screener survives behind a toggle: it answers
"how does X compare to Y on this measure", which is a question you can only ask
once you already know what you are looking for.


## Event awareness: the cadence problem

User report: "The largest concern is this only does yoy. Not valuable when it
comes to real trading. When Nvidia is set to do an earnings call, I should be
able to get on this app and make a decision."

Correct, and worse than stated. Three separate gaps:

1. **Comparison ran only on 10-Ks.** The gate was literally
   `if document.doc_subtype == "10-K"`, so all 214 stored quarterly reports
   were analysed in isolation and never compared to anything. The app could
   only ever say what changed year over year, which is the wrong cadence for a
   decision taken around a quarterly event.
2. **The diff read only Item 1A.** Risk factors, never management's discussion,
   which is where demand, margins, and outlook are actually discussed. In a
   10-Q the risk section is usually a cross reference back to the annual report,
   so the one section being compared was the least informative one available.
3. **No concept of when.** Nothing in the system knew a company was about to
   report.

Fixes, in order of how much they changed:

- **`COMPARISON_PLAN`** replaces the hardcoded gate: annual reports diff Item 1A,
  quarterly reports diff Item 2. Verified deterministically against stored
  filings, the quarterly diff surfaces real movement (NVDA gross margin 60.5%
  to 74.9%, Tesla cost of services +37%), all without a model call.
- **A separate prompt for the quarterly comparison**
  (`prompts/quarter_comparison.py`). Reusing the risk prompt here would have
  shipped something incoherent: it opens "you are comparing annual risk factors"
  and asks whether each passage is a substantive *new risk*, which is not a
  sensible question to put to a gross-margin table. The quarterly prompt instead
  asks what moved and by how much, and requires the magnitude in the finding,
  because a change without a size is not decision-useful.
- **An earnings calendar adapter** (Finnhub, free tier) storing scheduled dates
  and consensus as `earnings_event` facts, plus `engine/earnings.py` folding
  them into the read for the next report. Deterministic, for the same reason as
  the brief: the screen that matters most on the busiest day must not depend on
  a provider being up.

**What it stops short of.** It does not say buy or sell. Nothing in this
pipeline prices the stock, knows the reader's position, or models what is
already in the price, so a recommendation would be fabricated authority. What it
can honestly do is put the date, the expectations, the quarter-over-quarter
movement, insider positioning, and the standing verdict in one place at the
moment they are needed, and let the reader decide.

**Known dead end, flagged rather than hidden.** Finnhub's free tier returns
scheduled dates and consensus but no historical actuals, so the beat/miss track
record can never populate from it. The code degrades honestly (a record is only
mentioned once three reports exist, and the UI shows nothing rather than an
empty "0 of 0"), but that half is inert until a source of historical EPS is
added. SEC XBRL data in the filings already stored is the obvious candidate.
