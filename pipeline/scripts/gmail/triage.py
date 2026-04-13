"""Gmail triage CLI entry point (v2 — no digest, posts to Worker /mail/sync).

    python scripts/gmail/triage.py --sync            # full run
    python scripts/gmail/triage.py --sync --dry-run  # print rows, skip Worker

Env vars:
    PORTAL_SMTP_USER, PORTAL_SMTP_PASSWORD     (Gmail IMAP login)
    PORTAL_GMAIL_WORKER_URL                    (Worker base URL)
    PORTAL_GMAIL_SYNC_SECRET                   (shared with Worker env SYNC_SECRET)
    ANTHROPIC_API_KEY
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

# Add pipeline/scripts/ to sys.path so `from gmail.XXX import ...` works when
# running this file directly. etl/ is not imported here (we dropped email_report).
_scripts_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_scripts_dir))

from gmail.classify import classify_emails  # noqa: E402
from gmail.imap_client import ImapConfig, fetch_unread_last_24h  # noqa: E402
from gmail.worker_sync import WorkerSyncClient, WorkerSyncError  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gmail.triage")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gmail triage: fetch + classify + sync to Worker")
    p.add_argument("--sync", action="store_true", help="Run fetch+classify+sync")
    p.add_argument("--dry-run", action="store_true", help="Print rows to stdout, skip Worker call")
    return p.parse_args(argv)


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"error: missing env var {name}", file=sys.stderr)
        sys.exit(1)
    return v


def run_sync(dry_run: bool) -> int:
    smtp_user = _require_env("PORTAL_SMTP_USER")
    smtp_password = _require_env("PORTAL_SMTP_PASSWORD")
    anthropic_key = _require_env("ANTHROPIC_API_KEY")

    log.info("fetching unread emails from last 24h...")
    emails = fetch_unread_last_24h(ImapConfig(user=smtp_user, password=smtp_password))
    log.info("fetched %d emails", len(emails))

    if not emails:
        log.info("nothing to classify — exiting 0")
        return 0

    log.info("classifying...")
    classifications = classify_emails(emails, api_key=anthropic_key)

    classified_at = datetime.now(UTC).isoformat()
    rows: list[dict[str, object]] = []
    for e in emails:
        c = classifications.get(e.msg_id)
        if not c or not e.received_at:
            # Skip emails we couldn't classify or couldn't date — either is a data-quality issue
            # the UI would rather not see than show broken.
            log.warning("skipping %s: classification=%s received_at=%s", e.msg_id, c, e.received_at)
            continue
        rows.append({
            "msg_id": e.msg_id,
            "received_at": e.received_at,
            "classified_at": classified_at,
            "sender": e.sender,
            "subject": e.subject,
            "summary": c.summary,
            "category": c.category.value,
        })
    log.info("prepared %d rows for sync", len(rows))

    if dry_run:
        print(json.dumps({"classified_at": classified_at, "emails": rows}, indent=2))
        return 0

    worker_url = _require_env("PORTAL_GMAIL_WORKER_URL")
    sync_secret = _require_env("PORTAL_GMAIL_SYNC_SECRET")
    client = WorkerSyncClient(base_url=worker_url, secret=sync_secret)
    try:
        result = client.sync(classified_at=classified_at, emails=rows)
    except WorkerSyncError as e:
        log.error("sync to Worker failed: %s", e)
        return 1

    log.info("sync done: inserted=%d skipped_existing=%d", result.inserted, result.skipped)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.sync:
        return run_sync(dry_run=args.dry_run)
    print("usage: triage.py --sync [--dry-run]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
