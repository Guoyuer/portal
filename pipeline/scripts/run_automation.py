"""Python orchestrator for the Portal sync pipeline (invoked by Task Scheduler shim).

Flow: change detection -> incremental build -> parity vs prod -> diff sync.
Logs per-day, optionally pings healthchecks.io, graded exit codes so the
scheduler can distinguish build-fail from parity-fail from sync-fail.

Exit code taxonomy:
    0 — ok, or no changes detected (both normal outcomes for cron)
    1 — build failed
    2 — verify_vs_prod failed (local <-> prod parity drift — do NOT sync)
    3 — sync failed
    4 — verify_positions failed (replay disagrees with Fidelity snapshot — do NOT sync)

Email notifications (optional): set ``PORTAL_SMTP_USER`` + ``PORTAL_SMTP_PASSWORD``
and the orchestrator sends a changelog email on every run that detected real
changes, and on every failure. Silent no-change runs never email. See
``docs/automation-setup.md`` for Gmail app-password setup.

CLI (mirrors the previous PS1 flags):
    --force     Skip change detection
    --dry-run   Run build + verify but skip the sync step
    --local     Sync to local D1 (via wrangler --local) and skip verify_vs_prod

Environment variables:
    PORTAL_HEALTHCHECK_URL   Healthchecks.io base URL (optional; silent if unset)
    PORTAL_DOWNLOADS         Downloads dir (default: %USERPROFILE%\\Downloads)
    PORTAL_DB_PATH           timemachine.db path (default: pipeline/data/timemachine.db)
    PORTAL_SMTP_USER         Gmail address to send from (required for email)
    PORTAL_SMTP_PASSWORD     Gmail app password (required for email)
    PORTAL_SMTP_HOST         Default smtp.gmail.com
    PORTAL_SMTP_PORT         Default 587
    PORTAL_EMAIL_FROM        Default same as SMTP_USER
    PORTAL_EMAIL_TO          Default same as SMTP_USER
    APPDATA                  Qianji DB root on Windows
    LOCALAPPDATA             Log dir root on Windows
"""
from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)
from etl.changelog import (  # noqa: E402
    SyncChangelog,
    SyncSnapshot,
    build_subject,
    capture,
    diff,
    format_html,
    format_text,
)
from etl.email_report import EmailConfig, send  # noqa: E402

# ── Paths ─────────────────────────────────────────────────────────────────────

_SCRIPT_DIR = Path(__file__).resolve().parent
_PIPELINE_DIR = _SCRIPT_DIR.parent
_DATA_DIR = _PIPELINE_DIR / "data"
_MARKER = _DATA_DIR / ".last_run"


def get_db_path() -> Path:
    """timemachine.db location. Overridable via PORTAL_DB_PATH (used in tests)."""
    override = os.environ.get("PORTAL_DB_PATH")
    if override:
        return Path(override)
    return _DATA_DIR / "timemachine.db"

# Patterns monitored for change detection. Portfolio_Positions_*.csv IS watched
# because a fresh snapshot is what triggers the [3b] ground-truth gate; the gate
# is skipped (not failed) when no such file is present, so including it here is
# safe for runs where only transactions changed.
_WATCHED_PATTERNS = (
    "Accounts_History*.csv",
    "Bloomberg.Download*.qfx",
    "Robinhood_history.csv",
    "Portfolio_Positions_*.csv",
)

# Exit codes
EXIT_OK = 0
EXIT_BUILD_FAIL = 1
EXIT_PARITY_FAIL = 2
EXIT_SYNC_FAIL = 3
EXIT_POSITIONS_FAIL = 4


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

# Threshold for "DB is stale" — beyond this many calendar days behind today,
# run the pipeline even when no upstream CSV changed. Covers a standard
# weekend (2) + possible Monday holiday (3) + 1 day slack.
_STALE_DB_THRESHOLD_DAYS = 4


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


