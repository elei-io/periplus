# syntax=docker/dockerfile:1.7

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

WORKDIR /app/packages/periplus

COPY packages/periplus/pyproject.toml packages/periplus/uv.lock ./

RUN UV_PROJECT_ENVIRONMENT=/opt/periplus-venv uv sync --frozen --no-install-project --no-dev

# Periplus computes locally and shares DuckLake metadata through PostgreSQL.
ARG PERIPLUS_DUCKDB_EXTENSIONS=ducklake,postgres,httpfs,aws
RUN PERIPLUS_DUCKDB_EXTENSIONS="${PERIPLUS_DUCKDB_EXTENSIONS}" /opt/periplus-venv/bin/python -c "import os, duckdb; connection = duckdb.connect(); [connection.install_extension(name) for name in os.environ['PERIPLUS_DUCKDB_EXTENSIONS'].split(',') if name]; connection.close()"

COPY packages/periplus/src/periplus/platform/catalogue/cdc_extension.py /tmp/install_cdc.py
RUN /opt/periplus-venv/bin/python /tmp/install_cdc.py && rm /tmp/install_cdc.py

ENV PATH="/opt/periplus-venv/bin:${PATH}"

FROM dependencies AS base
COPY packages/periplus/ ./

# Install Periplus itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/periplus-venv uv sync --frozen --no-editable --no-dev

FROM base AS final
COPY LICENSE NOTICE LICENSING.md THIRD_PARTY_NOTICES.md /usr/share/licenses/periplus/
