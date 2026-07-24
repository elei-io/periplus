.PHONY: sync check console-check sdk-check setup catalogue-check catalogue-benchmark verify-remote-runtime worker-independence-smoke worker-horizontal-smoke reliability-check docs-diagrams api acquisition-worker ingestion-worker catalogue-relay-worker materialization-worker housekeeping-worker cli db-revision compose-up compose-down

sync:
	cd backend && uv sync
	npm install

check:
	cd backend && uv run python -m compileall actions agents api catalogue catalogue_relay cli config control db dom materialization observability repository runtime workers
	cd backend && uv run python -m unittest discover -s tests
	$(MAKE) sdk-check
	$(MAKE) console-check

sdk-check:
	cd backend && uv run python -m unittest discover -s ../sdk/tests

console-check:
	npm run check:console
	npm run test:console

setup:
	cd backend && uv run atlas-setup

catalogue-check:
	cd backend && uv run python -m repository.catalogue check

catalogue-benchmark:
	cd backend && uv run python -m repository.catalogue benchmark

verify-remote-runtime:
	cd backend && uv run python scripts/verify_remote_runtime.py

worker-independence-smoke:
	cd backend && uv run python ../scripts/verify-worker-independence.py

worker-horizontal-smoke:
	cd backend && uv run python ../scripts/verify-worker-horizontal-safety.py

reliability-check:
	docker compose up -d --wait
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

catalogue-relay-worker:
	cd backend && uv run atlas-worker catalogue-relay

materialization-worker:
	cd backend && uv run atlas-worker materialization

housekeeping-worker:
	cd backend && uv run atlas-worker housekeeping

cli:
	npm run atlas

db-revision:
	cd backend && uv run alembic -c db/alembic.ini revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d --wait

compose-down:
	docker compose down
