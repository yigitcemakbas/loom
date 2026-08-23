# Loom — Palantir-Gotham-style Investing Dashboard

## Context

You want a decision-support dashboard for equity investing modeled on Palantir Gotham: ingest messy heterogeneous data (news, SEC filings, earnings call transcripts, quarterly earnings reports) — "weaving" multiple data sources into one thread — run it through an engine, and surface simple, actionable signals for a human, not a numeric price predictor. This is a brand-new, greenfield project called **Loom**, with no ties to the current `ebpf_packet_router` repo; it will live in a fresh directory on the Desktop (`~/Desktop/loom`).

Confirmed decisions from discussion:
- **Engine type**: NLP/LLM signal extraction — summarization, sentiment, risk-factor extraction, quarter-over-quarter "what changed" diffing. Explicitly not a quantitative price/earnings forecasting model.
- **Data sourcing**: both official APIs *and* direct web scraping, used as parallel first-class source types — not one as primary/fallback for the other. **Breadth is an explicit goal**: mirror Gotham's "ingest anything" posture by covering as many source *categories* as practical — core text sources (news, filings, transcripts, earnings PRs), regulatory/institutional data (insider transactions, institutional holdings, short interest), and alt-data signals (patents, search trends, app rankings, hiring activity) — see "Source Catalog" below.
- **Cost constraint**: no paid API usage. Every source, official-API or scraped, must be free — this actively rules out some categories (e.g. Glassdoor, SimilarWeb) that only offer real data behind a paid tier; see "Source Catalog" for what's included/excluded and why.
- **Stack**: Python (FastAPI) backend, Postgres, React + TypeScript frontend.
- **Storage shape**: hybrid, not pure rows-and-columns — see "Storage Design" below. With the broadened source list, this now has **two** physical shapes (documents and structured facts), not one — see "Structured Facts" below.
- **Non-negotiable engineering constraint**: every part of the system is built for single responsibility, separation of concerns, and modularity — easy to modify, extend, and fix in isolation. This is enforced throughout via the concrete patterns in "Architecture Principles," not treated as a slogan.
- **Git workflow constraint**: do not run `git init`, `git commit`, or `git push` at any point during implementation. Write and edit files only — the user reviews the working tree themselves and handles all commits/pushes.

## Architecture Principles (applies to every phase)

These are the concrete mechanisms that make SRP / separation of concerns / modularity real rather than aspirational — every phase below is built through these seams, not around them:

- **Adapter pattern for ingestion** — one class per data source, all implementing the same `SourceAdapter` interface. A source's job is only "produce `RawDocumentDTO`s"; it knows nothing about Postgres, the engine, or other sources. Adding/removing/breaking one source never touches another.
- **Strategy pattern for engine steps** — sentiment extraction, risk-factor extraction, notable-quote extraction, and QoQ diffing are each an independent, independently-testable function/class with one job, orchestrated (not implemented) by `pipeline.py`. Prompts live separately from the LLM call code, so changing a prompt never touches API-call plumbing.
- **Repository pattern for data access** — `CompanyRepository`, `DocumentRepository`, `SignalRepository`, `WatchlistRepository` are the *only* code that knows SQL/ORM details. API routes and the engine call repositories, never raw sessions/queries directly — so the storage backend (see below) can change without rippling into business logic.
- **Thin routes, logic in services** — FastAPI route handlers only parse request → call a service/repository → serialize response. No business logic inline in routes.
- **Blob storage isolated behind an interface** — see "Storage Design": raw content persistence is a separate concern from structured querying, with its own interface (`BlobStore`) so the two can evolve (and be swapped) independently.
- **Config centralized** — one `config.py` (pydantic `Settings`) is the only place reading environment variables; nothing else calls `os.environ` directly.
- **Frontend mirrors the same split** — presentational components (`components/`) never fetch data directly; data-fetching hooks (`hooks/`) own that, calling a thin `api/` client layer. Pages compose hooks + components, no fetch logic in pages either.

## Storage Design: hybrid, not pure rows-and-columns

**The question worth answering explicitly: relational tables, a document store, or both?**

