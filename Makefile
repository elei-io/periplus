.PHONY: sync check setup archive-verify api crawler ingestor materializer janitor db-revision compose-up compose-down compose-reset

sync:
	cd packages/periplus && uv sync
	npm install

check:
	cd packages/periplus && uv run python -m compileall src/periplus
	cd packages/periplus && uv run python -m unittest discover -s tests
	cd packages/periplus && uv run --with-editable ../periplus-python-sdk python -m unittest discover -s ../periplus-python-sdk/tests
	npm run check:packages
	npm run test:packages
	npm run check:public
	npm run build:public
	npm run check:admin
	npm run build:admin

setup:
	cd packages/periplus && uv run periplus-setup

archive-verify:
	cd packages/periplus && uv run periplus-archive verify --manifest "$(MANIFEST)"

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
