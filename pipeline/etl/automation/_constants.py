"""Shared exit codes + status labels — broken out to avoid runner<->notify circular import."""
from __future__ import annotations

# ── Exit codes ───────────────────────────────────────────────────────────────

EXIT_OK = 0
EXIT_BUILD_FAIL = 1
EXIT_PARITY_FAIL = 2
EXIT_SYNC_FAIL = 3
EXIT_POSITIONS_FAIL = 4


# ── Status labels (email subject / body rendering) ───────────────────────────

_STATUS_LABELS = {
    EXIT_OK: "OK",
    EXIT_BUILD_FAIL: "BUILD FAILED",
    EXIT_PARITY_FAIL: "ARTIFACT VERIFY FAILED",
    EXIT_SYNC_FAIL: "R2 PUBLISH FAILED",
    EXIT_POSITIONS_FAIL: "POSITIONS GATE FAILED",
}
