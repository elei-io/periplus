FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim AS dependencies

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
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
ARG TARGETARCH
ARG ATLAS_DUCKLAKE_CDC_RELEASE=v0.5.3
RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) checksum=58a455af26c277d8327f4b50ad617fd6f99609d81d321406d5eabe57a5fc1e5a ;; \
      arm64) checksum=dc55f45bd81578153e343629f659f74cc9d90e3374c01aa8c01943d08a9c2898 ;; \
      *) echo "Unsupported ducklake_cdc architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    asset="ducklake_cdc-${ATLAS_DUCKLAKE_CDC_RELEASE}-duckdb-v1.5.4-linux_${TARGETARCH}.duckdb_extension"; \
    install -d /opt/atlas/extensions; \
    curl --fail --location --retry 3 \
      "https://github.com/ekkuleivonen/ducklake-cdc-extension/releases/download/${ATLAS_DUCKLAKE_CDC_RELEASE}/${asset}" \
      --output /opt/atlas/extensions/ducklake_cdc.duckdb_extension; \
    echo "${checksum}  /opt/atlas/extensions/ducklake_cdc.duckdb_extension" | sha256sum --check --strict

ENV PATH="/opt/atlas-venv/bin:${PATH}"
ENV ATLAS_DUCKLAKE_CDC_EXTENSION="/opt/atlas/extensions/ducklake_cdc.duckdb_extension"

FROM dependencies AS browser-dependencies
RUN playwright install --with-deps chromium

FROM dependencies AS base
COPY backend/ ./

# Install Atlas itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable --no-dev

FROM browser-dependencies AS browser
COPY backend/ ./
RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable

FROM base AS final
