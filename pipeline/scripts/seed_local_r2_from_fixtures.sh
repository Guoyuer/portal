#!/usr/bin/env bash
# Seed local R2 from the committed L2 regression fixtures so `wrangler dev
# --local` serves realistic endpoint artifacts without touching Yahoo/FRED or
# production Cloudflare resources.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PIPELINE_DIR/.." && pwd)"

FIXTURE_DIR="$PIPELINE_DIR/tests/fixtures/regression"
DATA_DIR="$PIPELINE_DIR/data/e2e-seed"
DOWNLOADS_DIR="$DATA_DIR/downloads"
ARTIFACT_DIR="$DATA_DIR/artifacts/r2"
AS_OF="${SEED_AS_OF:-2026-04-14}"

if [ -x "$PIPELINE_DIR/.venv/Scripts/python.exe" ]; then
  PYTHON="$PIPELINE_DIR/.venv/Scripts/python.exe"
elif [ -x "$PIPELINE_DIR/.venv/bin/python" ]; then
  PYTHON="$PIPELINE_DIR/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

PYTHON_IS_WINDOWS=0
case "$PYTHON" in
  *.exe) PYTHON_IS_WINDOWS=1 ;;
esac

python_path() {
  if [ "$PYTHON_IS_WINDOWS" -eq 0 ]; then
    printf '%s' "$1"
  elif command -v cygpath >/dev/null 2>&1; then
    cygpath -w "$1"
  elif command -v wslpath >/dev/null 2>&1; then
    wslpath -w "$1"
  else
    printf '%s' "$1"
  fi
}

PY_DATA_DIR="$(python_path "$DATA_DIR")"
PY_DOWNLOADS_DIR="$(python_path "$DOWNLOADS_DIR")"
PY_ARTIFACT_DIR="$(python_path "$ARTIFACT_DIR")"
PY_FIXTURE_DIR="$(python_path "$FIXTURE_DIR")"
PY_DB_PATH="$(python_path "$DATA_DIR/timemachine.db")"

echo "[seed] using python: $PYTHON"
echo "[seed] data dir: $DATA_DIR"

rm -rf "$DATA_DIR"
mkdir -p "$DOWNLOADS_DIR"

cp "$FIXTURE_DIR/Accounts_History_fixture.csv"           "$DOWNLOADS_DIR/"
cp "$FIXTURE_DIR/Bloomberg.Download_fixture_2024-06.qfx" "$DOWNLOADS_DIR/"
cp "$FIXTURE_DIR/Bloomberg.Download_fixture_2024-12.qfx" "$DOWNLOADS_DIR/"
cp "$FIXTURE_DIR/robinhood.csv"                          "$DOWNLOADS_DIR/Robinhood_history.csv"

export QIANJI_DB_PATH_OVERRIDE="$(python_path "$FIXTURE_DIR/qianji.sqlite")"
export QIANJI_CNY_RATE_OVERRIDE="7.20"
export QIANJI_USER_TZ="UTC"
export PORTAL_DB_PATH="$PY_DB_PATH"

echo "[seed] building timemachine.db from fixtures (as-of=$AS_OF)..."
(
  cd "$PIPELINE_DIR"
  "$PYTHON" scripts/build_timemachine_db.py \
    --data-dir "$PY_DATA_DIR" \
    --config "$PY_FIXTURE_DIR/config.json" \
    --downloads "$PY_DOWNLOADS_DIR" \
    --prices-from-csv "$PY_FIXTURE_DIR/prices.csv" \
    --dry-run-market \
    --no-validate \
    --as-of "$AS_OF"
)

WRANGLER_R2_DIR="$REPO_ROOT/worker/.wrangler/state/v3/r2"
if [ -d "$WRANGLER_R2_DIR" ]; then
  echo "[seed] wiping stale local R2 state at $WRANGLER_R2_DIR..."
  rm -rf "$WRANGLER_R2_DIR"
fi

echo "[seed] exporting and publishing artifacts to local R2..."
(
  cd "$PIPELINE_DIR"
  "$PYTHON" scripts/r2_artifacts.py --db "$PY_DB_PATH" --artifact-dir "$PY_ARTIFACT_DIR" export
  "$PYTHON" scripts/r2_artifacts.py --db "$PY_DB_PATH" --artifact-dir "$PY_ARTIFACT_DIR" verify
  "$PYTHON" scripts/r2_artifacts.py --db "$PY_DB_PATH" --artifact-dir "$PY_ARTIFACT_DIR" publish --local
)

echo "[seed] done — local R2 seeded from L2 fixtures."
