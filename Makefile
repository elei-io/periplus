.PHONY: sync check setup catalogue-check query-benchmark api crawler ingestor materializer janitor db-revision compose-up compose-down compose-reset

sync:
	cd backend && uv sync
	npm install

check:
	cd backend && uv run python -m compileall src/periplus
	cd backend && uv run python -m unittest discover -s tests
	cd backend && PYTHONPATH=../packages/periplus-python-sdk/src uv run python -m unittest discover -s ../packages/periplus-python-sdk/tests
	npm run check:packages
	npm run test:packages
	npm run check:web
	npm run build:web

setup:
	cd backend && uv run periplus-setup

catalogue-check:
	cd backend && \
	PERIPLUS_DUCKLAKE_ALIAS="$${PERIPLUS_DUCKLAKE_ALIAS:-periplus}" \
	PERIPLUS_DUCKLAKE_METADATA_PATH="$${PERIPLUS_DUCKLAKE_METADATA_PATH:-postgres:dbname=lake host=127.0.0.1 port=$${LAKE_POSTGRES_PORT:-55433} user=lake password=lake_local}" \
	PERIPLUS_DUCKLAKE_METADATA_SCHEMA="$${PERIPLUS_DUCKLAKE_METADATA_SCHEMA:-ducklake}" \
	PERIPLUS_DUCKLAKE_DATA_PATH="$${PERIPLUS_DUCKLAKE_DATA_PATH:-s3://lake/}" \
	PERIPLUS_DUCKLAKE_S3_ENDPOINT="$${PERIPLUS_DUCKLAKE_S3_ENDPOINT:-127.0.0.1:$${LAKE_S3_PORT:-7070}}" \
	PERIPLUS_DUCKLAKE_S3_REGION="$${PERIPLUS_DUCKLAKE_S3_REGION:-us-east-1}" \
	PERIPLUS_DUCKLAKE_S3_KEY_ID="$${PERIPLUS_DUCKLAKE_S3_KEY_ID:-lake}" \
	PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY="$${PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY:-lake-secret}" \
	PERIPLUS_DUCKLAKE_S3_URL_STYLE="$${PERIPLUS_DUCKLAKE_S3_URL_STYLE:-path}" \
	PERIPLUS_DUCKLAKE_S3_USE_SSL="$${PERIPLUS_DUCKLAKE_S3_USE_SSL:-false}" \
	PERIPLUS_DUCKDB_EXTENSION_PATH="$${PERIPLUS_DUCKDB_EXTENSION_PATH:-$(CURDIR)/../periplus-duckdb-extension/build/release/extension/periplus/periplus.duckdb_extension}" \
	uv run python -m periplus.platform.catalogue check

query-benchmark:
	cd backend && \
	PERIPLUS_DUCKLAKE_ALIAS="$${PERIPLUS_DUCKLAKE_ALIAS:-periplus}" \
	PERIPLUS_DUCKLAKE_METADATA_PATH="$${PERIPLUS_DUCKLAKE_METADATA_PATH:-postgres:dbname=lake host=127.0.0.1 port=$${LAKE_POSTGRES_PORT:-55433} user=lake password=lake_local}" \
	PERIPLUS_DUCKLAKE_METADATA_SCHEMA="$${PERIPLUS_DUCKLAKE_METADATA_SCHEMA:-ducklake}" \
	PERIPLUS_DUCKLAKE_DATA_PATH="$${PERIPLUS_DUCKLAKE_DATA_PATH:-s3://lake/}" \
	PERIPLUS_DUCKLAKE_S3_ENDPOINT="$${PERIPLUS_DUCKLAKE_S3_ENDPOINT:-127.0.0.1:$${LAKE_S3_PORT:-7070}}" \
	PERIPLUS_DUCKLAKE_S3_REGION="$${PERIPLUS_DUCKLAKE_S3_REGION:-us-east-1}" \
	PERIPLUS_DUCKLAKE_S3_KEY_ID="$${PERIPLUS_DUCKLAKE_S3_KEY_ID:-lake}" \
	PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY="$${PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY:-lake-secret}" \
	PERIPLUS_DUCKLAKE_S3_URL_STYLE="$${PERIPLUS_DUCKLAKE_S3_URL_STYLE:-path}" \
	PERIPLUS_DUCKLAKE_S3_USE_SSL="$${PERIPLUS_DUCKLAKE_S3_USE_SSL:-false}" \
	PERIPLUS_DUCKDB_EXTENSION_PATH="$${PERIPLUS_DUCKDB_EXTENSION_PATH:-$(CURDIR)/../periplus-duckdb-extension/build/release/extension/periplus/periplus.duckdb_extension}" \
	uv run python scripts/query_benchmark.py $(ARGS)

api:
	cd backend && uv run fastapi dev src/periplus/entrypoints/api.py

crawler:
	cd backend && uv run periplus-worker crawler

ingestor:
	cd backend && uv run periplus-worker ingestor

materializer:
	cd backend && uv run periplus-worker materializer

janitor:
	cd backend && uv run periplus-worker janitor

db-revision:
	cd backend && uv run alembic -c src/periplus/platform/postgres/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down

# Periplus is greenfield: reset the complete disposable local data plane.
compose-reset:
	docker compose down --volumes
