"""Change detection — decides whether a sync run can short-circuit.

Two independent checks, both of which must be False to skip the build:
    * :func:`changes_detected` — watched file globs / Qianji DB newer than the
      ``.last_run`` marker?
    * :func:`needs_catchup` — is ``computed_daily``'s latest row more than
      ``STALE_DB_THRESHOLD_DAYS`` behind today? (Handles the "no CSV moved but
      yfinance has new closes" case.)

The marker is never written from this module — the Runner owns that so dry-run
can leave it untouched (see commit 26438cd).
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

# Patterns monitored for change detection. ``Portfolio_Positions_*.csv`` IS
# watched because a fresh snapshot is what triggers the [3b] ground-truth gate;
# the gate is skipped (not failed) when no such file is present, so including
# it here is safe for runs where only transactions changed.
WATCHED_PATTERNS = (
    "Accounts_History*.csv",
    "Bloomberg.Download*.qfx",
    "Robinhood_history.csv",
    "Portfolio_Positions_*.csv",
)

# Threshold for "DB is stale" — beyond this many calendar days behind today,
# run the pipeline even when no upstream CSV changed. Covers a standard
# weekend (2) + possible Monday holiday (3) + 1 day slack.
STALE_DB_THRESHOLD_DAYS = 4


def changes_detected(
    marker: Path,
    downloads: Path,
    qianji_db: Path | None,
    patterns: tuple[str, ...] = WATCHED_PATTERNS,
) -> bool:
    """True if marker missing, or any watched file is newer than marker."""
    log = logging.getLogger(__name__)

    if not marker.exists():
        return True  # first run

    marker_mtime = marker.stat().st_mtime

    if qianji_db and qianji_db.exists() and qianji_db.stat().st_mtime > marker_mtime:
        log.info("  Change detected: Qianji DB modified")
        return True

    if downloads.exists():
        for pattern in patterns:
            for candidate in downloads.glob(pattern):
                if candidate.is_file() and candidate.stat().st_mtime > marker_mtime:
                    log.info("  Change detected: new %s (%s)", pattern, candidate.name)
                    return True

    return False


def needs_catchup(db_path: Path, today: date | None = None) -> bool:
    """True if ``computed_daily``'s latest row is too stale to skip the build.

    Guards against the silent skip where :func:`changes_detected` returns False
    because no CSV moved but the DB itself hasn't been refreshed in days —
    yfinance has new closes, we should pick them up.
    """
    from etl.db import get_last_computed_date  # lazy; avoids circular import at module load

    log = logging.getLogger(__name__)
    today = today or date.today()

    try:
        last = get_last_computed_date(db_path)
    except Exception as e:  # noqa: BLE001 — DB unreadable is itself a reason to rebuild
        log.info("  Catchup needed: could not read last computed date (%s)", e)
        return True

    if last is None:
        log.info("  Catchup needed: computed_daily is empty")
        return True

    gap = (today - last).days
    if gap > STALE_DB_THRESHOLD_DAYS:
        log.info("  Catchup needed: computed_daily latest=%s (%d days behind)",
                 last.isoformat(), gap)
        return True

    log.info("  No catchup needed: computed_daily latest=%s (%d days behind)",
             last.isoformat(), gap)
    return False


def find_new_positions_csv(downloads: Path, marker: Path) -> Path | None:
    """Return newest ``Portfolio_Positions_*.csv`` in downloads newer than marker.

    Returns None if downloads is missing, no matching files exist, or (when the
    marker is present) all matching files are older than the marker. This makes
    the [3b] gate a no-op unless the user has actually dropped a fresh CSV.
    """
    if not downloads.exists():
        return None
    candidates = list(downloads.glob("Portfolio_Positions_*.csv"))
    if marker.exists():
        mtime = marker.stat().st_mtime
        candidates = [p for p in candidates if p.stat().st_mtime > mtime]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)
