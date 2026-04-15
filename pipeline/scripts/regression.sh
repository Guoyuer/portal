#!/usr/bin/env bash
# Regression check: run after every task in the migration. Exits non-zero on any diff.
set -euo pipefail

cd "$(dirname "$0")/.."
BASELINE_DIR="tests/regression/baseline"
DB_PATH="${DB_PATH:-data/timemachine.db}"

# Reminder: wrangler comes from the top-level repo's worker/ package.
WRANGLER_CWD="../worker"

# ── Step 1: rebuild ─────────────────────────────────────────────────────
echo "[regression] rebuilding timemachine.db..."
.venv/Scripts/python.exe scripts/build_timemachine_db.py

# ── Step 2: L1 row-level hash compare ───────────────────────────────────
echo "[regression] L1: hashing computed_daily* ..."
.venv/Scripts/python.exe scripts/_regression_util.py compare "$DB_PATH" "$BASELINE_DIR"

# ── Step 3: L3 /timeline JSON hash compare ──────────────────────────────
# Seed the default wrangler persist dir (worker/.wrangler/state/) from
# timemachine.db. build_timemachine_db.py does NOT run sync_to_d1 itself,
# so we do it here — wrangler --local reads from the same default path.
echo "[regression] L3: syncing timemachine.db -> local D1 ..."
.venv/Scripts/python.exe scripts/sync_to_d1.py --local

echo "[regression] L3: starting wrangler --local ..."
pushd "$WRANGLER_CWD" >/dev/null
npx wrangler dev --local &
WRANGLER_PID=$!
popd >/dev/null
trap 'kill $WRANGLER_PID 2>/dev/null || true' EXIT

# wait for worker to become ready (max 30s)
for _ in $(seq 1 30); do
    if curl -sf http://localhost:8787/timeline >/dev/null 2>&1; then break; fi
    sleep 1
done

BODY=$(curl -sf http://localhost:8787/timeline)
GOT=$(printf '%s' "$BODY" | sha256sum | awk '{print $1}')
EXPECTED=$(cat "$BASELINE_DIR/timeline.sha256" | tr -d '[:space:]')

if [ "$GOT" != "$EXPECTED" ]; then
    echo "REGRESSION in /timeline: expected $EXPECTED, got $GOT" >&2
    exit 1
fi
echo "[regression] L3: OK"
echo "[regression] ALL TIERS GREEN ✓"
