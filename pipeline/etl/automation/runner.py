"""Orchestration state machine: detect → build → verify → sync.

The :class:`Runner` collaborates with the helper modules under
``etl.automation.*`` but owns all the control flow: graded exit codes, the
pre-sync gates, dry-run semantics, marker update, and the final email report.

CLI parsing is here too because the script-side entry point shrinks to
``parse_args → Runner.from_args → run`` and we want those bound together.

Exit-code taxonomy (constants live in :mod:`etl.automation._constants`):
    0 — ok, or no changes detected (both normal outcomes for cron)
    1 — build failed
    2 — verify_vs_prod failed (local <-> prod parity drift — do NOT sync)
    3 — sync failed
    4 — verify_positions failed (replay disagrees with Fidelity snapshot)
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from etl.changelog import SyncSnapshot, capture

from . import notify
from ._constants import (
    EXIT_BUILD_FAIL,
    EXIT_OK,
    EXIT_PARITY_FAIL,
    EXIT_POSITIONS_FAIL,
    EXIT_SYNC_FAIL,
)
from .changes import changes_detected, find_new_positions_csv, needs_catchup
from .notify import EmailConfig
from .paths import (
    MARKER,
    SCRIPTS_DIR,
    get_db_path,
    get_downloads_dir,
    get_log_dir,
    get_qianji_db_path,
)

log = logging.getLogger(__name__)


# ── Logging ──────────────────────────────────────────────────────────────────

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


# ── Subprocess runner ────────────────────────────────────────────────────────

# Per-process buffer of captured script output lines. Cleared at the start of
# each Runner.run() call so that warnings extracted for an email are scoped to
# the current invocation only — without this, re-reading the per-day log file
# would accumulate warnings from earlier runs on the same day, leading to
# duplicated validation messages in the email body (see PR-S8 Bug 1).
_SCRIPT_OUTPUT_BUFFER: list[str] = []


def run_python_script(script: Path, *args: str) -> int:
    """Invoke a sibling Python script, stream stdout/stderr into the logger.

    Each emitted line is also appended to :data:`_SCRIPT_OUTPUT_BUFFER` so that
    the orchestrator can later extract warnings scoped to the *current* run
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
    """Clear the per-run capture buffer. Call once at the start of Runner.run()."""
    _SCRIPT_OUTPUT_BUFFER.clear()


def get_script_output_buffer() -> list[str]:
    """Return a *copy* of the captured subprocess lines for THIS run."""
    return list(_SCRIPT_OUTPUT_BUFFER)


# ── CLI ──────────────────────────────────────────────────────────────────────

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


# ── Runner ───────────────────────────────────────────────────────────────────

