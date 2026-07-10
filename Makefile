.PHONY: sync check catalogue-bootstrap catalogue-check catalogue-test-postgres repository-test-s3 api ingestor cli db-upgrade db-revision compose-up compose-down

sync:
	cd backend && uv sync

check:
	cd backend && uv run python -m compileall actions api cli db dom repository tasks urls data_schemas query_schemas crawl_policies metrics observability worker
	cd backend && uv run python -m unittest discover -s tests

catalogue-bootstrap:
	cd backend && uv run python -m repository.ducklake bootstrap

catalogue-check:
	cd backend && uv run python -m repository.ducklake check

catalogue-test-postgres:
	docker compose up -d --wait atlas-postgres
	cd backend && ATLAS_TEST_DATABASE_URL="$${ATLAS_TEST_DATABASE_URL:-postgresql://$${POSTGRES_USER:-atlas}:$${POSTGRES_PASSWORD:-atlas}@127.0.0.1:$${POSTGRES_PORT:-5432}/$${POSTGRES_DB:-atlas}}" uv run python -m unittest tests.test_catalogue_postgres -v

repository-test-s3:
	docker compose up -d --wait atlas-minio
	cd backend && ATLAS_TEST_MINIO=1 uv run python -m unittest tests.test_repository.S3ObjectStoreTests -v

api:
	cd backend && uv run fastapi dev api/app.py

ingestor:
	cd backend && uv run python -m repository.worker

cli:
	cd backend && uv run atlas --help

db-upgrade:
	cd backend && uv run alembic -c db/alembic.ini upgrade head

db-revision:
	cd backend && uv run alembic -c db/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down
