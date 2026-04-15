#!/usr/bin/env bash
# One-shot: capture L1 + L3 baselines from the current tree. Run on main or on the
# pre-refactor commit before starting migration. Commits the .sha256 files.
set -euo pipefail

cd "$(dirname "$0")/.."
BASELINE_DIR="tests/regression/baseline"
DB_PATH="${DB_PATH:-data/timemachine.db}"
WRANGLER_CWD="../worker"

.venv/Scripts/python.exe scripts/build_timemachine_db.py
.venv/Scripts/python.exe scripts/_regression_util.py hash "$DB_PATH" "$BASELINE_DIR"

pushd "$WRANGLER_CWD" >/dev/null
npx wrangler dev --local --persist-to=../pipeline/data/.wrangler-regression &
WRANGLER_PID=$!
popd >/dev/null
trap 'kill $WRANGLER_PID 2>/dev/null || true' EXIT

for _ in $(seq 1 30); do
    if curl -sf http://localhost:8787/timeline >/dev/null 2>&1; then break; fi
    sleep 1
done

curl -sf http://localhost:8787/timeline | sha256sum | awk '{print $1}' > "$BASELINE_DIR/timeline.sha256"
echo "baselines captured in $BASELINE_DIR"
