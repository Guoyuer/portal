"""Python orchestrator for the Portal sync pipeline (invoked by Task Scheduler shim).

Flow: change detection -> incremental build -> parity vs prod -> diff sync.
Logs per-day, optionally pings healthchecks.io, graded exit codes so the
scheduler can distinguish build-fail from parity-fail from sync-fail.

Exit code taxonomy:
    0 — ok, or no changes detected (both normal outcomes for cron)
    1 — build failed
    2 — verify_vs_prod failed (local <-> prod parity drift — do NOT sync)
    3 — sync failed

CLI (mirrors the previous PS1 flags):
    --force     Skip change detection
    --dry-run   Run build + verify but skip the sync step
    --local     Sync to local D1 (via wrangler --local) and skip verify_vs_prod

Environment variables:
    PORTAL_HEALTHCHECK_URL   Healthchecks.io base URL (optional; silent if unset)
    PORTAL_DOWNLOADS         Downloads dir (default: %USERPROFILE%\\Downloads)
    PORTAL_DB_PATH           timemachine.db path (default: pipeline/data/timemachine.db)
    APPDATA                  Qianji DB root on Windows
    LOCALAPPDATA             Log dir root on Windows
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPT_DIR.parent
_DATA_DIR = _PIPELINE_DIR / "data"
_MARKER = _DATA_DIR / ".last_run"

# Patterns monitored for change detection. Portfolio_Positions is intentionally
# EXCLUDED — those snapshots are rarely fresh in automation contexts (Option A).
_WATCHED_PATTERNS = (
    "Accounts_History*.csv",
    "Bloomberg.Download*.qfx",
    "Robinhood_history.csv",
)

# Exit codes
EXIT_OK = 0
EXIT_BUILD_FAIL = 1
EXIT_PARITY_FAIL = 2
EXIT_SYNC_FAIL = 3


# ── Paths helpers (env-var aware) ─────────────────────────────────────────────

def get_downloads_dir() -> Path:
    override = os.environ.get("PORTAL_DOWNLOADS")
    if override:
        return Path(override)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile) / "Downloads"
    return Path.home() / "Downloads"


def get_qianji_db_path() -> Path | None:
    """Location of Qianji's Windows app DB. Returns None if APPDATA unset."""
    appdata = os.environ.get("APPDATA")
    if not appdata:
        return None
    return Path(appdata) / "com.mutangtech.qianji.win" / "qianji_flutter" / "qianjiapp.db"


def get_log_dir() -> Path:
    """Per-day log directory. Prefers %LOCALAPPDATA%\\portal\\logs on Windows."""
    localappdata = os.environ.get("LOCALAPPDATA")
    if localappdata:
        return Path(localappdata) / "portal" / "logs"
    return Path.home() / ".local" / "share" / "portal" / "logs"


# ── Logging ───────────────────────────────────────────────────────────────────

def setup_logging(log_dir: Path) -> Path:
    """Configure root logger with stdout + per-day file handler. Returns log path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = log_dir / f"sync-{today}.log"

    root = logging.getLogger()
    # Clear any prior handlers so repeated invocations don't stack duplicates.
    for h in list(root.handlers):
        root.removeHandler(h)
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(fh)
    root.addHandler(sh)

    return log_file


# ── Healthchecks.io ping ──────────────────────────────────────────────────────

def ping_healthcheck(suffix: str = "") -> None:
    """Ping PORTAL_HEALTHCHECK_URL (optionally /start or /fail). Silent on error or if unset."""
    url = os.environ.get("PORTAL_HEALTHCHECK_URL")
    if not url:
        return
    target = f"{url}/{suffix}" if suffix else url
    try:
        urllib.request.urlopen(target, timeout=10).read()  # noqa: S310 — trusted env URL
    except (urllib.error.URLError, OSError) as e:
        logging.getLogger(__name__).warning("  healthcheck ping failed (ignored): %s", e)


# ── Change detection ──────────────────────────────────────────────────────────

def changes_detected(
    marker: Path,
    downloads: Path,
    qianji_db: Path | None,
    patterns: tuple[str, ...] = _WATCHED_PATTERNS,
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


# ── Subprocess runner ─────────────────────────────────────────────────────────

def run_python_script(script: Path, *args: str) -> int:
    """Invoke a sibling Python script, stream stdout/stderr into the logger. Returns exit code."""
    log = logging.getLogger(__name__)
    cmd = [sys.executable, str(script), *args]
    log.info("  > %s", " ".join(cmd))

    proc = subprocess.Popen(  # noqa: S603 — fixed script path, controlled args
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        log.info(line.rstrip("\n"))
    proc.wait()
    return proc.returncode


# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Portal sync orchestrator (Task Scheduler shim: run_portal_sync.ps1)"
    )
    p.add_argument("--force", action="store_true",
                   help="Skip change detection and always build + sync")
    p.add_argument("--dry-run", action="store_true",
                   help="Run build + verify but skip the final sync")
    p.add_argument("--local", action="store_true",
                   help="Sync to local D1 (wrangler --local); skips verify_vs_prod")
    return p.parse_args(argv)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    log_dir = get_log_dir()
    log_file = setup_logging(log_dir)
    log = logging.getLogger(__name__)

    downloads = get_downloads_dir()
    qianji_db = get_qianji_db_path()

    hostname = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"
    log.info("=" * 60)
    log.info("  Portal Sync")
    log.info("  host=%s log=%s", hostname, log_file)
    log.info("=" * 60)

    ping_healthcheck("start")

    # [1] Change detection
    if not args.force:
        log.info("[1] Checking for data changes...")
        if not changes_detected(_MARKER, downloads, qianji_db):
            log.info("  No changes detected. Use --force to override.")
            ping_healthcheck()  # no-change is a valid success outcome
            return EXIT_OK
    else:
        log.info("[1] Force mode — skipping change detection")

    # [2] Build
    log.info("[2] Incremental build...")
    rc = run_python_script(_SCRIPT_DIR / "build_timemachine_db.py", "incremental")
    if rc != 0:
        log.error("  BUILD FAILED (exit=%d)", rc)
        ping_healthcheck("fail")
        return EXIT_BUILD_FAIL

    # [3] Pre-sync gate: guard against local data loss + historical drift
    if not args.local:
        log.info("[3] Verifying historical immutability + no local data loss vs prod D1...")
        rc = run_python_script(_SCRIPT_DIR / "verify_vs_prod.py")
        if rc != 0:
            log.error("  PRE-SYNC GATE FAILED (exit=%d) — SYNC BLOCKED", rc)
            ping_healthcheck("fail")
            return EXIT_PARITY_FAIL

    # [4] Sync (skipped in dry-run)
    if args.dry_run:
        log.info("[4] Dry run — skipping sync")
    else:
        log.info("[4] Syncing to D1 (diff mode — default)...")
        sync_args: tuple[str, ...] = ("--local",) if args.local else ()
        rc = run_python_script(_SCRIPT_DIR / "sync_to_d1.py", *sync_args)
        if rc != 0:
            log.error("  SYNC FAILED (exit=%d)", rc)
            ping_healthcheck("fail")
            return EXIT_SYNC_FAIL

    # Success: update marker
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text(datetime.now().isoformat(timespec="seconds"))
    log.info("=" * 60)
    log.info("  Done")
    log.info("=" * 60)
    ping_healthcheck()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
