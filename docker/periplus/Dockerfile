# syntax=docker/dockerfile:1.7

FROM debian:bookworm-slim AS ducklake-cdc-extension-builder
ARG DUCKDB_VERSION=v1.5.5
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential ca-certificates cmake git ninja-build \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /build/ducklake-cdc-extension
COPY --from=ducklake-cdc-extension CMakeLists.txt ./
COPY --from=ducklake-cdc-extension src/ src/
COPY docker/periplus/ducklake_cdc_extension_config.cmake ./extension_config.cmake
RUN git clone --depth 1 --branch "${DUCKDB_VERSION}" https://github.com/duckdb/duckdb.git /build/duckdb \
    && DISABLE_EXTENSIONS_FOR_TEST=1 cmake -G Ninja \
        -DDUCKDB_EXTENSION_CONFIGS=/build/ducklake-cdc-extension/extension_config.cmake \
        -DCMAKE_BUILD_TYPE=Release -S /build/duckdb -B /build/duckdb/build/release \
    && cmake --build /build/duckdb/build/release --target ducklake_cdc_loadable_extension --parallel 2

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

ENV PATH="/opt/periplus-venv/bin:${PATH}"

FROM dependencies AS base
COPY packages/periplus/ ./
COPY --from=ducklake-cdc-extension-builder \
    /build/duckdb/build/release/extension/ducklake_cdc/ducklake_cdc.duckdb_extension \
    /opt/periplus/ducklake_cdc.duckdb_extension

# Install Periplus itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/periplus-venv uv sync --frozen --no-editable --no-dev

ENV PERIPLUS_DUCKLAKE_CDC_EXTENSION_PATH=/opt/periplus/ducklake_cdc.duckdb_extension

FROM base AS final
