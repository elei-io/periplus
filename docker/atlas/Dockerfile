FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS dependencies

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        libxml2-dev \
        libxslt1-dev \
        procps \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./

RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-install-project --no-dev

# Install the DuckDB extensions used by the embedded DuckLake catalogue.
ARG ATLAS_DUCKDB_EXTENSIONS=ducklake,parquet,postgres,httpfs,sqlite
RUN ATLAS_DUCKDB_EXTENSIONS="${ATLAS_DUCKDB_EXTENSIONS}" /opt/atlas-venv/bin/python -c "import os, duckdb; connection = duckdb.connect(); [connection.install_extension(name) for name in os.environ['ATLAS_DUCKDB_EXTENSIONS'].split(',') if name]; connection.close()"
RUN /opt/atlas-venv/bin/python -c "import duckdb; connection = duckdb.connect(); connection.install_extension('ducklake_cdc', repository='community'); connection.close()"

ENV PATH="/opt/atlas-venv/bin:${PATH}"

FROM dependencies AS browser-dependencies
RUN playwright install --with-deps chromium

FROM dependencies AS base
COPY backend/ ./
COPY fixtures/ /app/fixtures/

# Install Atlas itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable --no-dev

FROM browser-dependencies AS browser
COPY backend/ ./
COPY fixtures/ /app/fixtures/
RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable

FROM base AS final
