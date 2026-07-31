.PHONY: sync check setup catalogue-check api crawler ingestor materializer janitor db-revision compose-up compose-down compose-reset

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
	ATLAS_DUCKLAKE_METADATA_PATH="$${ATLAS_DUCKLAKE_METADATA_PATH:-postgres:dbname=lake host=127.0.0.1 port=$${LAKE_POSTGRES_PORT:-55433} user=lake password=lake_local}" \
	ATLAS_DUCKLAKE_METADATA_SCHEMA="$${ATLAS_DUCKLAKE_METADATA_SCHEMA:-ducklake}" \
	ATLAS_DUCKLAKE_DATA_PATH="$${ATLAS_DUCKLAKE_DATA_PATH:-s3://lake/}" \
	ATLAS_DUCKLAKE_S3_ENDPOINT="$${ATLAS_DUCKLAKE_S3_ENDPOINT:-127.0.0.1:$${LAKE_S3_PORT:-7070}}" \
	ATLAS_DUCKLAKE_S3_REGION="$${ATLAS_DUCKLAKE_S3_REGION:-us-east-1}" \
	ATLAS_DUCKLAKE_S3_KEY_ID="$${ATLAS_DUCKLAKE_S3_KEY_ID:-lake}" \
	ATLAS_DUCKLAKE_S3_SECRET_ACCESS_KEY="$${ATLAS_DUCKLAKE_S3_SECRET_ACCESS_KEY:-lake-secret}" \
	ATLAS_DUCKLAKE_S3_URL_STYLE="$${ATLAS_DUCKLAKE_S3_URL_STYLE:-path}" \
	ATLAS_DUCKLAKE_S3_USE_SSL="$${ATLAS_DUCKLAKE_S3_USE_SSL:-false}" \
	ATLAS_DUCKDB_EXTENSION_PATH="$${ATLAS_DUCKDB_EXTENSION_PATH:-$(CURDIR)/../atlas-duckdb-extension/build/release/extension/atlas/atlas.duckdb_extension}" \
	uv run python -m atlas.platform.catalogue check

api:
	cd backend && uv run fastapi dev src/atlas/entrypoints/api.py

crawler:
	cd backend && uv run atlas-worker crawler

ingestor:
	cd backend && uv run atlas-worker ingestor

materializer:
	cd backend && uv run atlas-worker materializer

janitor:
	cd backend && uv run atlas-worker janitor

db-revision:
	cd backend && uv run alembic -c src/atlas/platform/postgres/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down

# Atlas is greenfield: reset the complete disposable local data plane.
compose-reset:
	docker compose down --volumes