def needs_catchup(db_path: Path, today: date | None = None) -> bool:
    """True if ``computed_daily``'s latest row is too stale to skip the build.

    Guards against the silent skip where ``changes_detected`` returns False
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
    if gap > _STALE_DB_THRESHOLD_DAYS:
        log.info("  Catchup needed: computed_daily latest=%s (%d days behind)",
                 last.isoformat(), gap)
        return True

    log.info("  No catchup needed: computed_daily latest=%s (%d days behind)",
             last.isoformat(), gap)
    return False


# ── Positions CSV discovery ───────────────────────────────────────────────────

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


# ── Subprocess runner ─────────────────────────────────────────────────────────

# Per-process buffer of captured script output lines. Cleared at the start of
# each main() run so that warnings extracted for an email are scoped to the
# current invocation only — without this, re-reading the per-day log file would
# accumulate warnings from earlier runs on the same day, leading to duplicated
# validation messages in the email body (see PR-S8 Bug 1).
_SCRIPT_OUTPUT_BUFFER: list[str] = []


def run_python_script(script: Path, *args: str) -> int:
    """Invoke a sibling Python script, stream stdout/stderr into the logger.

    Each emitted line is also appended to ``_SCRIPT_OUTPUT_BUFFER`` so that the
    orchestrator can later extract warnings scoped to the *current* run
    (without re-reading the per-day log file, which accumulates lines from
    every prior invocation).

    Returns the subprocess exit code.
    """
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
        stripped = line.rstrip("\n")
        log.info(stripped)
        _SCRIPT_OUTPUT_BUFFER.append(stripped)
    proc.wait()
    return proc.returncode


def _reset_script_output_buffer() -> None:
    """Clear the per-run capture buffer. Call once at the start of main()."""
    _SCRIPT_OUTPUT_BUFFER.clear()


def _report_stage_failure(
    log: logging.Logger,
    label: str,
    rc: int,
    exit_code: int,
    script_name: str,
    email_config: EmailConfig | None,
    snapshot_before: SyncSnapshot,
    db_path: Path,
    log_file: Path,
    started_at: datetime | None = None,
) -> int:
    """Shared error-report path used by every stage in ``main()``.

    Logs the failure, pings healthcheck, sends the failure email, and returns
    the stage-specific exit code. Collapses four near-identical blocks in
    ``main()`` that differ only by ``label`` / ``exit_code`` / ``script_name``.
    """
    log.error("  %s FAILED (exit=%d)", label, rc)
    ping_healthcheck("fail")
    _send_report_email(
        email_config, log, snapshot_before, capture(db_path),
        exit_code, log_file,
        error=f"{script_name} exited with code {rc}",
        validation_warnings=extract_validation_warnings(log_file),
        started_at=started_at,
    )
    return exit_code


def get_script_output_buffer() -> list[str]:
    """Return a *copy* of the captured subprocess lines for THIS main() run."""
    return list(_SCRIPT_OUTPUT_BUFFER)


# ── Email reporting ───────────────────────────────────────────────────────────

_STATUS_LABELS = {
    EXIT_OK: "OK",
    EXIT_BUILD_FAIL: "BUILD FAILED",
    EXIT_PARITY_FAIL: "PARITY GATE FAILED",
    EXIT_SYNC_FAIL: "SYNC FAILED",
    EXIT_POSITIONS_FAIL: "POSITIONS GATE FAILED",
}


def _parse_warnings_from_lines(lines: list[str]) -> list[str]:
    """Extract ``validate_build`` WARNING messages from an iterable of log lines.

    Matches lines containing ``"WARNING"`` followed by a colon or space. Skips
    healthcheck noise and de-duplicates exact repeats while preserving order
    (defense in depth: even if a caller passes a multi-run buffer, repeated
    warnings collapse to one entry).
    """
    warnings: list[str] = []
    for line in lines:
        m = re.search(r"WARNING[: ]\s*(.+)", line)
        if not m:
            continue
        msg = m.group(1).strip()
        if not msg or "healthcheck ping failed" in msg:
            continue
        warnings.append(msg)
    # dict.fromkeys preserves first-seen order while dropping duplicates.
    return list(dict.fromkeys(warnings))


def extract_validation_warnings(log_file: Path | None = None) -> list[str]:
    """Return validation WARNINGs captured from this run.

    Primary source is the in-memory subprocess capture buffer, which guarantees
    scoping to the CURRENT ``main()`` invocation. If that buffer is empty
    (e.g. subprocess hooks were bypassed in a test or the caller passed a path
    explicitly), falls back to parsing the tail of ``log_file`` starting from
    the most recent ``"=" * 60`` banner — which matches the "Portal Sync"
    opening block emitted by :func:`main` at each run.

    This two-tier approach keeps warnings from prior runs on the same day
    (the per-day log file is append-only) from leaking into the email body.
    """
    buffered = get_script_output_buffer()
    if buffered:
        return _parse_warnings_from_lines(buffered)

    if log_file is None or not log_file.exists():
        return []
    try:
        with log_file.open("r", encoding="utf-8", errors="replace") as fh:
            all_lines = fh.readlines()
    except OSError:
        return []

    # Find the start of the *current* run: the last "============" banner. The
    # orchestrator writes three banner lines ("=" * 60, "Portal Sync", "=" * 60)
    # at the top of each main() run; slicing from the last banner onward gives
    # us only the current run's output.
    banner = "=" * 60
    last_banner_idx = -1
    for i, line in enumerate(all_lines):
        if banner in line:
            last_banner_idx = i
    tail = all_lines[last_banner_idx:] if last_banner_idx >= 0 else all_lines
    return _parse_warnings_from_lines([ln.rstrip("\n") for ln in tail])


def _fmt_duration(seconds: float) -> str:
    """Compact ``NmNNs``-style duration (or ``NNs`` when under a minute)."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _build_context(
    changelog: SyncChangelog,
    exit_code: int,
    log_file: Path,
    snapshot_before: SyncSnapshot,
    snapshot_after: SyncSnapshot | None,
    error: str | None,
    warnings: list[str] | None,
    started_at: datetime | None = None,
) -> dict[str, object]:
    """Assemble the template context dict consumed by format_text / format_html."""
    before_dates = sorted(snapshot_before.computed_daily.keys()) if snapshot_before.computed_daily else []
    after_dates: list[str] = []
    econ_keys: list[str] = []
    if snapshot_after is not None:
        after_dates = sorted(snapshot_after.computed_daily.keys())
        econ_keys = sorted(snapshot_after.econ_series_keys)
    duration = ""
    if started_at is not None:
        duration = _fmt_duration((datetime.now() - started_at).total_seconds())
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "status_label": _STATUS_LABELS.get(exit_code, f"EXIT {exit_code}"),
        "exit_code": exit_code,
        "log_file": str(log_file),
        "error": error,
        "warnings": warnings or [],
        "before_dates": before_dates,
        "after_dates": after_dates,
        "econ_keys": econ_keys,
        "duration": duration,
    }


