"""Python orchestrator for the Portal sync pipeline (invoked by Task Scheduler shim).

Flow: change detection -> incremental build -> parity vs prod -> diff sync.
Logs per-day, optionally pings healthchecks.io, graded exit codes so the
scheduler can distinguish build-fail from parity-fail from sync-fail.

All orchestration logic lives under :mod:`etl.automation`; this script is a
thin argparse → :class:`~etl.automation.Runner` entry point so Task Scheduler
(via ``run_portal_sync.ps1``) has a stable CLI surface.

Exit code taxonomy:
    0 — ok, or no changes detected (both normal outcomes for cron)
    1 — build failed
    2 — verify_vs_prod failed (local <-> prod parity drift — do NOT sync)
    3 — sync failed
    4 — verify_positions failed (replay disagrees with Fidelity snapshot)

Email notifications (optional): set ``PORTAL_SMTP_USER`` + ``PORTAL_SMTP_PASSWORD``
and the orchestrator sends a changelog email on every run that detected real
changes, and on every failure. Silent no-change runs never email. See
``docs/automation-setup.md`` for Gmail app-password setup.

CLI (mirrors the previous PS1 flags):
    --force     Skip change detection
    --dry-run   Run build + verify but skip the sync step
    --local     Sync to local D1 (via wrangler --local) and skip verify_vs_prod

Environment variables:
    PORTAL_HEALTHCHECK_URL   Healthchecks.io base URL (recommended; see RUNBOOK §8)
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

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import etl.dotenv_loader  # noqa: E402, F401  (side effect: load pipeline/.env)
from etl.automation import Runner, parse_args  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    return Runner.from_args(args).run()


if __name__ == "__main__":
    sys.exit(main())
