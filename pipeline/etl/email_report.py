"""Gmail SMTP sender for sync notifications.

Stdlib only (smtplib + email.message). Env vars ``PORTAL_SMTP_USER`` and
``PORTAL_SMTP_PASSWORD`` toggle the feature on; if either is missing the
module returns ``None`` from :meth:`EmailConfig.from_env` and the caller
skips sending.

Host/port/from/to default to Gmail + self-email, overridable via env vars.
The password is NEVER logged — only the "enabled/disabled" state is.
"""
from __future__ import annotations

import os
import smtplib
from dataclasses import dataclass
from email.message import EmailMessage

# ── Config ───────────────────────────────────────────────────────────────────


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


# ── Send ─────────────────────────────────────────────────────────────────────


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