def _send_report_email(
    config: EmailConfig | None,
    log: logging.Logger,
    snapshot_before: SyncSnapshot,
    snapshot_after: SyncSnapshot | None,
    exit_code: int,
    log_file: Path,
    error: str | None = None,
    validation_warnings: list[str] | None = None,
    started_at: datetime | None = None,
) -> None:
    """Build a changelog, decide whether to send, send if yes.

    Policy:
        exit_code != 0                                 -> always send
        exit_code == 0 and has_meaningful_changes      -> send
        exit_code == 0 and no meaningful changes       -> skip

    Errors during SMTP are logged and swallowed — email must never affect the
    sync exit code.
    """
    if config is None:
        return

    if snapshot_after is not None:
        changelog = diff(snapshot_before, snapshot_after)
    else:
        changelog = SyncChangelog()

    should_send = exit_code != 0 or changelog.has_meaningful_changes()
    if not should_send:
        log.info("  Email skipped (no meaningful changes)")
        return

    context = _build_context(
        changelog, exit_code, log_file, snapshot_before, snapshot_after, error,
        validation_warnings, started_at=started_at,
    )
    subject = build_subject(changelog, exit_code)
    html = format_html(changelog, context)
    text = format_text(changelog, context)

    try:
        send(subject, html, text, config)
        log.info("  Email sent to %s", config.email_to)
    except Exception as e:  # noqa: BLE001 — email failure must not abort sync
        log.error("  Email send FAILED (not fatal): %s", e)


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
    p.add_argument(
        "--expected-drops",
        action="append", default=[],
        metavar="TABLE=N",
        help=(
            "Declare an intentional row-count drop on TABLE of exactly N rows "
            "(passes through to verify_vs_prod). Use when an ingest-logic "
            "change legitimately removes rows from the local DB that still "
            "exist in prod — e.g. after filtering balance-adjustment bills. "
            "Repeatable."
        ),
    )
    return p.parse_args(argv)


