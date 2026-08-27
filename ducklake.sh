#!/usr/bin/env bash
set -euo pipefail

periplus_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
extension_repo="${PERIPLUS_DUCKDB_EXTENSION_REPO:-${periplus_root}/../periplus-duckdb-extension}"
extension_binary="${extension_repo}/build/release/extension/periplus/periplus.duckdb_extension"
duckdb_cli="${extension_repo}/build/release/duckdb"

export PERIPLUS_DUCKLAKE_ALIAS="${PERIPLUS_DUCKLAKE_ALIAS:-periplus}"
export PERIPLUS_DUCKLAKE_METADATA_PATH="${PERIPLUS_DUCKLAKE_METADATA_PATH:-postgres:dbname=lake host=127.0.0.1 port=${LAKE_POSTGRES_PORT:-55433} user=lake password=lake_local}"
export PERIPLUS_DUCKLAKE_METADATA_SCHEMA="${PERIPLUS_DUCKLAKE_METADATA_SCHEMA:-ducklake}"
export PERIPLUS_DUCKLAKE_DATA_PATH="${PERIPLUS_DUCKLAKE_DATA_PATH:-s3://lake/}"
export PERIPLUS_DUCKLAKE_S3_ENDPOINT="${PERIPLUS_DUCKLAKE_S3_ENDPOINT:-127.0.0.1:${LAKE_ALLUXIO_S3_PORT:-7070}/api/v1/s3}"
export PERIPLUS_DUCKLAKE_S3_REGION="${PERIPLUS_DUCKLAKE_S3_REGION:-us-east-1}"
export PERIPLUS_DUCKLAKE_S3_KEY_ID="${PERIPLUS_DUCKLAKE_S3_KEY_ID:-alluxio}"
export PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY="${PERIPLUS_DUCKLAKE_S3_SECRET_ACCESS_KEY:-lake-secret}"
export PERIPLUS_DUCKLAKE_S3_URL_STYLE="${PERIPLUS_DUCKLAKE_S3_URL_STYLE:-path}"
export PERIPLUS_DUCKLAKE_S3_USE_SSL="${PERIPLUS_DUCKLAKE_S3_USE_SSL:-false}"
export PERIPLUS_DUCKDB_EXTENSION_PATH="${PERIPLUS_DUCKDB_EXTENSION_PATH:-${extension_binary}}"

if [[ ! -f "${PERIPLUS_DUCKDB_EXTENSION_PATH}" || ! -x "${duckdb_cli}" ]]; then
  echo "Periplus DuckDB extension release build not found." >&2
  echo "Expected: ${PERIPLUS_DUCKDB_EXTENSION_PATH}" >&2
  echo "Build locally with: make -C ${extension_repo} release" >&2
  exit 1
fi

cd "${periplus_root}/backend"
exec uv run python scripts/direct_ducklake.py \
  --duckdb-cli "${duckdb_cli}" \
  "$@"