What the data actually looks like:
- Raw ingested content (10-K text, news articles, transcripts) is large, unstructured, and *shaped differently per source* — a transcript has speaker turns, a filing has sections, a news article has a byline. Forcing every source into fixed relational columns means either a wall of nullable fields or a blob column anyway.
- LLM extraction output arrives as JSON — storing it as JSON is the path of least friction.
- But the dashboard's actual query needs — "timeline for AAPL," "all high-confidence signals across my watchlist this week," sentiment trend over time — are joins, filters, and aggregations across companies/watchlists/signals/documents. This is exactly what SQL is good at and a pure document database is comparatively bad at (joins become application-level loops).

**Verdict: hybrid, split by responsibility, not a wholesale move to a document DB.** A pure document store (Mongo etc.) would sacrifice the relational query power the dashboard depends on, and adds a second database system to operate for a solo-dev MVP — not worth it. But treating "store the metadata" and "store the raw bytes" as one undifferentiated concern (a giant `raw_text` column in a relational row) *is* worth fixing now, since it's exactly the kind of seam SRP asks for:

1. **Structured/relational layer (Postgres)** — companies, watchlists, signals, entities, and each document's *metadata* (ticker, source_type, published_at, content_hash, doc_subtype) as real typed columns. This is what needs integrity, foreign keys, filtering, joins.
2. **Object layer (`BlobStore` interface)** — the actual raw content (full filing text, transcript JSON with speaker turns, article HTML) is *not* a giant column on the relational row. It's put through a small interface:
   ```python
   class BlobStore(ABC):
       def put(self, key: str, content: bytes, content_type: str) -> str: ...  # returns a URI
       def get(self, uri: str) -> bytes: ...
   ```
   Phase 1 implementation is `LocalFileBlobStore` (writes to `./data/blobs/` on disk) — zero operational overhead. `raw_documents` keeps a `blob_uri` column pointing at it instead of storing text inline. When/if this needs to scale (many large transcripts, multi-machine deployment), an `S3BlobStore`/MinIO implementation drops in behind the same interface with **no changes to ingestion adapters or the engine** — this is the concrete payoff of separating the concern now.
3. **Semi-structured metadata (Postgres JSONB)** stays for genuinely variable, low-query-need extras — adapter-specific fields (SEC accession number, Finnhub article ID), the raw LLM JSON response + prompt version on each signal. JSONB is the right middle ground: queryable via GIN index if ever needed, but doesn't force a schema migration for every new source's quirks.

This gives nearly all the flexibility benefit "object storage" was reaching for, without giving up Postgres's relational integrity/query power or standing up a second database.

**Why not go further and mirror Gotham's full Ontology (generalized objects/properties/links)?** Gotham's object layer is itself a semantic API sitting on top of still-mostly-columnar/blob physical storage (Spark/Parquet, raw files) — it's not "objects instead of rows," it's "objects as an abstraction over heterogeneous rows/blobs." Palantir needs that generalized abstraction because Foundry is a platform resold into arbitrary future customer domains with data shapes they don't control in advance (video, sensor telemetry, legacy relational schemas, geospatial), plus cross-source entity resolution (the same person/vehicle appearing in unrelated datasets). Loom keeps a bounded version of the same idea, not the fully generic one: with the source list now deliberately widened (see "Source Catalog"), Loom's raw inputs split into exactly **two** physical shapes — free-text documents and structured numeric/tabular facts (see "Structured Facts" below) — not an open-ended variety of unknown future formats. Two known shapes plus a small, known entity-resolution surface (company/person/sector) is a fundamentally smaller problem than Gotham's fully generic meta-object framework, and is what's built here. Revisit going further only if Loom later takes on genuinely unpredictable input shapes (e.g. call audio/video, or domains far outside equities) — not before.

### Structured Facts — the second physical shape

Broadening sources into regulatory/institutional data (insider transactions, institutional holdings, short interest) and alt-data (patent filings, search-trend indices, app-store rankings, job-posting counts) introduces data that is **not document-shaped** — "insider Jane Doe bought 5,000 shares at $142.30 on 2026-08-20" is a row of structured facts, not a blob of prose. Modeling it as `raw_documents` text would be a bad fit and would defeat the point of keeping the LLM engine focused on genuinely unstructured extraction. Instead, these sources land in a parallel table:

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

This keeps the same SRP discipline as the document side: a `FactRepository` is the only thing that touches this table, and — like `raw_documents` — it's a single unified shape that every fact-producing adapter conforms to, regardless of whether the underlying source is an official filing or a scrape. The engine can later treat facts as additional evidence for signals (e.g. "cluster of insider buying + a new risk factor in the same week" is a stronger `qoq_anomaly` than either alone) without needing to touch how facts are ingested or stored.

