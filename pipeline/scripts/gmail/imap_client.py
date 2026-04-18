"""Minimal Gmail IMAP client: connect, login, search unread last 24h, fetch.

Stdlib imaplib + email. Returns plain dataclasses so downstream modules
don't depend on imaplib's awkward response shapes.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
import logging
from dataclasses import dataclass
from datetime import UTC, date, timedelta


@dataclass(frozen=True)
class ImapConfig:
    user: str
    password: str
    host: str = "imap.gmail.com"
    port: int = 993


@dataclass(frozen=True)
class ParsedMessage:
    msg_id: str          # Message-ID header with angle brackets
    received_at: str     # ISO 8601 UTC (from Date: header) — "" if missing
    sender: str          # raw From: value
    subject: str
    body_excerpt: str    # first ~500 chars of text body


def _imap_date(d: date) -> str:
    """Format for IMAP SEARCH (e.g. '12-Apr-2026')."""
    return d.strftime("%d-%b-%Y")


def _normalize_date(raw: str) -> str:
    """Parse RFC 2822 date to ISO 8601 UTC. Returns '' if unparseable."""
    if not raw:
        return ""
    try:
        dt = email.utils.parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC).isoformat()
    except (TypeError, ValueError):
        return ""


def fetch_unread_last_24h(config: ImapConfig) -> list[ParsedMessage]:
    """Return unread INBOX messages received since yesterday (day-granular)."""
    m = imaplib.IMAP4_SSL(config.host, config.port)
    try:
        m.login(config.user, config.password)
        m.select("INBOX")
        since = _imap_date(date.today() - timedelta(days=1))
        status, data = m.uid("SEARCH", "UNSEEN", "SINCE", since)
        if status != "OK" or not data or not data[0]:
            return []
        uids = data[0].split()
        out: list[ParsedMessage] = []
        for uid in uids:
            status, fetched = m.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK" or not fetched:
                continue
            for part in fetched:
                if isinstance(part, tuple) and len(part) >= 2:
                    raw = part[1]
                    if isinstance(raw, bytes):
                        out.append(parse_message(raw))
                        break
        return out
    finally:
        # LOGOUT is a best-effort cleanup; the socket will get closed either
        # way. But surface the reason if it fails so we notice a broken
        # connection rather than silently leaking the session.
        try:
            m.logout()
        except Exception as e:  # noqa: BLE001 — imaplib raises OSError/imaplib.error; any is cleanup noise
            logging.getLogger(__name__).warning("imap logout failed: %s", e)


def parse_message(raw: bytes) -> ParsedMessage:
    """Parse raw RFC 5322 bytes into a ParsedMessage."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    msg_id = (msg["Message-ID"] or "").strip()
    sender = (msg["From"] or "").strip()
    subject = (msg["Subject"] or "").strip()
    received_at = _normalize_date((msg["Date"] or "").strip())

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_content()
                break
        if not body:
            for part in msg.walk():
                if part.get_content_type() == "text/html":
                    body = part.get_content()
                    break
    else:
        body = msg.get_content() if msg.get_content_type() == "text/plain" else ""

    excerpt = body[:500].strip()
    return ParsedMessage(
        msg_id=msg_id, received_at=received_at, sender=sender,
        subject=subject, body_excerpt=excerpt,
    )
