# syntax=docker/dockerfile:1.7

FROM debian:bookworm-slim AS atlas-extension-builder

ARG DUCKDB_VERSION=v1.5.5
ARG LEXBOR_VERSION=v3.0.0

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        ninja-build \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "${LEXBOR_VERSION}" \
        https://github.com/lexbor/lexbor.git /tmp/lexbor \
    && cmake -S /tmp/lexbor -B /tmp/lexbor/build \
        -G Ninja \
        -DCMAKE_BUILD_TYPE=Release \
        -DLEXBOR_BUILD_TESTS=OFF \
        -DLEXBOR_BUILD_EXAMPLES=OFF \
    && cmake --build /tmp/lexbor/build --target install --parallel 2

WORKDIR /build/atlas-extension

COPY --from=atlas-extension CMakeLists.txt extension_config.cmake ./
COPY --from=atlas-extension src/ src/

RUN git clone --depth 1 --branch "${DUCKDB_VERSION}" \
        https://github.com/duckdb/duckdb.git duckdb \
    && DISABLE_EXTENSIONS_FOR_TEST=1 cmake \
        -G Ninja \
        -DDUCKDB_EXTENSION_CONFIGS=/build/atlas-extension/extension_config.cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -S duckdb \
        -B build/release \
    && cmake --build build/release \
        --target atlas_loadable_extension \
        --parallel 2


FROM atlas-extension-builder AS ducklake-cdc-extension-builder

WORKDIR /build/ducklake-cdc-extension

COPY --from=ducklake-cdc-extension CMakeLists.txt ./
COPY --from=ducklake-cdc-extension src/ src/
COPY docker/atlas/ducklake_cdc_extension_config.cmake ./extension_config.cmake

RUN DISABLE_EXTENSIONS_FOR_TEST=1 cmake \
        -G Ninja \
        -DDUCKDB_EXTENSION_CONFIGS=/build/ducklake-cdc-extension/extension_config.cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -S /build/atlas-extension/duckdb \
        -B /build/atlas-extension/build/release \
    && cmake --build /build/atlas-extension/build/release \
        --target ducklake_cdc_loadable_extension \
        --parallel 2


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

# Atlas computes locally and shares DuckLake metadata through PostgreSQL.
ARG ATLAS_DUCKDB_EXTENSIONS=ducklake,postgres
RUN ATLAS_DUCKDB_EXTENSIONS="${ATLAS_DUCKDB_EXTENSIONS}" /opt/atlas-venv/bin/python -c "import os, duckdb; connection = duckdb.connect(); [connection.install_extension(name) for name in os.environ['ATLAS_DUCKDB_EXTENSIONS'].split(',') if name]; connection.close()"

ENV PATH="/opt/atlas-venv/bin:${PATH}"

FROM dependencies AS base
COPY backend/ ./
COPY --from=atlas-extension-builder \
    /build/atlas-extension/build/release/extension/atlas/atlas.duckdb_extension \
    /opt/atlas/atlas.duckdb_extension
COPY --from=ducklake-cdc-extension-builder \
    /build/atlas-extension/build/release/extension/ducklake_cdc/ducklake_cdc.duckdb_extension \
    /opt/atlas/ducklake_cdc.duckdb_extension

# Install Atlas itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable --no-dev

ENV ATLAS_DUCKDB_EXTENSION_PATH=/opt/atlas/atlas.duckdb_extension
ENV ATLAS_DUCKLAKE_CDC_EXTENSION_PATH=/opt/atlas/ducklake_cdc.duckdb_extension

FROM base AS final