## Repo / Directory Structure

New monorepo at `~/Desktop/loom/`, two apps (`backend/`, `frontend/`), root `docker-compose.yml` (Postgres). Pin backend to **Python 3.11 or 3.12** in a virtualenv (this machine has Python 3.14 installed, but NLP/scraping deps lag behind brand-new Python releases). Docker/Compose already available for Postgres, so no local Postgres install needed.

```
loom/
├── docker-compose.yml            # postgres
├── Makefile                      # make up / ingest / migrate / test
├── backend/
│   ├── app/
│   │   ├── main.py                # FastAPI app + APScheduler startup
│   │   ├── config.py               # pydantic Settings — sole source of env vars
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
│   │   │   ├── llm_client.py        # Anthropic SDK wrapper — only place that calls the API
│   │   │   ├── prompts/             # sentiment.py, risk_extraction.py, notable_quotes.py, diff_summary.py
│   │   │   ├── pipeline.py          # orchestrates engine steps, contains no extraction logic itself
│   │   │   ├── diffing.py           # QoQ risk-factor diffing
│   │   │   └── signal_writer.py
│   │   └── scheduling/             # APScheduler jobs
│   ├── scripts/                    # seed_companies.py, ingest_once.py, run_engine_once.py
│   ├── alembic/                    # migrations
│   └── tests/                      # one test module per module above — enforced by the same seams
└── frontend/                        # Vite + React + TS
    └── src/
        ├── pages/                   # WatchlistPage, CompanyDetailPage, SignalFeedPage — composition only
        ├── components/               # TickerTable, TimelinePanel, SentimentTrendChart, RiskFactorDiffCard, SignalCard — presentational
        ├── hooks/                    # TanStack Query hooks — the only place that calls api/
        ├── api/                      # thin fetch client, no UI logic
        └── styles/theme.css          # dark Gotham-esque palette
```

## Data Model (Postgres)

One unified `raw_documents` table across all four source kinds is the key relational design choice — every ingestion adapter just needs to produce rows of this shape (with content routed through `BlobStore`, not inlined), which is what makes the pluggable-adapter pattern work cleanly:

- `companies` (ticker, name, cik, sector, exchange)
- `watchlists` / `watchlist_items`
- `raw_documents` (company_id, `source_type` enum: `sec_edgar_filing | news_api | scraped_transcript | scraped_earnings_report`, source_name, source_url, doc_subtype like `10-K`/`8-K`/`earnings_call`, `blob_uri` → points into `BlobStore` for the actual content, metadata JSONB for adapter-specific extras, `content_hash` unique per company for dedupe)
- `entities` / `entity_links` — simple join-table entity mentions (person/company/sector), no graph DB needed at this scale
- `signals` — the actionable output: `signal_type` enum (`sentiment_shift | new_risk_factor | notable_quote | qoq_anomaly | guidance_change`), summary, sentiment_score (-1..1), confidence (0..1), `evidence_quote` (verbatim excerpt), `source_document_id`, `compared_document_id` (for QoQ diffs), metadata JSONB (raw LLM response + prompt version)

Every signal must be traceable back to an exact document/quote — this provenance requirement is central to the Gotham-style "why does this matter" feel.

## Ingestion Layer

Two adapter interfaces, split by output shape (SRP: an adapter has exactly one job — produce documents, or produce facts, never both):

```python
class DocumentSourceAdapter(ABC):
    def fetch(self, ticker: str, since: datetime | None) -> list[RawDocumentDTO]: ...

class FactSourceAdapter(ABC):
    def fetch(self, ticker: str, since: datetime | None) -> list[StructuredFactDTO]: ...
```

Both register in `ingestion/registry.py` keyed by `source_type`. A generic `ingest_all(ticker)` loop calls every registered adapter, routes `RawDocumentDTO`s through `BlobStore.put()` + `DocumentRepository`, and routes `StructuredFactDTO`s through `FactRepository` — adding a new source of either shape later means writing one adapter and registering it, with no changes to storage or the engine.

### Source Catalog

All sources below are **free** (no paid API tier used anywhere) and are wired in as parallel, first-class sources — none is a fallback for another. Grouped by category and the phase that introduces them:

