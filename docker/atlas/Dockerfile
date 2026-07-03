FROM ghcr.io/astral-sh/uv:python3.14-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libxml2-dev \
        libxslt1-dev \
        zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

COPY backend/pyproject.toml backend/uv.lock ./

RUN UV_PROJECT_ENVIRONMENT=/opt/atlas-venv uv sync --frozen --no-install-project

RUN /opt/atlas-venv/bin/playwright install --with-deps chromium

ENV PATH="/opt/atlas-venv/bin:${PATH}"
ENV PYTHONPATH="/app/backend"
