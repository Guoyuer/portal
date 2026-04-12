# Gmail Auto-Triage — Design (2026-04-12)

> Revised 2026-04-12: Gmail app password (IMAP + SMTP) reusing `etl.email_report`. Runtime: **GitHub Actions** (Gmail triage has no local-file dependency). Click → **instant trash** via Worker-side IMAP (no async queue).

## Goal

Every morning at 07:00 local, receive a digest email summarizing the last 24 hours of Gmail. Important emails highlighted. Low-value emails come with one-click delete links that trash immediately (~1s after confirm).

## Non-goals

- Auto-apply labels or archive (beyond trash).
- Reply drafting.
- Multi-user.
- Read-state tracking.

## Architecture

```
        ┌──────────────────────────────────┐
        │  GitHub Actions                  │
        │    cron: "0 22 * * *" (UTC)      │
        │    runs: triage.py --digest      │
        └───────────────┬──────────────────┘
                        │
                        ▼
      ┌──────────────────────────────────┐
      │  Python: pipeline/scripts/gmail/ │
      │   1. IMAP fetch 24h unread       │
      │   2. Claude Haiku classify       │
      │   3. POST Worker /sign × N       │
      │   4. Render HTML (<pre>-based)   │
      │   5. SMTP send to self           │
      │      (via etl.email_report)      │
      └──────────────────────────────────┘


      ┌──────────────────────────────────┐
      │  worker-gmail/                   │
      │   POST /sign    (internal HMAC)  │
      │   GET  /trash   (confirm page)   │
      │   POST /trash   (IMAP trash)     │
      │                                  │
      │   imap.gmail.com:993 (app pw)    │
      └──────────────────────────────────┘
              ▲
              │
    user clicks ☐ in digest
```

