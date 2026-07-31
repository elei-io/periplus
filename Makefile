.PHONY: sync check setup catalogue-check api acquisition-worker ingestion-worker materialization-worker housekeeping-worker db-revision compose-up compose-down compose-reset-control

sync:
	cd backend && uv sync
	npm install

check:
	cd backend && uv run python -m compileall src/atlas
	cd backend && uv run python -m unittest discover -s tests
	cd backend && PYTHONPATH=../packages/atlas-python-sdk/src uv run python -m unittest discover -s ../packages/atlas-python-sdk/tests
	npm run check:packages
	npm run test:packages
	npm run check:web
	npm run build:web

setup:
	cd backend && uv run atlas-setup

catalogue-check:
	cd backend && \
	ATLAS_DUCKLAKE_ALIAS="$${ATLAS_DUCKLAKE_ALIAS:-atlas}" \
	ATLAS_DUCKLAKE_METADATA_PATH="$${ATLAS_DUCKLAKE_METADATA_PATH:-postgres:dbname=atlas_test host=127.0.0.1 port=$${ATLAS_POSTGRES_PORT:-55432} user=atlas password=atlas_local}" \
	ATLAS_DUCKLAKE_METADATA_SCHEMA="$${ATLAS_DUCKLAKE_METADATA_SCHEMA:-ducklake}" \
	ATLAS_DUCKLAKE_DATA_PATH="$${ATLAS_DUCKLAKE_DATA_PATH:-$(CURDIR)/.atlas/lake/}" \
	ATLAS_DUCKDB_EXTENSION_PATH="$${ATLAS_DUCKDB_EXTENSION_PATH:-$(CURDIR)/../atlas-duckdb-extension/build/release/extension/atlas/atlas.duckdb_extension}" \
	uv run python -m atlas.platform.catalogue check

api:
	cd backend && uv run fastapi dev src/atlas/entrypoints/api.py

acquisition-worker:
	cd backend && uv run atlas-worker acquisition

ingestion-worker:
	cd backend && uv run atlas-worker ingestion

materialization-worker:
	cd backend && uv run atlas-worker materialization

housekeeping-worker:
	cd backend && uv run atlas-worker housekeeping

db-revision:
	cd backend && uv run alembic -c src/atlas/platform/postgres/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down

# Atlas is greenfield: reset the complete disposable local data plane.
compose-reset-control:
	docker compose down --volumes
