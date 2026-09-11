.PHONY: sync check setup catalogue-check query-benchmark api crawler ingestor materializer janitor db-revision compose-up compose-down compose-reset

sync:
	cd packages/periplus && uv sync
	npm install

check:
	cd packages/periplus && uv run python -m compileall src/periplus
	cd packages/periplus && uv run python -m unittest discover -s tests
	cd packages/periplus && uv run --with ../periplus-python-sdk python -m unittest discover -s ../periplus-python-sdk/tests
	npm run check:packages
	npm run test:packages
	npm run check:public
	npm run build:public
	npm run check:admin
	npm run build:admin

setup:
	cd packages/periplus && uv run periplus-setup

catalogue-check:
	cd packages/periplus && \
	PERIPLUS_DUCKLAKE_ALIAS="$${PERIPLUS_DUCKLAKE_ALIAS:-periplus}" \
	PERIPLUS_DUCKLAKE_METADATA_PATH="$${PERIPLUS_DUCKLAKE_METADATA_PATH:-postgres:dbname=lake host=127.0.0.1 port=$${LAKE_POSTGRES_PORT:-55433} user=lake password=lake_local}" \
	PERIPLUS_DUCKLAKE_METADATA_SCHEMA="$${PERIPLUS_DUCKLAKE_METADATA_SCHEMA:-ducklake}" \
	PERIPLUS_DUCKLAKE_DATA_PATH="$${PERIPLUS_DUCKLAKE_DATA_PATH:-s3://lake/}" \
	PERIPLUS_DUCKLAKE_S3_ENDPOINT="$${PERIPLUS_DUCKLAKE_S3_ENDPOINT:-127.0.0.1:$${LAKE_S3_PORT:-7070}}" \
	PERIPLUS_DUCKLAKE_S3_REGION="$${PERIPLUS_DUCKLAKE_S3_REGION:-us-east-1}" \
	PERIPLUS_DUCKLAKE_S3_KEY_ID="$${PERIPLUS_DUCKLAKE_S3_KEY_ID:-periplus-local}" \
	PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY="$${PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY:-lake-secret}" \
	PERIPLUS_DUCKLAKE_S3_URL_STYLE="$${PERIPLUS_DUCKLAKE_S3_URL_STYLE:-path}" \
	PERIPLUS_DUCKLAKE_S3_USE_SSL="$${PERIPLUS_DUCKLAKE_S3_USE_SSL:-false}" \
	uv run python -m periplus.platform.catalogue check

query-benchmark:
	cd packages/periplus && \
	PERIPLUS_DUCKLAKE_ALIAS="$${PERIPLUS_DUCKLAKE_ALIAS:-periplus}" \
	PERIPLUS_DUCKLAKE_METADATA_PATH="$${PERIPLUS_DUCKLAKE_METADATA_PATH:-postgres:dbname=lake host=127.0.0.1 port=$${LAKE_POSTGRES_PORT:-55433} user=lake password=lake_local}" \
	PERIPLUS_DUCKLAKE_METADATA_SCHEMA="$${PERIPLUS_DUCKLAKE_METADATA_SCHEMA:-ducklake}" \
	PERIPLUS_DUCKLAKE_DATA_PATH="$${PERIPLUS_DUCKLAKE_DATA_PATH:-s3://lake/}" \
	PERIPLUS_DUCKLAKE_S3_ENDPOINT="$${PERIPLUS_DUCKLAKE_S3_ENDPOINT:-127.0.0.1:$${LAKE_S3_PORT:-7070}}" \
	PERIPLUS_DUCKLAKE_S3_REGION="$${PERIPLUS_DUCKLAKE_S3_REGION:-us-east-1}" \
	PERIPLUS_DUCKLAKE_S3_KEY_ID="$${PERIPLUS_DUCKLAKE_S3_KEY_ID:-periplus-local}" \
	PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY="$${PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY:-lake-secret}" \
	PERIPLUS_DUCKLAKE_S3_URL_STYLE="$${PERIPLUS_DUCKLAKE_S3_URL_STYLE:-path}" \
	PERIPLUS_DUCKLAKE_S3_USE_SSL="$${PERIPLUS_DUCKLAKE_S3_USE_SSL:-false}" \
	uv run python scripts/query_benchmark.py $(ARGS)

api:
	cd packages/periplus && uv run fastapi dev src/periplus/entrypoints/api.py

crawler:
	cd packages/periplus && uv run periplus-worker crawler

ingestor:
	cd packages/periplus && uv run periplus-worker ingestor

materializer:
	cd packages/periplus && uv run periplus-worker materializer

janitor:
	cd packages/periplus && uv run periplus-worker janitor

db-revision:
	cd packages/periplus && uv run alembic -c src/periplus/platform/postgres/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down

# Periplus is greenfield: reset the complete disposable local data plane.
compose-reset:
	docker compose down --volumes
