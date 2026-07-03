.PHONY: sync check api worker cli db-upgrade db-revision compose-up compose-down

sync:
	cd backend && uv sync

check:
	cd backend && uv run python -m compileall api cli domains

api:
	cd backend && uv run fastapi dev api/app.py

worker:
	cd backend && uv run python -m domains.index.worker

cli:
	cd backend && uv run atlas --help

db-upgrade:
	cd backend && uv run alembic upgrade head

db-revision:
	cd backend && uv run alembic revision --autogenerate -m "$(m)"

compose-up:
	docker compose up --build -d

compose-down:
	docker compose down
