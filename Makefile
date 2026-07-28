.PHONY: sync check setup catalogue-check verify-remote-runtime api acquisition-worker ingestion-worker cdc-worker materialization-worker housekeeping-worker db-revision compose-up compose-down compose-reset-control

sync:
	cd backend && uv sync
	npm install

check:
	cd backend && uv run python -m compileall acquisition api cdc config control db dom materialization observability repository runtime workers
	cd backend && uv run python -m unittest discover -s tests
	npm run check:packages
	npm run test:packages
	npm run check:web
	npm run build:web

setup:
	cd backend && uv run atlas-setup

catalogue-check:
	cd backend && uv run python -m repository.catalogue check

verify-remote-runtime:
	cd backend && uv run python scripts/verify_remote_runtime.py

api:
	cd backend && uv run fastapi dev api/app.py

acquisition-worker:
	cd backend && uv run atlas-worker acquisition

ingestion-worker:
	cd backend && uv run atlas-worker ingestion

cdc-worker:
	cd backend && uv run atlas-worker cdc

materialization-worker:
	cd backend && uv run atlas-worker materialization

housekeeping-worker:
	cd backend && uv run atlas-worker housekeeping

db-revision:
	cd backend && uv run alembic -c db/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down

# Atlas is greenfield: use this after a baseline migration is replaced.
# This removes only local control-plane Postgres state. DuckLake, repository
# objects, and the NATS volume are left intact.
compose-reset-control:
	docker compose down --volumes atlas-test-postgres