**Core text sources (Phase 1–4, already planned):**
| Source | Type | Adapter | Notes |
|---|---|---|---|
| SEC EDGAR (10-K/10-Q/8-K) | Official API | `sec_edgar.py` | Free, no key, just an honest `User-Agent` header. `data.sec.gov` submissions + full-text search. |
| Finnhub company news | Official API | `news_api.py` | Free tier, ticker-scoped `/company-news`. |
| Earnings call transcripts | Scrape | `scrapers/earnings_transcript_motley_fool.py` | Real ToS exposure — see existing note below; rate-limited, robots.txt-checked. |
| Earnings press releases | Scrape | `scrapers/earnings_pr_scraper.py` | Company IR pages / BusinessWire; company-authored public releases, pairs with the matching 8-K. |

**Regulatory/institutional (Phase 5, new — all official SEC/FINRA data, free):**
| Source | Type | Adapter | Notes |
|---|---|---|---|
| SEC Form 4 (insider transactions) | Official API | `facts/sec_form4.py` | Same EDGAR base client, new doc type; outputs `StructuredFactDTO` (`insider_transaction`) — buyer/seller, role, shares, price, date. |
| SEC 13F (institutional holdings) | Official API | `facts/sec_13f.py` | Quarterly institutional position filings via EDGAR; outputs `institutional_holding` facts (holder name, shares, value). |
| SEC DEF 14A (proxy statements) | Official API | `sec_edgar.py` (new doc_subtype) | Text document — board/exec comp disclosures; fits `raw_documents`, not facts. |
| FINRA short interest | Official, free download | `facts/finra_short_interest.py` | FINRA publishes bi-monthly short-interest files publicly; outputs `short_interest` facts (% of float, days-to-cover). |

