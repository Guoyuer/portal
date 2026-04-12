# Gmail Auto-Triage — Design (2026-04-12)

> Revised 2026-04-12: switched from GH Actions + OAuth to Task Scheduler + Gmail app password (IMAP + SMTP), aligning with `etl.email_report` / `run_automation.py` patterns already in main.

## Goal

Every morning at 07:00 local, receive a digest email summarizing the last 24 hours of Gmail, with important emails highlighted and low-value emails listed with one-click delete links. Clicks queue deletions; a flush task runs every 15 min, so trash happens within ≤15 min.

## Non-goals

- Auto-applying labels or archiving.
- Reply drafting.
- Multi-user support.
- **Instant** delete — 15-min latency is the cost of zero-OAuth simplicity.
- Read-state tracking.

## Architecture

Three runtime pieces. **Single Gmail app password** for all Gmail I/O (no OAuth anywhere):

```
                ┌──────────────────────────────────┐
                │  Task Scheduler (user's PC)      │
                │                                  │
                │  Daily 07:00: triage.py --digest │
                │  Every 15 min: triage.py --flush │
                └───────────────┬──────────────────┘
                                │
                                ▼
              ┌──────────────────────────────────┐
              │  Python: pipeline/scripts/gmail/ │
              │                                  │
              │  --digest:                       │
              │    1. IMAP fetch 24h unread      │
              │    2. Claude Haiku classify      │
              │    3. POST Worker /sign × N      │
              │    4. Render HTML (pre-based)    │
              │    5. SMTP send to self          │
              │       (via etl.email_report)     │
              │                                  │
              │  --flush:                        │
              │    1. GET Worker /pending        │
              │    2. IMAP trash each msg_id     │
              │    3. DELETE Worker /pending/... │
              └───────────────┬──────────────────┘
                              │
                              ▼
              ┌──────────────────────────────────┐
              │  worker-gmail/  (Free tier)      │
              │                                  │
              │  POST   /sign      (internal)    │
              │  GET    /trash     (user click)  │
              │  POST   /trash     (confirm)     │
              │  GET    /pending   (Python poll) │
              │  DELETE /pending/… (Python ack)  │
              │                                  │
              │  Storage: KV  (TRASH_QUEUE)      │
              │  No Gmail API. No OAuth.         │
              └──────────────────────────────────┘
                              ▲
                              │
                    user clicks ☐ in digest
```