# ── Main ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    started_at = datetime.now()

    # Reset the per-run subprocess capture so validation warnings extracted
    # later in this invocation never include leftovers from a previous call
    # to main() inside the same Python process (tests, or a hypothetical
    # long-lived orchestrator).
    _reset_script_output_buffer()

    log_dir = get_log_dir()
    log_file = setup_logging(log_dir)
    log = logging.getLogger(__name__)

    downloads = get_downloads_dir()
    qianji_db = get_qianji_db_path()
    db_path = get_db_path()

    hostname = os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "?"
    log.info("=" * 60)
    log.info("  Portal Sync")
    log.info("  host=%s log=%s", hostname, log_file)
    log.info("=" * 60)

    email_config = EmailConfig.from_env()
    log.info(
        "  Email reporting: %s",
        "enabled" if email_config else "disabled (no PORTAL_SMTP_USER/PASSWORD)",
    )

    ping_healthcheck("start")

    # Snapshot BEFORE build so we can diff regardless of which exit branch fires.
    snapshot_before = capture(db_path)

    # [1] Change detection + DB freshness catchup
    if not args.force:
        log.info("[1] Checking for data changes + DB freshness...")
        if not changes_detected(_MARKER, downloads, qianji_db) and not needs_catchup(db_path):
            log.info("  Validated up to date. Use --force to override.")
            ping_healthcheck()  # no-change is a valid success outcome
            # No-change is a silent success — never email.
            return EXIT_OK
    else:
        log.info("[1] Force mode — skipping change detection")

    # [2] Build (refresh window + gap-fill; first run falls back to full)
    log.info("[2] Build...")
    rc = run_python_script(_SCRIPT_DIR / "build_timemachine_db.py")
    if rc != 0:
        return _report_stage_failure(
            log, "BUILD", rc, EXIT_BUILD_FAIL, "build_timemachine_db.py",
            email_config, snapshot_before, db_path, log_file,
            started_at=started_at,
        )

    # [3] Pre-sync gate: guard against local data loss + historical drift
    if not args.local:
        log.info("[3] Verifying historical immutability + no local data loss vs prod D1...")
        gate_args: list[str] = []
        for spec in args.expected_drops:
            gate_args.extend(["--expected-drops", spec])
        rc = run_python_script(_SCRIPT_DIR / "verify_vs_prod.py", *gate_args)
        if rc != 0:
            return _report_stage_failure(
                log, "PRE-SYNC GATE", rc, EXIT_PARITY_FAIL, "verify_vs_prod.py",
                email_config, snapshot_before, db_path, log_file,
                started_at=started_at,
            )

    # [3b] Optional Portfolio_Positions ground-truth gate
    if not args.local:
        positions_csv = find_new_positions_csv(downloads, _MARKER)
        if positions_csv:
            log.info("[3b] Verifying share counts vs %s...", positions_csv.name)
            rc = run_python_script(_SCRIPT_DIR / "verify_positions.py",
                                   "--positions", str(positions_csv))
            if rc != 0:
                return _report_stage_failure(
                    log, "POSITIONS CHECK", rc, EXIT_POSITIONS_FAIL, "verify_positions.py",
                    email_config, snapshot_before, db_path, log_file,
                )
        else:
            log.info("[3b] No new Portfolio_Positions CSV — skipping ground-truth check")

    # [4] Sync (skipped in dry-run)
    if args.dry_run:
        log.info("[4] Dry run — skipping sync")
    else:
        log.info("[4] Syncing to D1 (diff mode — default)...")
        sync_args: tuple[str, ...] = ("--local",) if args.local else ()
        rc = run_python_script(_SCRIPT_DIR / "sync_to_d1.py", *sync_args)
        if rc != 0:
            return _report_stage_failure(
                log, "SYNC", rc, EXIT_SYNC_FAIL, "sync_to_d1.py",
                email_config, snapshot_before, db_path, log_file,
                started_at=started_at,
            )

    # Success: update marker
    _MARKER.parent.mkdir(parents=True, exist_ok=True)
    _MARKER.write_text(datetime.now().isoformat(timespec="seconds"))
    log.info("=" * 60)
    log.info("  Done")
    log.info("=" * 60)
    ping_healthcheck()
    _send_report_email(
        email_config, log, snapshot_before, capture(db_path),
        EXIT_OK, log_file,
        validation_warnings=extract_validation_warnings(log_file),
        started_at=started_at,
    )
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
