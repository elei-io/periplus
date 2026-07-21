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
ARG ATLAS_DUCKDB_EXTENSIONS=ducklake,parquet,postgres,httpfs,sqlite,quack
RUN ATLAS_DUCKDB_EXTENSIONS="${ATLAS_DUCKDB_EXTENSIONS}" /opt/atlas-venv/bin/python -c "import os, duckdb; connection = duckdb.connect(); [connection.install_extension(name) for name in os.environ['ATLAS_DUCKDB_EXTENSIONS'].split(',') if name]; connection.close()"
ARG TARGETARCH
ARG DUCKLAKE_CDC_VERSION=0.6.1
ARG DUCKLAKE_CDC_DUCKDB_VERSION=1.5.4
ARG DUCKLAKE_CDC_LINUX_AMD64_SHA256=00997856cf44ec43c0d4751ea30d9e716ac77e38fb2e9ece49040254b23aee17
ARG DUCKLAKE_CDC_LINUX_ARM64_SHA256=1cc5bfd4d5459082fbec349ab44a696242b7367c0632072ac29d5393ddf83d2f
RUN TARGETARCH="${TARGETARCH}" \
    DUCKLAKE_CDC_VERSION="${DUCKLAKE_CDC_VERSION}" \
    DUCKLAKE_CDC_DUCKDB_VERSION="${DUCKLAKE_CDC_DUCKDB_VERSION}" \
    DUCKLAKE_CDC_LINUX_AMD64_SHA256="${DUCKLAKE_CDC_LINUX_AMD64_SHA256}" \
    DUCKLAKE_CDC_LINUX_ARM64_SHA256="${DUCKLAKE_CDC_LINUX_ARM64_SHA256}" \
    /opt/atlas-venv/bin/python - <<'PY'
import hashlib
import os
import tempfile
import urllib.request

import duckdb

architecture = os.environ["TARGETARCH"]
if architecture not in {"amd64", "arm64"}:
    raise RuntimeError(f"Unsupported Atlas image architecture: {architecture}")
version = os.environ["DUCKLAKE_CDC_VERSION"]
duckdb_version = os.environ["DUCKLAKE_CDC_DUCKDB_VERSION"]
expected = os.environ[f"DUCKLAKE_CDC_LINUX_{architecture.upper()}_SHA256"]
filename = f"ducklake_cdc-v{version}-duckdb-v{duckdb_version}-linux_{architecture}.duckdb_extension"
url = f"https://github.com/ekkuleivonen/ducklake-cdc-extension/releases/download/v{version}/{filename}"
with tempfile.TemporaryDirectory() as directory:
    artifact = os.path.join(directory, "ducklake_cdc.duckdb_extension")
    urllib.request.urlretrieve(url, artifact)
    with open(artifact, "rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected:
        raise RuntimeError(f"ducklake_cdc checksum mismatch: expected {expected}, got {actual}")
    connection = duckdb.connect(config={"allow_unsigned_extensions": "true"})
    connection.install_extension(artifact, force_install=True)
    connection.close()
PY

ENV PATH="/opt/atlas-venv/bin:${PATH}"

FROM dependencies AS base
COPY backend/ ./
COPY fixtures/ /app/fixtures/

# Install Atlas itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable --no-dev

FROM base AS final
