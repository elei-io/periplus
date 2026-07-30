#!/usr/bin/env bash
set -euo pipefail

atlas_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
extension_repo="${ATLAS_DUCKDB_EXTENSION_REPO:-${atlas_root}/../atlas-duckdb-extension}"
extension_binary="${extension_repo}/build/release/extension/atlas/atlas.duckdb_extension"
duckdb_cli="${extension_repo}/build/release/duckdb"

export ATLAS_DUCKLAKE_ALIAS="${ATLAS_DUCKLAKE_ALIAS:-atlas}"
export ATLAS_DUCKLAKE_METADATA_PATH="${ATLAS_DUCKLAKE_METADATA_PATH:-postgres:dbname=atlas_test host=127.0.0.1 port=${ATLAS_POSTGRES_PORT:-55432} user=atlas password=atlas_local}"
export ATLAS_DUCKLAKE_METADATA_SCHEMA="${ATLAS_DUCKLAKE_METADATA_SCHEMA:-ducklake}"
export ATLAS_DUCKLAKE_DATA_PATH="${ATLAS_DUCKLAKE_DATA_PATH:-${atlas_root}/.atlas/lake/}"
export ATLAS_DUCKDB_EXTENSION_PATH="${ATLAS_DUCKDB_EXTENSION_PATH:-${extension_binary}}"

if [[ ! -f "${ATLAS_DUCKDB_EXTENSION_PATH}" || ! -x "${duckdb_cli}" ]]; then
  echo "Atlas DuckDB extension release build not found." >&2
  echo "Expected: ${ATLAS_DUCKDB_EXTENSION_PATH}" >&2
  echo "Build locally with: make -C ${extension_repo} release" >&2
  exit 1
fi

cd "${atlas_root}/backend"
exec uv run python scripts/direct_ducklake.py \
  --duckdb-cli "${duckdb_cli}" \
  "$@"