**Why this shape:**
- One Gmail **app password** covers everything: Python SMTP send + Python IMAP read + Worker IMAP trash. No OAuth.
- **No queue / no async**. Worker does IMAP directly on click. ~1s UX.
- **GH Actions** because Gmail triage touches zero local files; PC-must-be-on is not a cost worth paying (Task Scheduler is only justified when local file access is needed — see `PortalSync`).
- **Imports `etl.email_report`** (merged via PR #122). Zero duplicated send code.

## Data flow

### 1. Daily digest

```
GH Actions cron "0 22 * * *" UTC = 07:00 +08
  → python pipeline/scripts/gmail/triage.py --digest
  ↓
imaplib.IMAP4_SSL('imap.gmail.com', 993).login(user, app_password)
M.select('INBOX')
M.uid('SEARCH', 'UNSEEN', 'SINCE', '12-Apr-2026')
  → list of ~30 UIDs
  ↓
M.uid('FETCH', uid, 'BODY.PEEK[]')  per UID → MIME parse
  → {msg_id (Message-ID header), from, subject, body_excerpt}
  ↓
anthropic.messages.create(model="claude-haiku-4-5-20251001", ...)
  → {classifications: [{msg_id, category, summary}]}
  ↓
For each TRASH_CANDIDATE:
  POST {WORKER_URL}/sign  X-Signing-Secret header
    → {token: "<base64>"}
  ↓
Render HTML + text fallback (etl.changelog's <pre>-based pattern)
  ↓
etl.email_report.send(subject, html, text, config)
  → digest arrives seconds later
```

### 2. Click → instant trash

```
User clicks ☐ link → GET {WORKER_URL}/trash?t=<token>
  ↓
Worker:
  - Verify HMAC + expiry
  - Render confirm page: "Trash this email? [Confirm]"
  ↓
User clicks Confirm → POST /trash (same token in form)
  ↓
Worker:
  - Re-verify token
  - Connect imap.gmail.com:993, TLS, LOGIN
  - UID SEARCH HEADER "Message-ID" <msg_id>  → uid
  - UID STORE uid +X-GM-LABELS "\\Trash"      (Gmail-specific move to Trash)
  - UID STORE uid +FLAGS \\Deleted
  - LOGOUT (skip EXPUNGE — Gmail handles cleanup)
  - Render: "✓ Trashed. Recoverable for 30 days in Gmail Trash."

On error:
  - IMAP auth fail:  503 + log (operator re-generates app password)
  - UID not found:   200 "Already gone"   (e.g. trashed from phone)
  - IMAP timeout:    503 "Gmail unavailable, try again"
```

Wall-clock Confirm click → success page: ~500–800 ms (5 IMAP round-trips).

**GET → POST two-step**: defeats link-prefetching by email scanners. GET renders HTML only; nothing mutates until POST from human.

**Gmail IMAP quirk**: `\Deleted` alone does NOT trash in Gmail — only `X-GM-LABELS \Trash` moves it. Set both; skip EXPUNGE.

## Components

### Python: `pipeline/scripts/gmail/`

```
gmail/
├── triage.py            # CLI: --digest / --dry-run
├── imap_client.py       # IMAP connect, search, fetch (read-only)
├── classify.py          # Anthropic call + prompt with few-shot
├── digest_html.py       # <pre>-based HTML + plain-text twin
├── worker_sign.py       # httpx POST /sign wrapper
├── tests/
│   ├── test_imap_client.py     # mock imaplib
│   ├── test_classify.py        # mock anthropic
│   └── test_digest_html.py     # golden snapshots (0 / 1 / 30 emails)
├── requirements.txt     # anthropic, httpx
└── README.md
```

Python side does **no trash operations**. Read-only + send.

**Reuses** (from main post-PR #122):
- `etl.email_report.EmailConfig.from_env()`
- `etl.email_report.send()`

### Cloudflare Worker: `worker-gmail/`

```
worker-gmail/
├── src/
│   └── index.ts         # all routes + HMAC + IMAP — single file, <400 LoC
├── wrangler.jsonc       # nodejs_compat flag + secret bindings
├── package.json
└── tsconfig.json
```

Single file. No KV. No D1.

**Routes:**

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/sign` | `X-Signing-Secret` header | Return HMAC-signed token for msg_id |
| GET | `/trash?t=<token>` | HMAC | Render confirm page |
| POST | `/trash` | HMAC (form body) | IMAP-trash msg_id, render success |

**IMAP library approach:**

1. **First try `imapflow`** — modern, promise-based, maintained. Needs `nodejs_compat` in wrangler config. If it runs cleanly in Workers runtime → done.
2. **Fallback: hand-roll** — the 4 IMAP commands we need (LOGIN, SELECT, UID SEARCH HEADER, UID STORE) are ~150 LoC of text protocol over `connect()` TCP socket. Straightforward since we don't need a general IMAP client, just these specific commands.

Don't pre-commit to either; implementation pass picks whichever ships faster.

**CPU expectation:** IMAP response parsing < 10 ms on free tier. Network wait (TLS handshake + round trips ~500ms wall-clock) is NOT CPU time in Workers. Verify after first deploy; if over budget, switch to hand-rolled parser.

## Token format

```
payload    = msg_id + "|" + expiry_unix
signature  = HMAC_SHA256(SIGNING_KEY, payload)
token      = base64url(payload + "|" + signature)
```

- 7-day expiry.
- No key rotation scheme in v1 — if leaked, regenerate and invalidate all outstanding digest links (7-day blast radius).

## Classification prompt

Stored in `classify.py`:

```
System:
  You triage Gmail for <user>. Return STRICT JSON.

  Categories:
    IMPORTANT      — recruiter outreach (猎头), emails needing user action
                     (bills due, security needing response, invitations
                     with deadlines, human email asking a direct question).
    TRASH_CANDIDATE — promotional newsletters the user doesn't engage
                      with, routine system notifications (login-success,
                      "weekly summary"), duplicate marketing.
    NEUTRAL        — everything else. When in doubt, NEUTRAL.

  FEW-SHOT (edit in source; no config DSL):
    "Software Engineer role at Stripe — competitive comp"
      → IMPORTANT (recruiter)
    "Your statement is ready — Chase Freedom"
      → IMPORTANT (bill action)
    "Notion's weekly digest: 5 pages you haven't opened"
      → TRASH_CANDIDATE
    "Security alert: sign-in from Chrome on Windows"
      → TRASH_CANDIDATE (routine)
    "Slack: 2 new messages in #general"
      → NEUTRAL

User:
  Classify these N emails. Output:
    {"classifications": [{"msg_id": "...", "category": "...",
     "summary": "one-sentence what-this-is"}]}

  [email list here]
```

Haiku 4.5, ~$0.001/day at 30 emails. Tune via few-shot edits in source.

## Digest HTML

`<pre>`-wrapped block matching `etl.changelog.format_html`:

```html
<html>
<body style="font-family: -apple-system, Segoe UI, sans-serif; color: #222;">
  <h2 style="margin-bottom: 8px;">Gmail Triage — Mon Apr 12</h2>
  <pre style="font-family: Consolas, Menlo, monospace; font-size: 13px;
              background: #f6f8fa; padding: 14px 16px; border-radius: 6px;
              white-space: pre-wrap; line-height: 1.45;">
IMPORTANT (3)
──────────────
▸ recruiter@talent.io  Software Engineer role at Stripe
  Reaching out about senior role, mentions $300k+
  <a href="https://mail.google.com/mail/u/0/#inbox/{msg_id}">Open in Gmail</a>

▸ billing@chase.com  Your statement is ready
  $1,247.33 balance, due Apr 28
  <a href="...">Open in Gmail</a>

OTHER (8)
──────────
• @slack.com   — 3 new DMs
• @notion.so   — Page shared by colleague
...

SUGGESTED TRASH (12)
────────────────────
☐  @linkedin.com   People you may know         <a href="{worker}/trash?t=...">Delete</a>
☐  @spotify.com    Your monthly wrapped         <a href="{worker}/trash?t=...">Delete</a>
...
  </pre>
</body>
</html>
```

- `☐` = Unicode U+2610 (BALLOT BOX).
- `<a>` tags inside `<pre>` render as underlined links — no layout tricks needed.
- HTML-escape all email-sourced strings (same escape rules as `etl.changelog.format_html`).
- Text fallback = same block, links stripped.

## Env vars

Python-side → **GitHub Secrets**. Worker-side → `wrangler secret put`.

| Var | Storage | Used by |
|---|---|---|
| `PORTAL_SMTP_USER` | GH Secrets + Worker secret | Python (SMTP+IMAP login), Worker (IMAP login) |
| `PORTAL_SMTP_PASSWORD` | GH Secrets + Worker secret | Python, Worker |
| `PORTAL_GMAIL_WORKER_URL` | GH Secrets | Python |
| `PORTAL_GMAIL_WORKER_SECRET` | GH Secrets + Worker secret | `X-Signing-Secret` header |
| `PORTAL_GMAIL_TOKEN_SIGNING_KEY` | GH Secrets + Worker secret | HMAC (both sides) |
| `ANTHROPIC_API_KEY` | GH Secrets | Python |

The app password sits in two places (GH Secrets + Worker secret) — the cost of moving trash into the Worker. Rotating = update both, ~10s of ops.

## Setup — one-time tasks

1. **Gmail app password** (2 min). Google Account → Security → App passwords. Reuse existing if already set for `email_report`.

2. **Enable IMAP** in Gmail Settings → Forwarding and POP/IMAP.

3. **HMAC signing key** — `openssl rand -hex 32`.

4. **Cloudflare Worker**
   ```bash
   cd worker-gmail
   npx wrangler secret put PORTAL_SMTP_USER
   npx wrangler secret put PORTAL_SMTP_PASSWORD
   npx wrangler secret put PORTAL_GMAIL_WORKER_SECRET
   npx wrangler secret put PORTAL_GMAIL_TOKEN_SIGNING_KEY
   npx wrangler deploy
   ```

5. **GitHub Secrets** — repo Settings → Secrets → Actions. Add all 6 vars from the env table.

6. **GitHub Actions workflow** — create `.github/workflows/gmail-digest.yml`:

   ```yaml
   name: Gmail Triage Digest
   on:
     schedule:
       - cron: "0 22 * * *"     # 22:00 UTC = 07:00 +08 (no DST)
     workflow_dispatch: {}      # manual trigger for testing
   jobs:
     digest:
       runs-on: ubuntu-latest
       timeout-minutes: 5
       steps:
         - uses: actions/checkout@v4
         - uses: actions/setup-python@v5
           with: { python-version: "3.13" }
         - run: pip install -r pipeline/scripts/gmail/requirements.txt
         - run: python pipeline/scripts/gmail/triage.py --digest
           env:
             PORTAL_SMTP_USER:              ${{ secrets.PORTAL_SMTP_USER }}
             PORTAL_SMTP_PASSWORD:          ${{ secrets.PORTAL_SMTP_PASSWORD }}
             PORTAL_GMAIL_WORKER_URL:       ${{ secrets.PORTAL_GMAIL_WORKER_URL }}
             PORTAL_GMAIL_WORKER_SECRET:    ${{ secrets.PORTAL_GMAIL_WORKER_SECRET }}
             PORTAL_GMAIL_TOKEN_SIGNING_KEY: ${{ secrets.PORTAL_GMAIL_TOKEN_SIGNING_KEY }}
             ANTHROPIC_API_KEY:             ${{ secrets.ANTHROPIC_API_KEY }}
   ```

   Disable by deleting the workflow file (or via Actions UI).

7. **Smoke test** — `python pipeline/scripts/gmail/triage.py --digest --dry-run` locally prints HTML to stdout. Trigger workflow once via `workflow_dispatch` before relying on cron.

## Error handling

| Failure | Behavior |
|---|---|
| IMAP auth 535 (bad app password) | digest: exit 1, workflow fails (GH emails user). trash: 503 "Gmail auth failed" |
| IMAP SEARCH / FETCH fails | Retry 2× w/ backoff. Persistent: exit 1 (digest) or 503 (trash) |
| Anthropic timeout / 5xx | Retry 2×. Fallback: all emails classified NEUTRAL with "AI unavailable" banner in digest |
| Worker `/sign` unreachable during digest | Skip trash tokens; digest has "Open in Gmail" links only |
| `/trash` token HMAC invalid | 400 "Invalid or tampered link" |
| `/trash` token expired (>7d) | 410 "This link has expired" |
| `/trash`: UID not found (already trashed) | 200 "Already gone" |
| `/trash`: IMAP timeout | 503 "Gmail temporarily unavailable" — user retries |
| Worker CPU > 10ms free tier | Measure on first deploy. If over: drop `imapflow`, hand-roll minimal parser (skips robust error handling — acceptable for known-good Gmail endpoint) |

## Security

| Threat | Mitigation |
|---|---|
| Digest email forwarded / leaked | Attacker can trash those specific emails (30d reversible). App password ≠ account password; no broader compromise. |
| App password leaked | Revoke in Google → Security. Update both GH and Worker secrets. |
| Email scanner prefetches `/trash` link | GET = confirm page only. Mutation requires POST from human click. |
| `/sign` called by random internet | `X-Signing-Secret` header required. 401 otherwise. |
| HMAC key leaked | Regenerate both sides. Outstanding digest links become invalid (7d max blast radius). |
| Prompt injection via email body | Classification is advisory — misclassified important email just shows in wrong section, nothing auto-deletes. |
| Accidental permanent delete | `X-GM-LABELS \Trash` moves to Trash (30d reversible). We never EXPUNGE outside Trash. |

## Testing

### Python

- `test_imap_client.py`: mock `imaplib.IMAP4_SSL`, verify SEARCH/FETCH call sequence and argument shapes.
- `test_classify.py`: mock `anthropic`, assert fixtures map to expected categories.
- `test_digest_html.py`: golden HTML snapshots for 0 / 1 / 30-email digests. Include an email with special chars to verify escape.
- `test_worker_sign.py`: mock `httpx`, assert `X-Signing-Secret` header present.

### Worker

- HMAC sign/verify roundtrip + tampering + expiry tests.
- Integration: `wrangler dev` + `curl` for all 3 routes against a Gmail test account.

### End-to-end smoke

- `triage.py --digest --dry-run`: classify + render, print to stdout.

## Open questions (resolved)

1. ✅ Sync vs async delete: **sync (instant)**
2. ✅ Runtime: **GH Actions**
3. ✅ Auth: **Gmail app password (IMAP + SMTP)**
4. Log destination: GH Actions captures stdout in workflow logs — good enough, no separate log file.

## Out of scope for v1

- Auto-labels / archive
- Reply drafting
- "Unsubscribe for me"
- Bulk-delete link
- Mobile push
- Analytics / click-through tracking

## Success criteria

- Daily digest arrives within ~15 min of 07:00 local (allowing for GH Actions cron drift).
- ≤1 false-positive / week (important in TRASH_CANDIDATE).
- Click Confirm → trashed within ~1 s, success rate > 95%.
- Monthly cost < $0.50 (Anthropic only; everything else free tier).
- Zero duplicated SMTP-send logic with `etl.email_report`.
