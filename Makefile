.PHONY: sync check console-check sdk-check duckdb-extension-configure duckdb-extension-check duckdb-extension-release duckdb-extension-clean setup catalogue-check catalogue-benchmark catalogue-load-synthetic analytical-ground-truth-test analytical-ground-truth-plan analytical-ground-truth-load analytical-ground-truth-verify analytical-ground-truth-run analytical-ground-truth-claim-plan analytical-ground-truth-claim-load analytical-ground-truth-claim-verify analytical-ground-truth-claim-run verify-remote-runtime worker-independence-smoke worker-horizontal-smoke reliability-check docs-diagrams api acquisition-worker ingestion-worker catalogue-relay-worker materialization-worker housekeeping-worker cli db-revision compose-up compose-down

sync:
	cd backend && uv sync
	npm install

check:
	cd backend && uv run python -m compileall actions agents api catalogue catalogue_relay cli config control db dom materialization observability repository runtime workers
	cd backend && uv run python -m unittest discover -s tests
	$(MAKE) analytical-ground-truth-test
	$(MAKE) sdk-check
	$(MAKE) console-check

sdk-check:
	cd backend && uv run python -m unittest discover -s ../sdk/tests

console-check:
	npm run check:console
	npm run test:console

duckdb-extension-configure:
	$(MAKE) -C packages/atlas-duckdb-extension configure

duckdb-extension-check: duckdb-extension-configure
	$(MAKE) -C packages/atlas-duckdb-extension debug
	$(MAKE) -C packages/atlas-duckdb-extension test_debug

duckdb-extension-release: duckdb-extension-configure
	$(MAKE) -C packages/atlas-duckdb-extension release

duckdb-extension-clean:
	$(MAKE) -C packages/atlas-duckdb-extension clean_all

setup:
	cd backend && uv run atlas-setup

catalogue-check:
	cd backend && uv run python -m repository.catalogue check

catalogue-benchmark:
	cd backend && uv run python -m repository.catalogue benchmark

catalogue-load-synthetic:
	cd backend && uv run python scripts/load_synthetic_catalogue.py

analytical-ground-truth-test:
	cd backend && uv run python -m unittest discover -s ../benchmarks/analytical_ground_truth/tests

analytical-ground-truth-plan:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py plan

analytical-ground-truth-load:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py load

analytical-ground-truth-verify:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py verify

analytical-ground-truth-run:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py run

analytical-ground-truth-claim-plan:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py plan --scenario claim_lineage

analytical-ground-truth-claim-load:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py load --scenario claim_lineage --batch-size 30

analytical-ground-truth-claim-verify:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py verify --scenario claim_lineage

analytical-ground-truth-claim-run:
	cd backend && uv run python ../benchmarks/analytical_ground_truth/cli.py run --scenario claim_lineage

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
