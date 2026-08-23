# Loom

A Palantir-Gotham-inspired decision-support dashboard for equity investing. Loom ingests news, SEC filings, earnings call transcripts, and quarterly earnings reports/press releases, runs them through an NLP/LLM engine, and surfaces simple, actionable signals — sentiment shifts, new risk factors, notable quotes, quarter-over-quarter anomalies — for a human to act on. It is explicitly **not** a price-prediction engine.

See [`docs/plan.md`](docs/plan.md) for the full architecture and phased build plan (mirrors the approved implementation plan).

## Quickstart (Phase 1)

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

Then, in another terminal:

```bash
cd frontend
npm install
npm run dev
```

## Architecture principles

Every module in this codebase follows single responsibility, separation of concerns, and modularity as hard constraints, not aspirations:

- **Adapter pattern** for ingestion — one class per data source (`app/ingestion/`), all implementing `DocumentSourceAdapter` or `FactSourceAdapter`.
- **Repository pattern** for data access — `app/repositories/` is the only code that touches SQLAlchemy directly.
- **Blob storage isolated behind an interface** — `app/storage/blob_store.py` separates raw content persistence from relational metadata.
- **Thin API routes** — routes parse requests, call repositories/services, serialize responses. No business logic inline.
- **Centralized config** — `app/config.py` is the only place reading environment variables.

## Project layout

```
loom/
├── docker-compose.yml       # Postgres
├── backend/                 # FastAPI + SQLAlchemy + Alembic
└── frontend/                 # Vite + React + TypeScript
```
