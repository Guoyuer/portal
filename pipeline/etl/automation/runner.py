"""Orchestration state machine: detect → build → export → publish R2.

The :class:`Runner` collaborates with the helper modules under
``etl.automation.*`` but owns all the control flow: graded exit codes,
pre-publish gates, dry-run semantics, marker update, and the final email report.

CLI parsing is here too because the script-side entry point shrinks to
``parse_args → Runner(args).run()`` and we want those bound together.

Exit-code taxonomy (constants live in :mod:`etl.automation._constants`):
    0 — ok, or no changes detected (both normal outcomes for cron)
    1 — build failed
    2 — export or dry-run artifact verification failed (do NOT publish)
    3 — R2 publish failed (publish verifies locally before upload)
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

from etl.automation.receipt import capture, load_publish_summary

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
        h.close()
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

def run_python_script(script: Path, *args: str) -> tuple[int, list[str]]:
    """Invoke a sibling Python script, stream stdout/stderr into the logger.

    Returns ``(exit_code, output_lines)``. The caller decides how to scope the
    captured lines; :class:`Runner` keeps them on the instance for its current
    run instead of using process-global state.
    """
    log = logging.getLogger(__name__)
    cmd = [sys.executable, str(script), *args]
    log.info("  > %s", " ".join(cmd))

    output: list[str] = []
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
        output.append(stripped)
    proc.wait()
    return proc.returncode, output


def _artifact_summary_path() -> Path:
    return SCRIPTS_DIR.parent / "artifacts" / "r2" / "reports" / "export-summary.json"


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Portal sync orchestrator (Task Scheduler shim: run_portal_sync.ps1)"
    )
    p.add_argument("--force", action="store_true",
                   help="Skip change detection and always build + publish")
    p.add_argument("--dry-run", action="store_true",
                   help="Run build + artifact verification but skip the final publish")
    return p.parse_args(argv)


# ── Runner ───────────────────────────────────────────────────────────────────

class Runner:
    """Detect → build → verify → publish state machine.

    All collaborators (``MARKER``, path helpers, the subprocess runner, etc.)
    are resolved through module attributes so tests can monkeypatch them at
    the canonical location (``etl.automation.runner.run_python_script`` etc.).

    Construct from a parsed ``argparse.Namespace``; the scripts-layer
    ``main()`` wraps that call and exits with :meth:`run`'s return code.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.started_at = datetime.now()
        self.script_output: list[str] = []
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

    def run(self) -> int:
        """Execute the state machine. Returns the process exit code."""
        self.script_output.clear()

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
        publish_summary = None

        def fail(
            label: str,
            rc: int,
            exit_code: int,
            script_name: str,
            *,
            include_publish_summary: bool = False,
        ) -> int:
            log.error("  %s FAILED (exit=%d)", label, rc)
            notify.ping_healthcheck("fail")
            notify.send_report_email(
                email_config, log, snapshot_before, capture(db_path),
                exit_code, log_file,
                error=f"{script_name} exited with code {rc}",
                validation_warnings=notify.extract_validation_warnings(self.script_output),
                started_at=self.started_at,
                publish_summary=publish_summary if include_publish_summary else None,
            )
            return exit_code

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
        rc = self._run_python_script(SCRIPTS_DIR / "build_timemachine_db.py")
        if rc != 0:
            return fail("BUILD", rc, EXIT_BUILD_FAIL, "build_timemachine_db.py")

        # [3] Optional Portfolio_Positions ground-truth gate.
        positions_csv = find_new_positions_csv(downloads, MARKER)
        if positions_csv:
            log.info("[3] Verifying share counts vs %s...", positions_csv.name)
            rc = self._run_python_script(
                SCRIPTS_DIR / "verify_positions.py", "--positions", str(positions_csv),
            )
            if rc != 0:
                return fail("POSITIONS CHECK", rc, EXIT_POSITIONS_FAIL, "verify_positions.py")
        else:
            log.info("[3] No new Portfolio_Positions CSV — skipping ground-truth check")

        # [4] Export endpoint-shaped R2 artifacts.
        log.info("[4] Exporting R2 artifacts...")
        rc = self._run_python_script(SCRIPTS_DIR / "r2_artifacts.py", "export")
        if rc != 0:
            return fail("EXPORT", rc, EXIT_PARITY_FAIL, "r2_artifacts.py export")
        publish_summary = load_publish_summary(_artifact_summary_path())

        if self.args.dry_run:
            log.info("[5] Verifying R2 artifacts...")
            rc = self._run_python_script(SCRIPTS_DIR / "r2_artifacts.py", "verify")
            if rc != 0:
                return fail("ARTIFACT VERIFY", rc, EXIT_PARITY_FAIL, "r2_artifacts.py verify")
            log.info("[6] Dry run — skipping R2 publish")
        else:
            log.info("[5] Publishing R2 artifacts (--remote)...")
            rc = self._run_python_script(SCRIPTS_DIR / "r2_artifacts.py", "publish", "--remote")
            if rc != 0:
                return fail(
                    "PUBLISH", rc, EXIT_SYNC_FAIL, "r2_artifacts.py publish",
                    include_publish_summary=True,
                )

        # Success: update marker — but NOT in dry-run mode. Dry-run skipped the
        # publish step, so nothing reached R2; touching the marker would make the
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
            validation_warnings=notify.extract_validation_warnings(self.script_output),
            started_at=self.started_at,
            publish_summary=publish_summary,
            dry_run=self.args.dry_run,
        )
        return EXIT_OK

    def _run_python_script(self, script: Path, *args: str) -> int:
        """Run a child script and retain its output for this Runner invocation."""
        rc, output = run_python_script(script, *args)
        self.script_output.extend(output)
        return rc
