.PHONY: up down migrate seed ingest run-api run-frontend test

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd backend && alembic upgrade head

seed:
	cd backend && python -m scripts.seed_companies

ingest:
	cd backend && python -m scripts.ingest_once --ticker $(TICKER)

run-api:
	cd backend && uvicorn app.main:app --reload

run-frontend:
	cd frontend && npm run dev

test:
	cd backend && pytest
