FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

ARG TARGETARCH
ARG DUCKLAKE_CDC_VERSION=0.5.3
ARG DUCKDB_VERSION=1.5.4

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        curl \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./

RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-install-project

RUN /opt/atlas-venv/bin/python -c "import duckdb; connection = duckdb.connect(); [connection.install_extension(name) for name in ('ducklake', 'parquet', 'postgres', 'httpfs', 'quack')]; connection.close()"

RUN set -eu; \
    case "${TARGETARCH}" in \
      amd64) checksum=58a455af26c277d8327f4b50ad617fd6f99609d81d321406d5eabe57a5fc1e5a ;; \
      arm64) checksum=dc55f45bd81578153e343629f659f74cc9d90e3374c01aa8c01943d08a9c2898 ;; \
      *) echo "unsupported ducklake_cdc architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    asset="ducklake_cdc-v${DUCKLAKE_CDC_VERSION}-duckdb-v${DUCKDB_VERSION}-linux_${TARGETARCH}.duckdb_extension"; \
    destination=/opt/duckdb/extensions/ducklake_cdc.duckdb_extension; \
    mkdir -p "$(dirname "${destination}")"; \
    curl -fsSL "https://github.com/ekkuleivonen/ducklake-cdc-extension/releases/download/v${DUCKLAKE_CDC_VERSION}/${asset}" -o "${destination}"; \
    echo "${checksum}  ${destination}" | sha256sum -c -

COPY backend/ ./

RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable

ENV PATH="/opt/atlas-venv/bin:${PATH}"

CMD ["python", "-m", "repository.quack_server"]
