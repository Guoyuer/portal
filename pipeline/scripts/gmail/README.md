# Gmail Triage

Daily Gmail classification script. Fetches 24h of unread emails, runs them
through Claude Haiku, and POSTs per-email categories + summaries to the
worker-gmail D1 via the `/mail/sync` endpoint.

Portal's `/mail` tab reads from that D1 via `/mail/list`.

## Run locally (dry-run)

```bash
cd pipeline
PORTAL_SMTP_USER=...@gmail.com PORTAL_SMTP_PASSWORD=... ANTHROPIC_API_KEY=sk-... \
  .venv/Scripts/python.exe scripts/gmail/triage.py --sync --dry-run
```

Prints the classified rows to stdout. No Worker call, no D1 write.

## Production

Runs on GitHub Actions daily. See `.github/workflows/gmail-sync.yml`.
