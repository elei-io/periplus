#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
D2_BIN="${D2_BIN:-d2}"
SOURCE="$ROOT_DIR/docs/diagrams/catalogue-schema.d2"
OUTPUT="$ROOT_DIR/web/src/assets/catalogue-schema.svg"

if ! command -v "$D2_BIN" >/dev/null 2>&1; then
  echo "D2 is required to render documentation diagrams: https://d2lang.com/tour/install/" >&2
  exit 1
fi

"$D2_BIN" --layout=elk --theme=200 --pad=24 "$SOURCE" "$OUTPUT"

# D2's dark SQL-table theme uses violet field names and pink constraints. Keep
# its table rendering and replace only those semantic accents with Atlas tones.
perl -pi -e 's/#CBA6f7/#B9C3C6/g; s/#f38BA8/#36B7DF/g' "$OUTPUT"