**Alt-data signals (Phase 6, new — free APIs/scrapes only):**
| Source | Type | Adapter | Notes |
|---|---|---|---|
| USPTO PatentsView | Official API | `facts/uspto_patents.py` | Free, official government API; `patent_filing` facts (filing/grant counts, tech category) as an innovation-activity proxy. |
| Google Trends | Scrape (public, unofficial) | `facts/google_trends.py` | Via the public Trends interface (e.g. `pytrends`) — no login/paywall, low legal risk since it's just querying a public consumer tool; `search_trend_index` facts. |
| Company careers pages | Scrape | `scrapers/careers_page_scraper.py` | First-party (each company's own site) — lowest third-party-ToS risk of the scrapers; `job_posting_count` facts as a hiring-trend proxy. Most fragile to maintain (per-company HTML), so scoped last. |
| App Store / Google Play listings | Scrape | `scrapers/app_store_scraper.py` | Public consumer-facing listing pages, no login; `app_store_ranking` facts (category rank, rating, review count) for companies with consumer apps. |

**Explicitly excluded for now — flagged, not silently dropped:** Glassdoor and SimilarWeb were considered for alt-data but are excluded because meaningful data from either sits behind a **paid** API, which conflicts with the no-paid-API constraint — their free public pages give only token/heavily-limited data, and Glassdoor's ToS + anti-scraping measures make scraping it a poor risk/reward trade. Revisit only if the no-paid-API constraint changes. Market/analyst data (analyst ratings, options flow) and social/retail sentiment (Reddit, X/Twitter, StockTwits) were also discussed but deprioritized by you in favor of regulatory/institutional and alt-data — they remain a natural Phase 7+ if wanted later, noting that most quality options-flow/analyst-ratings feeds are paid, and social scraping (Reddit/X) carries the heaviest ToS/rate-limit friction of any category here.

Shared scraper infrastructure (`scrapers/base_scraper.py`): robots.txt check per domain, per-domain rate limiting, an honest identifying User-Agent (never spoofing a browser to evade blocks), `httpx` + `selectolax`/BeautifulSoup parsing, and per-scraper try/except isolation so one broken selector never takes down any other source — increasingly important as the source count grows.

## Engine (raw documents → signals)

The engine runs in two stages: **per-document extraction** (triggered as each document/fact is ingested) and a **scheduled correlation sweep** (periodic, cross-document). Both follow one guiding principle throughout: **deterministic code handles anything that's arithmetic or structural comparison; the LLM is reserved for what genuinely requires language judgment** (sentiment nuance, quote selection, narrative synthesis). This keeps cost proportional to actual signal, keeps output auditable, and keeps each step small enough to satisfy SRP.

### Stage 1 — per-document extraction

`engine/pipeline.py` routes each new document/fact through independent, single-purpose steps: sentiment extraction, risk-factor extraction (if filing), notable-quote extraction (if transcript/news), and — for 10-K/10-Q — a diff against the prior same-subtype filing; for `structured_facts`, simple threshold rules (e.g. short interest +20% MoM, ≥3 insider sells in a week) run with no LLM involved at all. `pipeline.py` only orchestrates; each step is its own function/module, independently testable/replaceable.

- **Chunking is structural, not arbitrary**: long filings are split by their actual section headings (`Item 1A`, `Item 7`), transcripts by speaker turn, so no prompt ever sees a mid-sentence cut; very long documents get a map-reduce pass (summarize each section, then one pass over the summaries).
- LLM calls go through `engine/llm_client.py` (Anthropic SDK), always requesting schema-constrained structured output parsed into Pydantic models, with one retry on a parse failure before logging and dropping. This is the *only* module that talks to the Anthropic API. **Model routing by task**: a faster/cheaper model for high-volume routine news sentiment, the stronger model for filings/transcripts/diffing/synthesis where nuance matters more — a cost lever as much as a quality one.
- **QoQ "what changed" diffing** (`engine/diffing.py`) — the flagship feature — is two-stage rather than "feed both filings to the LLM": (1) deterministic section extraction + paragraph-level similarity matching (difflib/TF-IDF) to cheaply pair "same risk, reworded" vs. "genuinely new/dropped"; (2) LLM pass only on the paragraphs that didn't match cleanly, asking whether it's new/reworded/dropped and why it matters, producing an auditable `qoq_anomaly`/`new_risk_factor` signal pointing at both documents. Every other future text-comparison need should follow this same two-stage template, not a one-shot LLM call.
- `sentiment_score` (-1..1) and `confidence` (0..1, blending the LLM's self-reported confidence with a heuristic discount for e.g. truncated/chunked text) are stored per signal.
- `signal_writer.py` is the only module that persists engine output — it calls `SignalRepository`/`FactRepository`, so extraction steps never touch the database directly.
- **Idempotency**: each engine run is keyed by `(document_id, prompt_version)` — re-running with an unchanged prompt version is a no-op; deliberately bumping `prompt_version` (stored in `signals.metadata`) is the only way to force reprocessing, so prompt iteration never silently duplicates signals.

### Stage 2 — correlation sweep (the actual "Gotham" behavior)

A single signal in isolation is weak evidence. The real value is noticing when independent weak signals line up — a cluster of insider selling, a negative sentiment shift, and a new risk factor all inside the same window is much stronger evidence than any one alone. `engine/correlation.py` runs on a schedule (not per-document), scanning each watchlist ticker's recent `signals` + `structured_facts` in a rolling window (e.g. 7–14 days):
1. **Deterministic rule matching** (cheap, runs every sweep for every ticker) decides *whether* a cluster is interesting — e.g. "≥2 signal types + ≥1 corroborating fact type within N days."
2. **Only when a rule fires**, an LLM synthesis call writes the composite "why this matters" narrative, explicitly citing the underlying signal/fact IDs it ties together, producing a new composite signal (or an alert distinct from raw signals) pointing at its sources.

This keeps LLM spend proportional to how much is actually happening on a given ticker, not to how many tickers are watched.

### Ranking

Every signal gets a `priority` score for the dashboard feed (beyond `confidence` alone), combining: confidence, a per-signal-type weight (a deterministic threshold-rule fact is more trustworthy than an LLM sentiment read, and should rank accordingly), recency, and corroboration count (composite signals from Stage 2 rank above lone signals). This is what keeps the Signal Feed a ranked short list rather than a firehose of everything the engine noticed.

### Evaluation

Because LLM output quality is inherently fuzzy, keep a small hand-labeled golden set of real documents (a 10-K with known risk changes, a transcript with a known tone shift) from Phase 2 onward, and run it as a regression check before shipping any prompt change — cheap insurance against silently degrading extraction quality over time.

## Backend API (FastAPI)

`watchlists` CRUD, `companies` list/detail, `companies/{ticker}/timeline` (merged documents + signals), `companies/{ticker}/sentiment` (time series), `signals` feed with filters (ticker/type/since/min_confidence), `documents/{id}` (source drill-down, fetches content via `BlobStore`), `search`, plus dev-convenience `admin/ingest/{ticker}` and `admin/engine/run/{ticker}` to trigger runs manually. Single-user MVP: no auth, bind to localhost; `owner_email` on watchlists left in place for later multi-user support. Every route calls a repository — none touch SQLAlchemy sessions directly.

## Frontend (React + TypeScript)

Vite + React + TS, TanStack Query for server state (fits a fetch-and-display dashboard, no need for Redux), plain CSS variables for a dark, high-contrast "Gotham" theme (monospace for data, cyan/amber accents) — deliberately closer to an analyst terminal than a consumer app. Desktop-first for the MVP; mobile is an explicit non-goal for now, not an oversight. Data-fetching lives only in `hooks/`; `components/` stay presentational and reusable.

### UX Flow — what an investor sees and does

**`WatchlistPage` (home)**: one row per tracked ticker — ticker/name, sector, a sentiment sparkline (~90 days), and a **priority badge** (driven by the engine's `priority` score) showing whether anything noteworthy happened since last visit. This is the "scan in seconds, act on what matters" home view the whole product exists to produce. "Add ticker" is a simple search-and-add modal; a freshly-added ticker shows a "gathering data…" state while first ingestion/engine runs complete, rather than a blank page.

**`CompanyDetailPage`**: `SentimentTrendChart` at top (sentiment over time with markers per contributing signal, clickable), a merged `TimelinePanel` below (filings/news/transcripts/press-releases/signals interleaved chronologically, source-type icon + sentiment color per card), a `RiskFactorDiffCard` whenever a new 10-K/10-Q lands (plain-English "N new, M dropped" risk factors, each with verbatim excerpt + why it matters), and — once Phase 5 ships — an Activity side panel surfacing recent insider transactions/short-interest trend, visually distinct from the LLM-derived signals so the investor knows which kind of evidence they're looking at.

**`SignalFeedPage`**: the cross-ticker alerts inbox, ranked by `priority` (not chronological), filterable by ticker/type/min-confidence/date. Composite alerts from the correlation sweep are visually distinguished and rank above lone signals. Every card — no exception — expands to its exact evidence: verbatim quote, source link, timestamp, confidence. **Nothing is ever presented without a receipt**; the investor is always one click from verifying a claim rather than trusting the model. Reviewed signals can be marked/dismissed so the feed doesn't restate the same thing daily.

**Concrete actions available**: add/remove watchlist tickers; scan the ranked home view instead of reading everything; open any signal/diff card to inspect its evidence and judge it themselves; filter the Signal Feed on demand; manually trigger a fresh ingest/engine run per ticker (surfaced as a "refresh" button over the `admin/ingest`/`admin/engine/run` endpoints) rather than waiting for the schedule; search by ticker/company/exec name and jump straight there.

## Scheduling

**APScheduler running inside the FastAPI process** — not Celery/Redis — is the right call for a single user with a modest watchlist and infrequent ingestion (every few hours). Jobs registered in `scheduling/jobs.py`, started from `main.py`'s lifespan hook, calling the same ingestion/engine entry points the CLI scripts and admin routes use — one code path, three triggers. If this later needs to scale to many users/tickers, swap to Celery + Beat without touching ingestion/engine interfaces — not needed now.

## Phased Build Plan

**Phase 1 — Scaffold + SEC ingestion + raw data shell (no LLM yet).** Prove ingestion → BlobStore/Postgres → API → frontend end-to-end with real SEC data: docker-compose (Postgres), migrations for `companies`/`watchlists`/`watchlist_items`/`raw_documents`, `storage/blob_store.py` (`LocalFileBlobStore`), `ingestion/base.py` + `sec_edgar.py`, `repositories/`, seed/ingest CLI scripts, read-only API routes, a plain frontend list view (no charts/signals yet). Fully verifiable without an Anthropic API key.

**Phase 2 — LLM signal engine + alerts feed.** `engine/` module (llm_client, prompts, pipeline, diffing, signal_writer), migration adding `signals`/`entities`/`entity_links`, `SignalRepository`, `signals` API route, frontend `SignalFeedPage`/`SignalCard`/`SentimentTrendChart`/`RiskFactorDiffCard`. QoQ risk-factor diffing is the flagship deliverable here. Include a basic `priority` score on signals from the start (confidence + type weight + recency) so the feed is ranked, not a raw list. `engine/correlation.py` (the cross-signal sweep) can follow once Phase 5 gives it structured facts worth correlating against — noted here so it isn't lost, but its natural home is after Phase 5.

**Phase 3 — News API + first scraper (earnings call transcripts).** `news_api.py` (Finnhub), `scrapers/base_scraper.py`, `earnings_transcript_motley_fool.py`, wired into the registry; turn on APScheduler jobs so ingestion/engine run automatically instead of only via CLI.

**Phase 4 — Earnings report/press-release scraping + dashboard polish.** `earnings_pr_scraper.py` (IR pages/BusinessWire), cross-referencing press releases with their corresponding 8-K, frontend visual pass (theme, sidebar/topbar, source-type icons/color-coding on the timeline), `search` endpoint + search bar.

**Phase 5 — Regulatory/institutional breadth.** Migration adding `structured_facts` + `FactRepository`; `FactSourceAdapter` base added to `ingestion/base.py`; `facts/sec_form4.py`, `facts/sec_13f.py`, `sec_edgar.py` extended for DEF 14A text, `facts/finra_short_interest.py`. Deterministic threshold rules over facts (short-interest spike, insider-sell cluster) land here too — the first non-LLM signals. New `facts` API route exposing per-ticker fact series. Frontend: a compact "activity" panel on `CompanyDetailPage` surfacing recent insider transactions and short-interest trend alongside the existing timeline.

**Phase 6 — Alt-data breadth + correlation sweep.** `facts/uspto_patents.py`, `facts/google_trends.py`, `scrapers/app_store_scraper.py`, `scrapers/careers_page_scraper.py` (built last — most fragile, per-company HTML). This is also the natural point to add `engine/correlation.py` (Stage 2 of the engine design above): with a real mix of signals and facts now flowing in, the scheduled cross-signal sweep has enough to correlate against — deterministic rule matching for "is this cluster interesting," LLM synthesis only when a rule fires, producing composite alerts that outrank lone signals.

## Verification (Phase 1)

1. `docker compose up -d` — Postgres up, confirm via `docker compose ps`.
2. `alembic upgrade head` — apply migrations, confirm tables via `psql ... -c '\dt'`.
3. `python -m scripts.seed_companies` — seed a couple of tickers (e.g. AAPL, MSFT) + default watchlist.
4. `python -m scripts.ingest_once --ticker AAPL` — live SEC EDGAR fetch, confirm rows land in `raw_documents` and files land in `./data/blobs/`.
5. `uvicorn app.main:app --reload` then `curl localhost:8000/companies/AAPL/timeline` — confirm ingested filings return as JSON with correct `source_url`/`published_at`.
6. `npm run dev` in `frontend/` — browse to the watchlist page, open AAPL, confirm the same filings render with working "view source" links to SEC EDGAR.
7. Re-run `ingest_once` and confirm no duplicate rows (`content_hash` dedupe) — the one non-obvious failure mode worth checking before Phase 2.

## Critical Files

- `backend/app/ingestion/base.py` — the `SourceAdapter`/`RawDocumentDTO` contract every source (SEC, news, scrapers) must conform to.
- `backend/app/storage/blob_store.py` — the `BlobStore` interface separating raw content persistence from relational metadata; the concrete seam that makes the storage design swappable.
- `backend/app/repositories/` — the only layer allowed to touch SQLAlchemy directly; keeps routes/engine decoupled from storage details.
- `backend/app/models/document.py` + its migration — the unified `raw_documents` metadata shape all sources land in.
- `backend/app/engine/pipeline.py` and `backend/app/engine/diffing.py` — the core signal-extraction logic and the QoQ diffing that's the product's signature feature.
- `backend/app/ingestion/sec_edgar.py` — first concrete adapter, unblocks all of Phase 1.
- `backend/app/models/structured_fact.py` + its migration — the second physical shape (Phase 5+), parallel to `raw_documents`, that every regulatory/alt-data fact source lands in.
- `frontend/src/pages/CompanyDetailPage.tsx` (with `TimelinePanel`, `SentimentTrendChart`, `RiskFactorDiffCard`) — the page most representative of the target UX.

## Note on scraping risk

Scraping earnings call transcripts and press releases (per your preference, as first-class sources rather than fallback) carries real ToS and fragility risk depending on the target site — this plan isolates that risk behind a shared, rate-limited, robots.txt-aware scraper base so individual sources can be swapped or dropped without redesigning ingestion, but the legal exposure itself is a judgment call only you can make per source when the time comes.
