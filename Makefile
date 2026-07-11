.PHONY: sync check setup catalogue-check catalogue-test-postgres repository-test-s3 api runtime-worker repository-worker cli db-revision compose-up compose-down

sync:
	cd backend && uv sync

check:
	cd backend && uv run python -m compileall actions api cli config control db dom observability repository runtime
	cd backend && uv run python -m unittest discover -s tests

setup:
	cd backend && uv run atlas-setup

catalogue-check:
	cd backend && uv run python -m repository.catalogue check

catalogue-test-postgres:
	docker compose up -d --wait atlas-postgres
	cd backend && ATLAS_TEST_DATABASE_URL="$${ATLAS_TEST_DATABASE_URL:-postgresql://$${POSTGRES_USER:-atlas}:$${POSTGRES_PASSWORD:-atlas}@127.0.0.1:$${POSTGRES_PORT:-5432}/$${POSTGRES_DB:-atlas}}" uv run python -m unittest tests.test_catalogue_postgres -v

repository-test-s3:
	docker compose up -d --wait atlas-minio
	cd backend && ATLAS_TEST_MINIO=1 uv run python -m unittest tests.test_repository.S3ObjectStoreTests -v

api:
	cd backend && uv run fastapi dev api/app.py

runtime-worker:
	cd backend && uv run python -m runtime.worker

repository-worker:
	cd backend && uv run python -m repository.worker

cli:
	cd backend && uv run atlas --help

db-revision:
	cd backend && uv run alembic -c db/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down
