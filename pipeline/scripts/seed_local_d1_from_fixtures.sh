#!/usr/bin/env bash
# Seed the local D1 (wrangler miniflare) from the committed L2 regression
# fixtures, so `wrangler dev --local` serves realistic data without touching
# Yahoo/FRED. Used by `.github/workflows/e2e-real-worker.yml`.
#
# Contract:
#   - reads committed fixtures under pipeline/tests/fixtures/regression/
#   - writes timemachine.db to a throwaway directory inside pipeline/data/
#   - applies worker/schema.sql to the local D1 (idempotent — CREATE TABLE IF NOT EXISTS)
#   - pushes via sync_to_d1.py --local
#
# Offline: no network calls. Mirrors test_pipeline_golden.py's subprocess invocation.
#
# Usage: bash pipeline/scripts/seed_local_d1_from_fixtures.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIPELINE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$PIPELINE_DIR/.." && pwd)"

FIXTURE_DIR="$PIPELINE_DIR/tests/fixtures/regression"
DATA_DIR="$PIPELINE_DIR/data/e2e-seed"
DOWNLOADS_DIR="$DATA_DIR/downloads"

# L2 pins as-of=2026-04-14 so the golden stays reproducible; we match that so
# the worker serves the same dates the fixtures cover.
AS_OF="${SEED_AS_OF:-2026-04-14}"

# Prefer the pinned venv; fall back to system python in CI (GH Actions
# setup-python doesn't create a venv under pipeline/.venv).
if [ -x "$PIPELINE_DIR/.venv/Scripts/python.exe" ]; then
  PYTHON="$PIPELINE_DIR/.venv/Scripts/python.exe"
elif [ -x "$PIPELINE_DIR/.venv/bin/python" ]; then
  PYTHON="$PIPELINE_DIR/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "[seed] using python: $PYTHON"
echo "[seed] data dir: $DATA_DIR"

# Fresh scratch dir each run — mirror the pytest fixture's tmp_path_factory
# semantics so a stale half-built DB can't leak across runs.
rm -rf "$DATA_DIR"
mkdir -p "$DOWNLOADS_DIR"

# Copy the committed raw-broker fixtures into the build's downloads/ so the
# production globs pick them up. Names + rename rules lifted from
# tests/regression/test_pipeline_golden.py.
cp "$FIXTURE_DIR/Accounts_History_fixture.csv"           "$DOWNLOADS_DIR/"
cp "$FIXTURE_DIR/Bloomberg.Download_fixture_2024-06.qfx" "$DOWNLOADS_DIR/"
cp "$FIXTURE_DIR/Bloomberg.Download_fixture_2024-12.qfx" "$DOWNLOADS_DIR/"
# Robinhood production glob is Robinhood_history*.csv; the fixture ships under
# a clearer name and is renamed on copy.
cp "$FIXTURE_DIR/robinhood.csv"                          "$DOWNLOADS_DIR/Robinhood_history.csv"

# Pin the offline environment exactly the way the pytest fixture does.
export QIANJI_DB_PATH_OVERRIDE="$FIXTURE_DIR/qianji.sqlite"
export QIANJI_CNY_RATE_OVERRIDE="7.20"
export QIANJI_USER_TZ="UTC"
# PORTAL_DB_PATH lets sync_to_d1.py find the throwaway DB instead of the
# user's pipeline/data/timemachine.db.
export PORTAL_DB_PATH="$DATA_DIR/timemachine.db"

echo "[seed] building timemachine.db from fixtures (as-of=$AS_OF)..."
(
  cd "$PIPELINE_DIR"
  "$PYTHON" scripts/build_timemachine_db.py \
    --data-dir "$DATA_DIR" \
    --config "$FIXTURE_DIR/config.json" \
    --downloads "$DOWNLOADS_DIR" \
    --prices-from-csv "$FIXTURE_DIR/prices.csv" \
    --dry-run-market \
    --no-validate \
    --as-of "$AS_OF"
)

# Fresh miniflare D1 state — on a long-lived dev machine the persisted D1
# may predate the current schema, and CREATE TABLE IF NOT EXISTS won't add
# the missing columns (sync would then fail with "no such column" on the
# first INSERT). Wiping the miniflare D1 dir sidesteps the drift and keeps
# the local dry-run path identical to CI's fresh-checkout behaviour.
# Non-D1 state (cache/, workflows/) is left alone.
WRANGLER_D1_DIR="$REPO_ROOT/worker/.wrangler/state/v3/d1"
if [ -d "$WRANGLER_D1_DIR" ]; then
  echo "[seed] wiping stale local D1 state at $WRANGLER_D1_DIR..."
  rm -rf "$WRANGLER_D1_DIR"
fi

# Apply schema.sql to the local D1 before the data sync. `CREATE TABLE IF
# NOT EXISTS` makes this idempotent for the views + indexes, and the wipe
# above guarantees the tables are created with the current column set.
echo "[seed] applying worker/schema.sql to local D1..."
(
  cd "$REPO_ROOT/worker"
  npx wrangler d1 execute portal-db --local --file=schema.sql
)

# Push rows. --local hits miniflare's on-disk D1 under worker/.wrangler/state/.
echo "[seed] syncing rows to local D1..."
(
  cd "$PIPELINE_DIR"
  "$PYTHON" scripts/sync_to_d1.py --local
)

echo "[seed] done — local D1 seeded from L2 fixtures."
