#!/usr/bin/env bash
set -euo pipefail

periplus_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
duckdb_cli="$(command -v duckdb || true)"

export PERIPLUS_DUCKLAKE_ALIAS="${PERIPLUS_DUCKLAKE_ALIAS:-periplus}"
export PERIPLUS_DUCKLAKE_METADATA_PATH="${PERIPLUS_DUCKLAKE_METADATA_PATH:-postgres:dbname=lake host=127.0.0.1 port=${LAKE_POSTGRES_PORT:-55433} user=lake password=lake_local}"
export PERIPLUS_DUCKLAKE_METADATA_SCHEMA="${PERIPLUS_DUCKLAKE_METADATA_SCHEMA:-ducklake}"
export PERIPLUS_DUCKLAKE_DATA_PATH="${PERIPLUS_DUCKLAKE_DATA_PATH:-s3://lake/}"
export PERIPLUS_DUCKLAKE_S3_ENDPOINT="${PERIPLUS_DUCKLAKE_S3_ENDPOINT:-127.0.0.1:${LAKE_S3_PORT:-7070}}"
export PERIPLUS_DUCKLAKE_S3_REGION="${PERIPLUS_DUCKLAKE_S3_REGION:-us-east-1}"
export PERIPLUS_DUCKLAKE_S3_KEY_ID="${PERIPLUS_DUCKLAKE_S3_KEY_ID:-periplus-local}"
export PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY="${PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY:-lake-secret}"
export PERIPLUS_DUCKLAKE_S3_URL_STYLE="${PERIPLUS_DUCKLAKE_S3_URL_STYLE:-path}"
export PERIPLUS_DUCKLAKE_S3_USE_SSL="${PERIPLUS_DUCKLAKE_S3_USE_SSL:-false}"

if [[ -z "${duckdb_cli}" ]]; then
  echo "Install the DuckDB CLI and make duckdb available on PATH." >&2
  exit 1
fi

cd "${periplus_root}/packages/periplus"
exec uv run python scripts/direct_ducklake.py \
  --duckdb-cli "${duckdb_cli}" \
  "$@"
