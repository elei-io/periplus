FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        procps \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./

RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-install-project

# Catalogue attachment must not depend on extension downloads during pod startup.
RUN /opt/atlas-venv/bin/python -c "import duckdb; connection = duckdb.connect(); [connection.install_extension(name) for name in ('ducklake', 'parquet', 'postgres', 'httpfs', 'sqlite')]; connection.close()"

RUN /opt/atlas-venv/bin/playwright install --with-deps chromium

COPY backend/ ./

# Install Atlas itself as a regular package. The resulting image must not depend on
# a source bind mount, editable-package path, or the build context at runtime.
RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-editable

ENV PATH="/opt/atlas-venv/bin:${PATH}"
