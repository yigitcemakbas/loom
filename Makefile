.PHONY: up down migrate seed seed-dev-account ingest run-api run-frontend test

up:
	docker compose up -d

down:
	docker compose down

migrate:
	cd backend && alembic upgrade head

seed:
	cd backend && python -m scripts.seed_companies

# A verified local account, so a fresh database is usable without walking
# through the emailed sign-in code. Credentials are in the script.
seed-dev-account:
	cd backend && python -m scripts.seed_dev_account

ingest:
	cd backend && python -m scripts.ingest_once --ticker $(TICKER)

run-api:
	cd backend && uvicorn app.main:app --reload

run-frontend:
	cd frontend && npm run dev

test:
	cd backend && pytest
