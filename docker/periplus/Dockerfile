# syntax=docker/dockerfile:1.7

FROM debian:bookworm-slim AS periplus-extension-builder

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

WORKDIR /build/periplus-extension

COPY --from=periplus-extension CMakeLists.txt extension_config.cmake ./
COPY --from=periplus-extension src/ src/

RUN git clone --depth 1 --branch "${DUCKDB_VERSION}" \
        https://github.com/duckdb/duckdb.git duckdb \
    && DISABLE_EXTENSIONS_FOR_TEST=1 cmake \
        -G Ninja \
        -DDUCKDB_EXTENSION_CONFIGS=/build/periplus-extension/extension_config.cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -S duckdb \
        -B build/release \
    && cmake --build build/release \
        --target periplus_loadable_extension \
        --parallel 2


FROM periplus-extension-builder AS ducklake-cdc-extension-builder

WORKDIR /build/ducklake-cdc-extension

COPY --from=ducklake-cdc-extension CMakeLists.txt ./
COPY --from=ducklake-cdc-extension src/ src/
COPY docker/periplus/ducklake_cdc_extension_config.cmake ./extension_config.cmake

RUN DISABLE_EXTENSIONS_FOR_TEST=1 cmake \
        -G Ninja \
        -DDUCKDB_EXTENSION_CONFIGS=/build/ducklake-cdc-extension/extension_config.cmake \
        -DCMAKE_BUILD_TYPE=Release \
        -S /build/periplus-extension/duckdb \
        -B /build/periplus-extension/build/release \
    && cmake --build /build/periplus-extension/build/release \
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

RUN UV_PROJECT_ENVIRONMENT=/opt/periplus-venv uv sync --frozen --no-install-project --no-dev

# Periplus computes locally and shares DuckLake metadata through PostgreSQL.
ARG PERIPLUS_DUCKDB_EXTENSIONS=ducklake,postgres,httpfs,aws
RUN PERIPLUS_DUCKDB_EXTENSIONS="${PERIPLUS_DUCKDB_EXTENSIONS}" /opt/periplus-venv/bin/python -c "import os, duckdb; connection = duckdb.connect(); [connection.install_extension(name) for name in os.environ['PERIPLUS_DUCKDB_EXTENSIONS'].split(',') if name]; connection.close()"

ENV PATH="/opt/periplus-venv/bin:${PATH}"

FROM dependencies AS base
COPY backend/ ./
COPY --from=periplus-extension-builder \
    /build/periplus-extension/build/release/extension/periplus/periplus.duckdb_extension \
    /opt/periplus/periplus.duckdb_extension
COPY --from=ducklake-cdc-extension-builder \
    /build/periplus-extension/build/release/extension/ducklake_cdc/ducklake_cdc.duckdb_extension \
    /opt/periplus/ducklake_cdc.duckdb_extension

# Install Periplus itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/periplus-venv uv sync --frozen --no-editable --no-dev

ENV PERIPLUS_DUCKDB_EXTENSION_PATH=/opt/periplus/periplus.duckdb_extension
ENV PERIPLUS_DUCKLAKE_CDC_EXTENSION_PATH=/opt/periplus/ducklake_cdc.duckdb_extension

FROM base AS final