class Runner:
    """Detect → build → verify → sync state machine.

    All collaborators (``MARKER``, path helpers, the subprocess runner, etc.)
    are resolved through module attributes so tests can monkeypatch them at
    the canonical location (``etl.automation.runner.run_python_script`` etc.).

    Construct via :meth:`from_args` from a parsed ``argparse.Namespace``; the
    scripts-layer ``main()`` wraps that call and exits with :meth:`run`'s
    return code.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.started_at = datetime.now()
        # Warn (but don't fail) if the healthcheck URL isn't configured.
        # Without it, ``notify.ping_healthcheck`` silently no-ops and a dead
        # healthchecks.io check would never fire on failure. Loud warning keeps
        # it in front of the operator without breaking automation for users
        # who haven't set up healthchecks.io yet.
        if not os.environ.get("PORTAL_HEALTHCHECK_URL"):
            log.warning(
                "PORTAL_HEALTHCHECK_URL is not set — automation failures will "
                "only surface via email, not an external monitor. "
                "See docs/RUNBOOK.md §8 to configure.",
            )

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> Runner:
        return cls(args)

    def run(self) -> int:
        """Execute the state machine. Returns the process exit code."""
        # Reset the per-run subprocess capture so validation warnings extracted
        # later in this invocation never include leftovers from a previous call
        # inside the same Python process (tests, or a hypothetical long-lived
        # orchestrator).
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

        notify.ping_healthcheck("start")

        # Snapshot BEFORE build so we can diff regardless of which exit branch fires.
        snapshot_before = capture(db_path)

        # [1] Change detection + DB freshness catchup
        if not self.args.force:
            log.info("[1] Checking for data changes + DB freshness...")
            if not changes_detected(MARKER, downloads, qianji_db) and not needs_catchup(db_path):
                log.info("  Validated up to date. Use --force to override.")
                notify.ping_healthcheck()  # no-change is a valid success outcome
                # No-change is a silent success — never email.
                return EXIT_OK
        else:
            log.info("[1] Force mode — skipping change detection")

        # [2] Build (refresh window + gap-fill; first run falls back to full)
        log.info("[2] Build...")
        rc = run_python_script(SCRIPTS_DIR / "build_timemachine_db.py")
        if rc != 0:
            return self._report_stage_failure(
                log, "BUILD", rc, EXIT_BUILD_FAIL, "build_timemachine_db.py",
                email_config, snapshot_before, db_path, log_file,
            )

        # [3] Pre-sync gate: guard against local data loss + historical drift
        if not self.args.local:
            log.info("[3] Verifying historical immutability + no local data loss vs prod D1...")
            gate_args: list[str] = []
            for spec in self.args.expected_drops:
                gate_args.extend(["--expected-drops", spec])
            rc = run_python_script(SCRIPTS_DIR / "verify_vs_prod.py", *gate_args)
            if rc != 0:
                return self._report_stage_failure(
                    log, "PRE-SYNC GATE", rc, EXIT_PARITY_FAIL, "verify_vs_prod.py",
                    email_config, snapshot_before, db_path, log_file,
                )

        # [3b] Optional Portfolio_Positions ground-truth gate
        if not self.args.local:
            positions_csv = find_new_positions_csv(downloads, MARKER)
            if positions_csv:
                log.info("[3b] Verifying share counts vs %s...", positions_csv.name)
                rc = run_python_script(SCRIPTS_DIR / "verify_positions.py",
                                       "--positions", str(positions_csv))
                if rc != 0:
                    return self._report_stage_failure(
                        log, "POSITIONS CHECK", rc, EXIT_POSITIONS_FAIL, "verify_positions.py",
                        email_config, snapshot_before, db_path, log_file,
                    )
            else:
                log.info("[3b] No new Portfolio_Positions CSV — skipping ground-truth check")

        # [4] Sync (skipped in dry-run)
        if self.args.dry_run:
            log.info("[4] Dry run — skipping sync")
        else:
            log.info("[4] Syncing to D1 (diff mode — default)...")
            sync_args: tuple[str, ...] = ("--local",) if self.args.local else ()
            rc = run_python_script(SCRIPTS_DIR / "sync_to_d1.py", *sync_args)
            if rc != 0:
                return self._report_stage_failure(
                    log, "SYNC", rc, EXIT_SYNC_FAIL, "sync_to_d1.py",
                    email_config, snapshot_before, db_path, log_file,
                )

        # Success: update marker — but NOT in dry-run mode. Dry-run skipped the
        # sync step, so nothing reached D1; touching the marker would make the
        # next change-detection pass treat the DB as already synced and short-
        # circuit a legitimate follow-up run.
        if not self.args.dry_run:
            MARKER.parent.mkdir(parents=True, exist_ok=True)
            MARKER.write_text(datetime.now().isoformat(timespec="seconds"))
        log.info("=" * 60)
        log.info("  Done")
        log.info("=" * 60)
        notify.ping_healthcheck()
        notify.send_report_email(
            email_config, log, snapshot_before, capture(db_path),
            EXIT_OK, log_file,
            validation_warnings=notify.extract_validation_warnings(
                log_file, buffer=get_script_output_buffer(),
            ),
            started_at=self.started_at,
        )
        return EXIT_OK

    def _report_stage_failure(
        self,
        log: logging.Logger,
        label: str,
        rc: int,
        exit_code: int,
        script_name: str,
        email_config: EmailConfig | None,
        snapshot_before: SyncSnapshot,
        db_path: Path,
        log_file: Path,
    ) -> int:
        """Shared error-report path used by every stage in :meth:`run`.

        Logs the failure, pings healthcheck, sends the failure email (with the
        per-run duration always included), and returns the stage-specific exit
        code. Collapses four near-identical blocks that differ only by
        ``label`` / ``exit_code`` / ``script_name``.
        """
        log.error("  %s FAILED (exit=%d)", label, rc)
        notify.ping_healthcheck("fail")
        notify.send_report_email(
            email_config, log, snapshot_before, capture(db_path),
            exit_code, log_file,
            error=f"{script_name} exited with code {rc}",
            validation_warnings=notify.extract_validation_warnings(
                log_file, buffer=get_script_output_buffer(),
            ),
            started_at=self.started_at,
        )
        return exit_code
