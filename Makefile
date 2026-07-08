.PHONY: sync check api cli db-upgrade db-revision compose-up compose-down

sync:
	cd backend && uv sync

check:
	cd backend && uv run python -m compileall actions artifacts api cli db tasks urls crawls extract_schemas crawl_policies

api:
	cd backend && uv run fastapi dev api/app.py

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
