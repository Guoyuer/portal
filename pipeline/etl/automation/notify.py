"""Sync notifications: healthchecks.io pings + email summaries.

Healthchecks is recommended (see RUNBOOK §8). :class:`etl.automation.runner.
Runner` logs a loud warning at startup when the URL is unset but doesn't
fail — automation still runs. :func:`ping_healthcheck` itself stays tolerant
too: unset URL silently no-ops, network errors logged + swallowed.

Email is opt-in: set ``PORTAL_SMTP_USER`` + ``PORTAL_SMTP_PASSWORD``. A
no-change run is silently successful; failures and successful publish attempts
send a compact receipt. SMTP errors are logged and swallowed too.
"""
from __future__ import annotations

import logging
import os
import smtplib
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from etl.automation.receipt import (
    PublishSummary,
    SyncReceipt,
    SyncSnapshot,
    build_subject,
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
    """Extract WARNING messages, skipping healthcheck noise and duplicates."""
    warnings: list[str] = []
    seen: set[str] = set()
    for line in lines:
        msg = ""
        for marker in ("WARNING:", "WARNING "):
            if marker in line:
                msg = line.split(marker, 1)[1].strip()
                break
        if not msg or "healthcheck ping failed" in msg or msg in seen:
            continue
        seen.add(msg)
        warnings.append(msg)
    return warnings


def extract_validation_warnings(buffer: list[str] | None = None) -> list[str]:
    """Return validation WARNINGs captured from this run's subprocess output."""
    return _parse_warnings_from_lines(buffer or [])


def _fmt_duration(seconds: float) -> str:
    """Compact ``NmNNs``-style duration (or ``NNs`` when under a minute)."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s:02d}s"


def _build_context(
    exit_code: int,
    log_file: Path,
    error: str | None,
    warnings: list[str] | None,
    started_at: datetime | None = None,
    publish_summary: PublishSummary | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    """Assemble context consumed by format_text / format_html."""
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
        "publish_summary": publish_summary,
        "dry_run": dry_run,
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
    publish_summary: PublishSummary | None = None,
    dry_run: bool = False,
) -> None:
    """Build a compact publish receipt and send it.

    The orchestrator already short-circuits the silent no-change path before
    reaching this function (see ``Runner.run`` change-detection block), so every
    call here represents real work that ran end-to-end: failure, dry-run, or
    publish. The operator gets a concise confirmation either way.

    Delivery is best-effort: SMTP errors are logged and swallowed — email
    must never affect the sync exit code.
    """
    if config is None:
        return

    receipt = SyncReceipt(before=snapshot_before, after=snapshot_after)
    context = _build_context(
        exit_code,
        log_file,
        error,
        validation_warnings,
        started_at=started_at,
        publish_summary=publish_summary,
        dry_run=dry_run,
    )
    subject = build_subject(receipt, exit_code, _STATUS_LABELS.get(exit_code), publish_summary)
    html = format_html(receipt, context)
    text = format_text(receipt, context)

    try:
        send(subject, html, text, config)
        log.info("  Email sent to %s", config.email_to)
    except Exception as e:  # noqa: BLE001 — email failure must not abort sync
        log.error("  Email send FAILED (not fatal): %s", e)
