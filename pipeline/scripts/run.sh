#!/usr/bin/env bash
# ── Pipeline runner: detect changes → incremental build → diff sync ──
#
# Usage:
#   ./run.sh              # auto-detect changes, incremental build, diff sync
#   ./run.sh --force      # skip change detection, always build + sync
#   ./run.sh --local      # sync to local D1 instead of remote
#   ./run.sh --dry-run    # build but don't sync

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PIPELINE_DIR="$(dirname "$SCRIPT_DIR")"
DATA_DIR="${PIPELINE_DIR}/data"
DB_PATH="${DATA_DIR}/timemachine.db"

# Python interpreter
PYTHON="${PIPELINE_DIR}/.venv/Scripts/python.exe"
if [ ! -f "$PYTHON" ]; then
    PYTHON="${PIPELINE_DIR}/.venv/bin/python"
fi
if [ ! -f "$PYTHON" ]; then
    PYTHON="python3"
fi

# ── Parse args ──────────────────────────────────────────────────────────

FORCE=false
LOCAL_FLAG=""
DRY_RUN=false
SYNC_ARGS=""

for arg in "$@"; do
    case $arg in
        --force) FORCE=true ;;
        --local) LOCAL_FLAG="--local"; SYNC_ARGS="$SYNC_ARGS --local" ;;
        --dry-run) DRY_RUN=true ;;
    esac
done

# ── Change detection ────────────────────────────────────────────────────

DOWNLOADS="${PORTAL_DOWNLOADS:-$HOME/Downloads}"
MARKER="${DATA_DIR}/.last_run"

changes_detected() {
    if [ ! -f "$MARKER" ]; then
        return 0  # first run
    fi

    # Check Qianji DB
    local qj_db
    if [ "$(uname -s)" = "MINGW"* ] || [ "$(uname -s)" = "MSYS"* ] || [ -n "${APPDATA:-}" ]; then
        qj_db="${APPDATA}/com.mutangtech.qianji.win/qianji_flutter/qianjiapp.db"
    else
        qj_db="$HOME/Library/Containers/com.mutangtech.qianji.fltios/Data/Documents/qianjiapp.db"
    fi
    if [ -f "$qj_db" ] && [ "$qj_db" -nt "$MARKER" ]; then
        echo "  Change detected: Qianji DB modified"
        return 0
    fi

    # Check for new Fidelity CSVs
    local new_csvs
    new_csvs=$(find "$DOWNLOADS" -maxdepth 1 -name "Accounts_History*.csv" -newer "$MARKER" 2>/dev/null | head -1)
    if [ -n "$new_csvs" ]; then
        echo "  Change detected: new Fidelity CSV"
        return 0
    fi

    return 1  # no changes
}

# ── Main ────────────────────────────────────────────────────────────────

echo "============================================================"
echo "  Pipeline Runner"
echo "============================================================"

if [ "$FORCE" = false ]; then
    echo "[1] Checking for data changes..."
    if ! changes_detected; then
        echo "  No changes detected. Use --force to override."
        exit 0
    fi
else
    echo "[1] Force mode — skipping change detection"
fi

# ── Build ───────────────────────────────────────────────────────────────

echo "[2] Running incremental build..."
"$PYTHON" "$SCRIPT_DIR/build_timemachine_db.py" incremental

# ── Get last_date for diff sync ─────────────────────────────────────────

LAST_DATE=$("$PYTHON" -c "
import sqlite3
conn = sqlite3.connect('${DB_PATH}')
row = conn.execute('SELECT MAX(date) FROM computed_daily').fetchone()
print(row[0] if row and row[0] else '')
conn.close()
")

if [ -z "$LAST_DATE" ]; then
    echo "  ERROR: No data in computed_daily after build"
    exit 1
fi
echo "  Data coverage: up to $LAST_DATE"

# ── Sync ────────────────────────────────────────────────────────────────

if [ "$DRY_RUN" = true ]; then
    echo "[3] Dry run — skipping sync"
else
    echo "[3] Syncing to D1 (diff mode)..."
    "$PYTHON" "$SCRIPT_DIR/sync_to_d1.py" --diff --since "$LAST_DATE" $SYNC_ARGS
fi

# ── Update marker ──────────────────────────────────────────────────────

touch "$MARKER"

echo ""
echo "============================================================"
echo "  Done"
echo "============================================================"
