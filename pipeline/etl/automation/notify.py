"""Sync notifications: healthchecks.io pings + email summaries.

Healthchecks is recommended (see RUNBOOK §8). :class:`etl.automation.runner.
Runner` logs a loud warning at startup when the URL is unset but doesn't
fail — automation still runs. :func:`ping_healthcheck` itself stays tolerant
too: unset URL silently no-ops, network errors logged + swallowed.

Email is opt-in: set ``PORTAL_SMTP_USER`` + ``PORTAL_SMTP_PASSWORD``. A no-
change run is silently successful; failures and meaningful-change successes
send. SMTP errors are logged and swallowed too.

The :func:`extract_validation_warnings` helper reads the per-run subprocess
capture buffer (populated by :func:`etl.automation.runner.run_python_script`)
and falls back to parsing the per-day log file's most recent banner block. See
PR-S8 Bug 1 for the scoping rationale.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from etl.changelog import (
    SyncChangelog,
    SyncSnapshot,
    build_subject,
    diff,
    format_html,
    format_text,
)

from ._constants import _STATUS_LABELS

# ── SMTP config ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class EmailConfig:
    """SMTP configuration sourced from env vars.

    Returned by :meth:`from_env`; ``None`` means email is disabled. Store the
    password here only; never log or serialize this instance.
    """

    smtp_host: str
    smtp_port: int
    smtp_user: str
    smtp_password: str
    email_from: str
    email_to: str

    @classmethod
    def from_env(cls) -> EmailConfig | None:
        """Build from env vars. Return None if required vars are unset."""
        user = os.environ.get("PORTAL_SMTP_USER")
        password = os.environ.get("PORTAL_SMTP_PASSWORD")
        if not user or not password:
            return None
        return cls(
            smtp_host=os.environ.get("PORTAL_SMTP_HOST", "smtp.gmail.com"),
            smtp_port=int(os.environ.get("PORTAL_SMTP_PORT", "587")),
            smtp_user=user,
            smtp_password=password,
            email_from=os.environ.get("PORTAL_EMAIL_FROM", user),
            email_to=os.environ.get("PORTAL_EMAIL_TO", user),
        )


# ── SMTP send ────────────────────────────────────────────────────────────────


def send(subject: str, html_body: str, text_body: str, config: EmailConfig) -> None:
    """Send a MIME multipart email (text + html). Raises on SMTP errors.

    Uses STARTTLS on the configured port (Gmail = 587). Caller is responsible
    for deciding whether to swallow the exception — here we surface it so the
    orchestrator can log the failure without crashing the sync.
    """
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = config.email_from
    msg["To"] = config.email_to
    msg.set_content(text_body)
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(config.smtp_host, config.smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(config.smtp_user, config.smtp_password)
        smtp.send_message(msg)


# ── Healthchecks.io ping ─────────────────────────────────────────────────────

def ping_healthcheck(suffix: str = "") -> None:
    """Ping ``PORTAL_HEALTHCHECK_URL`` (optionally ``/start`` or ``/fail``).

    Silent on error or when the env var is unset.
    """
    url = os.environ.get("PORTAL_HEALTHCHECK_URL")
    if not url:
        return
    target = f"{url}/{suffix}" if suffix else url
    try:
        urllib.request.urlopen(target, timeout=10).read()  # noqa: S310 — trusted env URL
    except (urllib.error.URLError, OSError) as e:
        logging.getLogger(__name__).warning("  healthcheck ping failed (ignored): %s", e)


# ── Email reporting ──────────────────────────────────────────────────────────

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


def extract_validation_warnings(
    log_file: Path | None = None,
    *,
    buffer: list[str] | None = None,
) -> list[str]:
    """Return validation WARNINGs captured from this run.

    Primary source is the subprocess capture ``buffer`` (typically obtained
    from :func:`etl.automation.runner.get_script_output_buffer`), which
    guarantees scoping to the CURRENT ``main()`` invocation. If that buffer is
    empty or not provided, falls back to parsing the tail of ``log_file``
    starting from the most recent ``"=" * 60`` banner — which matches the
    "Portal Sync" opening block emitted at each run.

    This two-tier approach keeps warnings from prior runs on the same day
    (the per-day log file is append-only) from leaking into the email body.
    """
    if buffer:
        return _parse_warnings_from_lines(buffer)

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


def send_report_email(
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
    """Build a changelog and send. Always attempts to send when reached.

    The orchestrator already short-circuits the silent no-change path before
    reaching this function (see ``Runner.run`` change-detection block), so
    every call here represents real work that ran end-to-end — failure or
    success. The operator gets a confirmation either way.

    Delivery is best-effort: SMTP errors are logged and swallowed — email
    must never affect the sync exit code.
    """
    if config is None:
        return

    if snapshot_after is not None:
        changelog = diff(snapshot_before, snapshot_after)
    else:
        changelog = SyncChangelog()

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