**Why this shape:**
- **App password** covers SMTP (send digest) + IMAP (read inbox, trash emails) with one credential. No OAuth consent, no refresh tokens, no "unverified app" warning.
- **Worker is a thin queue**, never calls Gmail. Cloudflare free tier easily handles this workload (<5 ms CPU/request).
- **Python owns all Gmail I/O**, runs on existing Task Scheduler alongside `PortalSync` — identical operational model.
- **Reuses `etl.email_report`** (merged to main via PR #122). Zero new send code.

## Data flow

### 1. Daily digest (07:00 local)

```
Task Scheduler → triage.py --digest
  ↓
imaplib.IMAP4_SSL('imap.gmail.com', 993).login(user, app_password)
  ↓
M.select('INBOX')
M.uid('SEARCH', 'UNSEEN', 'SINCE', '12-Apr-2026')
  → ~30 UIDs
  ↓
M.uid('FETCH', uid, 'BODY.PEEK[]')  per UID, parse MIME
  → list of {msg_id (Message-ID header), from, subject, body_excerpt}
  ↓
anthropic.messages.create(model="claude-haiku-4-5-20251001", ...)
  → {classifications: [{msg_id, category, summary}]}
  ↓
For each TRASH_CANDIDATE:
  POST {WORKER_URL}/sign
    Header: X-Signing-Secret: {WORKER_SECRET}
    Body:   {msg_id, expiry: now + 7d}
    → {token: "<base64>"}
  ↓
Render HTML (+ plain-text twin) — use etl.changelog's <pre>-based pattern
  ↓
etl.email_report.send(subject, html, text, config)
  → digest arrives in inbox within seconds
```

### 2. User click (asynchronous)

```
User opens digest in Gmail, clicks a ☐ link → opens /trash?t=<token>
  ↓
Worker:
  - Verify HMAC + expiry
  - Render confirm page: "Queue this email for trash? [Confirm]"
  ↓
User clicks Confirm → POST /trash with same token
  ↓
Worker:
  - Re-verify token
  - KV: TRASH_QUEUE.put(msg_id, {queued_at: now}, {expirationTtl: 7d})
  - Render: "✓ Queued. Will be trashed within ~15 min."
```

Note: GET renders confirm only (mutation-free), POST performs the queue. Defeats link-prefetch by email scanners.

### 3. Queue flush (every 15 min)

```
Task Scheduler → triage.py --flush
  ↓
GET {WORKER_URL}/pending  (Header: X-Poll-Secret)
  → {pending: [{msg_id, queued_at}, ...]}
  ↓
If empty: log "no pending", exit 0  (typical case — ~90% of runs)
  ↓
Otherwise:
  imaplib.IMAP4_SSL().login(user, app_password)
  M.select('INBOX')
  ↓
  For each msg_id in pending:
    uid = M.uid('SEARCH', 'HEADER', 'Message-ID', msg_id)
    if uid:
      M.uid('STORE', uid, '+X-GM-LABELS', '\\Trash')  # Gmail-specific: moves to Trash
      M.uid('STORE', uid, '+FLAGS', '\\Deleted')
      M.expunge()
      DELETE /pending/<msg_id> (acknowledge)
    else:
      # Already trashed elsewhere / moved — ack anyway to clear queue
      DELETE /pending/<msg_id>
```

**Gmail IMAP quirk**: setting `\Deleted` alone does NOT trash in Gmail — it only unlabels "INBOX". Use the Gmail-specific `X-GM-LABELS` extension to apply the `\Trash` label. Both together + expunge = behavior matching the Gmail UI "Trash" button.

## Components

### Python: `pipeline/scripts/gmail/`

```
gmail/
├── triage.py            # CLI entry: --digest / --flush / --dry-run
├── imap_client.py       # IMAP connect, search, fetch, trash
├── classify.py          # Anthropic call + prompt with few-shot
├── digest_html.py       # <pre>-based HTML renderer (matches etl.changelog)
├── worker_api.py        # httpx wrappers: /sign, /pending, /pending/:id
├── tests/
│   ├── test_imap_client.py    # mock imaplib
│   ├── test_classify.py       # mock anthropic
│   ├── test_digest_html.py    # golden snapshots (0 / 1 / 30 emails)
│   └── test_worker_api.py     # mock httpx
├── requirements.txt     # anthropic, httpx  (stdlib imaplib for IMAP)
└── README.md
```

**Dependencies**: only `anthropic` + `httpx`. No `google-*` libraries. IMAP via stdlib.

**Reuses from `etl/`** (already in main as of PR #122):
- `etl.email_report.EmailConfig.from_env()`
- `etl.email_report.send(subject, html, text, config)`

### Env vars (align with existing `PORTAL_*` family)

| Var | Source | Used by |
|---|---|---|
| `PORTAL_SMTP_USER` | reuse from email_report | Python (SMTP send + IMAP login) |
| `PORTAL_SMTP_PASSWORD` | reuse | Python |
| `PORTAL_GMAIL_TRIAGE_ENABLED` | new | Python opt-in guard |
| `PORTAL_GMAIL_WORKER_URL` | new | Python |
| `PORTAL_GMAIL_WORKER_SECRET` | new, shared | Python (X-Signing-Secret), Worker env (SIGNING_SECRET) |
| `PORTAL_GMAIL_POLL_SECRET` | new, shared | Python (X-Poll-Secret), Worker env (POLL_SECRET) |
| `PORTAL_GMAIL_TOKEN_SIGNING_KEY` | new, shared | Python + Worker (HMAC) |
| `ANTHROPIC_API_KEY` | new | Python |

Python uses the **same app password** as `email_report` for IMAP — Gmail app passwords work for IMAP too.

### Cloudflare Worker: `worker-gmail/`

```
worker-gmail/
├── src/
│   ├── index.ts         # all routes in one file (<250 LoC target)
│   └── token.ts         # HMAC sign/verify
├── wrangler.jsonc       # binds TRASH_QUEUE KV namespace + secrets
├── package.json
└── tsconfig.json
```

**No Gmail API. No OAuth. No D1.** KV-only storage.

Routes:

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/sign` | `X-Signing-Secret` header | Return HMAC-signed token for a msg_id |
| GET | `/trash?t=<token>` | HMAC token | Render confirm HTML page |
| POST | `/trash?t=<token>` | HMAC token (form body) | KV.put msg_id → success page |
| GET | `/pending` | `X-Poll-Secret` header | Return `{pending: [{msg_id, queued_at}]}` |
| DELETE | `/pending/:msg_id` | `X-Poll-Secret` header | KV.delete, 204 |

**KV schema**:
- Namespace: `TRASH_QUEUE`
- Key: `msg_id` (URL-encoded if special chars — Message-IDs shouldn't need it)
- Value: JSON `{queued_at: <unix_ms>}`
- TTL: 7 days (auto-expire — safety net if Python stops flushing)

**Free tier fit**: 100k reads/day + 1k writes/day. Our workload: ~100 reads/day (polls), ~20 writes/day (queue + ack). Zero concern.

### Reuse from main (post-PR#122)

| From `etl/email_report.py` | Usage |
|---|---|
| `EmailConfig.from_env()` | Direct import |
| `send()` | Direct import |

| Pattern from `etl/changelog.py` | Usage |
|---|---|
| `<pre>`-wrapped HTML with monospace inline CSS | Copy the pattern into `digest_html.py` |
| Plain-text primary, HTML wraps it | Same approach |
| Status color convention (green `#2e7d32` / red `#c62828`) | For header accents |

Post-merge consolidation TODO: once both features stabilize, consider lifting shared HTML scaffolding into a common `etl/email_html.py` helper. Not a blocker for v1 — a trivial follow-up PR.

## Token format

```
payload    = msg_id + "|" + expiry_unix + "|" + key_version
signature  = HMAC_SHA256(SIGNING_KEY_<version>, payload)
token      = base64url(payload + "|" + signature)
```

- `expiry`: 7 days after digest send.
- `key_version`: single byte prefix. Supports key rotation.
- Idempotent: queuing an already-queued msg_id is a KV overwrite — no user-visible difference.

## Classification prompt

Stored in `classify.py`. Structure:

```
System:
  You triage Gmail for <user>. You return STRICT JSON.

  Categories:
    IMPORTANT      — recruiter outreach (猎头), emails demanding user
                     action (bill due dates, security alerts needing
                     response, time-sensitive invitations, human email
                     asking a direct question).
    TRASH_CANDIDATE — promotional newsletters the user doesn't engage
                      with, routine system notifications (login-success,
                      "weekly summary"), duplicate marketing.
    NEUTRAL        — anything else. When in doubt, NEUTRAL.

  FEW-SHOT EXAMPLES (edit in source to tune — no config file):
    "Software Engineer role at Stripe — competitive comp"
      → IMPORTANT (recruiter)
    "Your statement is ready — Chase Freedom"
      → IMPORTANT (action: pay bill)
    "Notion's weekly digest: 5 pages you haven't opened"
      → TRASH_CANDIDATE (marketing, no action)
    "Security alert: sign-in from Chrome on Windows"
      → TRASH_CANDIDATE (routine, user's own device)
    "Slack: 2 new messages in #general"
      → NEUTRAL (user decides)

User (input):
  Classify the following N emails. Output:
    {"classifications": [
      {"msg_id": "...", "category": "IMPORTANT|NEUTRAL|TRASH_CANDIDATE",
       "summary": "one short sentence on what this email is"}
    ]}

  Emails:
    [msg_id: <uuid>] From: <...> | Subject: <...> | Body excerpt: <...>
    ...
```

Haiku 4.5 suffices (~$0.001/day at 30 emails). Tune via few-shot edits + redeploy — no config DSL.

## Digest HTML

Match `etl.changelog.format_html`'s pattern: one `<pre>` block with monospace CSS inside a minimal `<html>` wrapper. No tables, no Bootstrap, no pixel-perfect design.

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
• @slack.com  — 3 new DMs
• @notion.so  — Page shared
...

SUGGESTED TRASH (12)
────────────────────
☐  @linkedin.com   People you may know         <a href="{worker}/trash?t=...">Delete</a>
☐  @spotify.com    Your monthly wrapped        <a href="{worker}/trash?t=...">Delete</a>
...
  </pre>
</body>
</html>
```

- `☐` is U+2610 (BALLOT BOX) — a Unicode glyph, not a real checkbox.
- `[Open in Gmail]` uses the deep link `https://mail.google.com/mail/u/0/#inbox/<msg_id>`.
- `[Delete]` is the signed Worker link.
- `text/plain` fallback renders as the same text minus the `<a>` tags.

HTML-escape all email-sourced content (subjects, summaries, senders) before injecting into the template — same escape as `etl.changelog.format_html`.

## Setup — one-time tasks

**Vastly simpler than an OAuth setup** — no Google Cloud, no consent screen, no verification.

1. **Gmail app password** (2 min)
   - Google Account → Security → App passwords
   - Generate one named "Portal Gmail Triage". Copy the 16-char string.
   - (Or reuse the existing `PORTAL_SMTP_PASSWORD` if it's already the app password for this account. App passwords work for both SMTP and IMAP.)

2. **Enable IMAP** (1 min)
   - Gmail Settings → Forwarding and POP/IMAP → IMAP access: Enable

3. **HMAC signing key** — `openssl rand -hex 32`. Save to `.env` (Python side) and Worker secrets (via `wrangler secret put`).

4. **Worker secrets** (inside `worker-gmail/`)
   ```bash
   npx wrangler kv:namespace create TRASH_QUEUE
   npx wrangler secret put SIGNING_KEY       # HMAC key
   npx wrangler secret put SIGNING_SECRET    # for /sign
   npx wrangler secret put POLL_SECRET       # for /pending + /pending/:id
   npx wrangler deploy
   ```

5. **Env vars on PC** — add the 5 new `PORTAL_GMAIL_*` entries + `ANTHROPIC_API_KEY` to wherever `PORTAL_SMTP_*` lives (per `docs/automation-setup.md`).

6. **Task Scheduler**
   ```cmd
   :: Daily 07:00 — digest
   schtasks /create /tn "PortalGmailDigest" /sc daily /st 07:00 ^
     /tr "powershell.exe -NoProfile -File %USERPROFILE%\Projects\portal\pipeline\scripts\run_gmail_triage.ps1 --digest"

   :: Every 15 min — flush pending trash queue
   schtasks /create /tn "PortalGmailFlush" /sc minute /mo 15 ^
     /tr "powershell.exe -NoProfile -File %USERPROFILE%\Projects\portal\pipeline\scripts\run_gmail_triage.ps1 --flush"
   ```

7. **Dry-run smoke test** — `python pipeline/scripts/gmail/triage.py --digest --dry-run` prints HTML to stdout. Verify classifications + formatting before enabling scheduled tasks.

## Error handling

| Failure | Behavior |
|---|---|
| IMAP auth (535) — bad app password | Exit 1, log plainly. User regenerates app password, updates env. |
| IMAP SEARCH fails | Retry 3× w/ backoff. Persistent: exit 1. |
| Anthropic timeout/5xx | Retry 2×. Fallback: digest lists subjects as NEUTRAL with "AI unavailable" note (still useful). |
| Worker `/sign` unreachable during `--digest` | Skip trash tokens; digest shows "Open in Gmail" links only. |
| Worker `/pending` unreachable during `--flush` | Exit 0, no-op. Retries next cycle. |
| IMAP trash fails for one msg_id (in `--flush`) | Leave in queue, continue. KV TTL auto-expires after 7d. |
| `/trash` token HMAC invalid | 400 "Invalid or tampered link" |
| `/trash` token expired | 410 "This link is older than 7 days" |
| msg_id already trashed when `--flush` runs (user trashed manually) | IMAP SEARCH returns empty UID. Ack anyway (DELETE /pending/:id) to clear queue. |
| Concurrent `--digest` runs (overlap of scheduler) | Lock file in `%TEMP%/portal_gmail_digest.lock`; second run no-ops. |

## Security

| Threat | Mitigation |
|---|---|
| Digest email forwarded/leaked | Attacker can trash those specific emails (reversible in Gmail Trash for 30d). App password is isolated from Google account password. |
| App password leaked | Revoke in Google Account → Security. Generate new. Update env. |
| Email scanner prefetches `/trash` link | GET renders confirm page only. Actual queue add requires POST from human click. |
| `/sign` / `/pending` / `DELETE /pending` called by random caller | Require `X-Signing-Secret` / `X-Poll-Secret` header. 401 otherwise. |
| HMAC signing key leaked | Rotate: increment key_version. Outstanding tokens remain valid until 7d expiry. |
| Prompt injection via email body | Classification advisory-only. Worst case: misclassification. User ignores, moves on. |
| Accidental permanent delete | IMAP `X-GM-LABELS \Trash` moves to Trash (30d recoverable). Never EXPUNGE outside Trash. |
| Python fails partway through `--flush` (crash after IMAP trash, before DELETE ack) | Next cycle: IMAP SEARCH finds no UID, acks anyway. Idempotent. |

## Testing

### Python

- `test_imap_client.py`: `imaplib.IMAP4_SSL` mocked; verify SEARCH, FETCH, STORE, EXPUNGE call sequence.
- `test_classify.py`: `anthropic.Anthropic` mocked; assert each fixture email maps to expected category.
- `test_digest_html.py`: golden HTML snapshot tests for 0 / 1 / 30 email digests. Include a fixture with special chars to verify escaping.
- `test_worker_api.py`: `httpx` mocked; assert each Worker call has correct headers + body.

### Worker

- `token.test.ts`: HMAC sign → verify roundtrip, tampering detection, expiry boundary.
- Local integration: `wrangler dev` + `curl` for all 5 routes; assert KV state transitions.

### End-to-end smoke

- `triage.py --digest --dry-run`: classify + render, print HTML to stdout, skip send.
- `triage.py --flush --dry-run`: fetch pending, skip IMAP trash, print what-would-happen.

## Open questions (resolved)

1. **Flush cadence**: every 15 min. ✅ (user confirmed, free-tier compatible)
2. **Opt-in guard**: `PORTAL_GMAIL_TRIAGE_ENABLED=1` required. Default off.
3. **`email_report` post-merge refactor** — treat as stable import; open follow-up PR later if interfaces drift.
4. **Log file**: follow `run_automation.py`'s pattern (stdout captured by Task Scheduler + optional log file env).

## Out of scope for v1

- Auto-applying labels / auto-archive
- Reply drafting
- "Unsubscribe for me"
- Bulk-delete link ("Trash all TRASH_CANDIDATES" — one-click for the whole section)
- Mobile app / push notification
- Multi-language UI (digest naturally bilingual if inbox is mixed zh/en)
- Click-through analytics
- Retroactive triage of old emails

## Success criteria

- Daily digest arrives at 07:00 local without intervention for 2+ weeks.
- ≤1 false-positive per week (important email classified TRASH_CANDIDATE).
- Click ☐ → actual trash within 15 min, success rate > 95%.
- Monthly cost < $0.50 (Anthropic API; everything else free tier).
- Shared code (`email_report`, `changelog` pattern): zero duplication of SMTP send logic.
