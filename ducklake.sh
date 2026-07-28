#!/usr/bin/env bash
set -euo pipefail

atlas_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
extension_repo="${ATLAS_DUCKDB_EXTENSION_REPO:-${atlas_root}/../atlas-duckdb-extension}"
extension_binary="${extension_repo}/build/release/extension/atlas/atlas.duckdb_extension"
duckdb_cli="${extension_repo}/build/release/duckdb"

if [[ ! -f "${extension_binary}" || ! -x "${duckdb_cli}" ]]; then
  echo "Atlas DuckDB extension release build not found." >&2
  echo "Run: make -C ${extension_repo} release" >&2
  exit 1
fi

cd "${atlas_root}/backend"
exec uv run python scripts/direct_ducklake.py \
  --extension "${extension_binary}" \
  --duckdb-cli "${duckdb_cli}" \
  "$@"
