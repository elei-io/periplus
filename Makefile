.PHONY: sync check setup catalogue-check catalogue-benchmark catalogue-test-postgres repository-test-s3 resource-governor-smoke worker-independence-smoke worker-horizontal-smoke reliability-check docs-diagrams api acquisition-worker ingestion-worker materialization-worker maintenance-worker cli db-revision compose-up compose-down

sync:
	cd backend && uv sync

check:
	cd backend && uv run python -m compileall actions api cli config control db dom materialization observability repository runtime workers
	cd backend && uv run python -m unittest discover -s tests

setup:
	cd backend && uv run atlas-setup

catalogue-check:
	cd backend && uv run python -m repository.catalogue check

catalogue-benchmark:
	cd backend && uv run python -m repository.catalogue benchmark

catalogue-test-postgres:
	docker compose up -d --wait atlas-postgres
	cd backend && ATLAS_TEST_DATABASE_URL="$${ATLAS_TEST_DATABASE_URL:-postgresql://$${POSTGRES_USER:-atlas}:$${POSTGRES_PASSWORD:-atlas}@127.0.0.1:$${POSTGRES_PORT:-5432}/$${POSTGRES_DB:-atlas}}" uv run python -m unittest tests.test_catalogue_postgres -v

repository-test-s3:
	docker compose up -d --wait atlas-minio
	cd backend && ATLAS_TEST_MINIO=1 uv run python -m unittest tests.test_repository.S3ObjectStoreTests -v

resource-governor-smoke:
	cd backend && uv run python ../scripts/verify-resource-governor-reliability.py

worker-independence-smoke:
	cd backend && uv run python ../scripts/verify-worker-independence.py

worker-horizontal-smoke:
	cd backend && uv run python ../scripts/verify-worker-horizontal-safety.py

reliability-check:
	docker compose up -d --wait
	$(MAKE) resource-governor-smoke
	$(MAKE) worker-independence-smoke
	$(MAKE) worker-horizontal-smoke

docs-diagrams:
	./scripts/render-doc-diagrams.sh

api:
	cd backend && uv run fastapi dev api/app.py

acquisition-worker:
	cd backend && uv run atlas-worker acquisition

ingestion-worker:
	cd backend && uv run atlas-worker ingestion

materialization-worker:
	cd backend && uv run atlas-worker materialization

maintenance-worker:
	cd backend && uv run atlas-worker maintenance

cli:
	cd backend && uv run atlas --help

db-revision:
	cd backend && uv run alembic -c db/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down
