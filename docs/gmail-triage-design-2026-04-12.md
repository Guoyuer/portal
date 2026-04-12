# Gmail Auto-Triage — Design (2026-04-12)

## Goal

Every morning at 7am local time, receive a digest email summarizing the last 24 hours of Gmail, with important emails highlighted and low-value emails listed with one-click delete links.

## Non-goals

- Auto-applying labels or archiving (Gmail MCP is read+draft only; deletion is the only mutation we perform, via direct OAuth — labeling stays out of scope to keep the blast radius narrow).
- Reply drafting.
- Multi-user support.
- Reading state tracking ("which emails did I open") — we trust the user to click through.

## Top-level architecture

Three runtime components:

```
            ┌──────────────────────────────┐
 7am local  │  GitHub Actions workflow     │
 ──cron────▶│  .github/workflows/          │
            │    gmail-triage.yml          │
            └──────────────┬───────────────┘
                           │
                           ▼
            ┌──────────────────────────────┐
            │  Python triage script        │
            │  scripts/gmail/triage.py     │
            │                              │
            │  1. Gmail list + batch_get   │
            │  2. Claude classify (Haiku)  │
            │  3. Request trash-tokens     │──┐
            │  4. Render HTML digest       │  │
            │  5. Gmail send to self       │  │
            └──────────────────────────────┘  │
                                              │
                   ┌──────────────────────────┘
                   ▼
            ┌──────────────────────────────┐
            │  worker-gmail/               │◀────── user clicks ☐
            │  Cloudflare Worker           │        in digest email
            │                              │
            │  POST /sign  (internal)      │
            │  GET  /trash (confirm page)  │
            │  POST /trash (commit delete) │
            └──────────────────────────────┘
                           │
                           ▼
                     Gmail API: messages.trash()
```

**Why split GH Actions + Worker**: Cloudflare Workers free tier caps CPU at 10ms/request. The digest generation (30 emails × HMAC signing + Anthropic response parsing + HTML render) would exceed this. GH Actions runs the heavy work (free: 2000 min/mo private repo, we use <1 min/day). Worker only handles user-facing click traffic, which is trivial CPU.

## Data flow

### 1. Digest generation (daily cron)

```
GH Actions cron fires (22:00 UTC = 7am local)
  ↓
Python refreshes Gmail access token using stored refresh_token
  ↓
GET /gmail/v1/users/me/messages?q=(newer_than:1d in:inbox is:unread)
  → list of message IDs (~30 for this user)
  ↓
POST /batch/gmail/v1  (batch get message bodies, format=full)
  → full email contents
  ↓
POST https://api.anthropic.com/v1/messages
  model=claude-haiku-4-5-20251001
  → { classifications: [{msg_id, category, summary}, ...] }
  where category ∈ {IMPORTANT, NEUTRAL, TRASH_CANDIDATE}
  ↓
For each TRASH_CANDIDATE:
  POST https://worker-gmail.<account>.workers.dev/sign
    Header: X-Signing-Secret: <shared>
    Body: { msg_id, expiry: now + 7d }
    → { token: "base64(...)" }
  ↓
Python renders HTML digest (see "Digest HTML" section)
  ↓
POST /gmail/v1/users/me/messages/send
  From: me  To: me  Subject: "📬 Gmail Triage — Apr 12"
  → digest arrives in inbox seconds later
```

### 2. Delete click (user-initiated)

```
User sees digest in Gmail, clicks ☐ next to a TRASH_CANDIDATE
  ↓
Browser: GET https://worker-gmail.<account>.workers.dev/trash?t=<token>
  ↓
Worker:
  - Verify HMAC signature
  - Verify expiry
  - Render confirm page: "Trash this email? [Confirm]"
  ↓
User clicks "Confirm" → POST /trash  (same token in form)
  ↓
Worker:
  - Re-verify token
  - Refresh Gmail access token (using stored refresh_token)
  - POST /gmail/v1/users/me/messages/{msg_id}/trash
  - Return: "✅ Moved to Trash. Restore within 30 days via Gmail."
  ↓
On Gmail error 404 (already trashed):
  - Return: "Already gone."
On Gmail error 5xx:
  - Return: "Gmail unavailable, try again in a minute."
```

**Why two-step (GET confirm → POST commit)**: Any upstream email scanner (corporate gateway, iOS Mail preview, archive.org) that prefetches links would otherwise silently delete emails on GET. The confirm page forces a human action, and the POST is never prefetched.

## Components

### GitHub Actions workflow

`.github/workflows/gmail-triage.yml`

```yaml
name: Gmail Triage Digest
on:
  schedule:
    - cron: "0 22 * * *"     # 22:00 UTC = 07:00 +07 (adjust for DST)
  workflow_dispatch: {}      # manual trigger for testing
jobs:
  triage:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.13" }
      - run: pip install -r scripts/gmail/requirements.txt
      - run: python scripts/gmail/triage.py
        env:
          GMAIL_CLIENT_ID:      ${{ secrets.GMAIL_CLIENT_ID }}
          GMAIL_CLIENT_SECRET:  ${{ secrets.GMAIL_CLIENT_SECRET }}
          GMAIL_REFRESH_TOKEN:  ${{ secrets.GMAIL_REFRESH_TOKEN }}
          ANTHROPIC_API_KEY:    ${{ secrets.ANTHROPIC_API_KEY }}
          WORKER_SIGN_URL:      ${{ secrets.WORKER_SIGN_URL }}
          WORKER_SIGN_SECRET:   ${{ secrets.WORKER_SIGN_SECRET }}
```

### Python triage script

```
scripts/gmail/
├── triage.py              # entry point, orchestrates fetch→classify→sign→render→send
├── oauth.py               # Gmail OAuth refresh (using refresh_token)
├── fetch.py               # list + batch_get messages
├── classify.py            # Anthropic call, prompt with few-shot
├── sign.py                # HTTP POST to Worker /sign
├── digest.py              # HTML template rendering
├── send.py                # Gmail API send
├── obtain_refresh_token.py  # one-shot utility for initial OAuth dance
├── tests/
│   ├── test_classify.py
│   ├── test_digest.py
│   └── fixtures/
├── requirements.txt
└── README.md              # setup + run instructions
```

**Boundaries:** each module has one purpose, `<150` LoC. `triage.py` is pure orchestration (no business logic). Each module has a unit test file with network calls mocked.

**Dependencies**: `google-auth`, `google-api-python-client`, `anthropic`, `httpx`. No heavy frameworks.

### Cloudflare Worker

```
worker-gmail/
├── src/
│   ├── index.ts         # fetch handler (routes /sign, /trash)
│   ├── token.ts         # HMAC sign + verify
│   ├── gmail.ts         # Gmail API client (OAuth refresh + trash call)
│   └── html.ts          # confirm/success/error page templates
├── tests/
│   └── token.test.ts    # HMAC roundtrip
├── package.json
├── tsconfig.json
└── wrangler.jsonc
```

Single-file is also fine — if total `< 300` LoC, keep in `index.ts`; split only when each concern exceeds `~100` LoC.

Runtime env vars (via `wrangler secret put`):
- `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` — for Gmail API trash calls
- `SIGNING_KEY` — HMAC key (shared with GH Actions)
- `SIGNING_SECRET` — header secret for internal `/sign` endpoint

**No D1 / KV needed.** Stateless — tokens carry their own expiry; Gmail trash is idempotent.

## Token format

Goal: each digest link is a self-contained, tamper-proof, expiring credential to trash one specific message.

```
payload    = msg_id + "|" + expiry_unix_ts + "|" + key_version
signature  = HMAC_SHA256(SIGNING_KEY_<version>, payload)
token      = base64url(payload + "|" + signature)
```

- `expiry`: 7 days after digest send — long enough for lazy weekend cleanup, short enough to bound exposure.
- `key_version`: single byte prefix on the key. Lets us rotate by adding a new key version without invalidating outstanding digests.
- **Stateless** — no replay protection beyond idempotent Gmail trash. Rationale: stolen digest email has already exposed the email subjects; letting an attacker trash those same emails is lower severity than the read access they already stole. Not worth the KV round-trip on every click.

## Classification prompt

Stored in `scripts/gmail/classify.py`. Structure:

```
System:
  You triage Gmail for <user>. You return STRICT JSON.
  
  Categories:
    IMPORTANT — recruiter outreach (猎头), emails demanding user action
                (bills with due dates, account security alerts requiring
                response, time-sensitive invitations, work emails from
                humans asking a direct question).
    TRASH_CANDIDATE — promotional newsletters the user doesn't engage
                      with, routine system notifications (login from
                      known device, "your weekly summary"), duplicate
                      marketing from services already subscribed.
    NEUTRAL — anything else. When in doubt, NEUTRAL.
  
  FEW-SHOT EXAMPLES (edit this list as you correct misclassifications):
    "Software Engineer role at Stripe — competitive comp"
      → IMPORTANT (recruiter)
    "Your statement is ready — Chase Freedom"
      → IMPORTANT (action: pay bill)
    "Notion's weekly digest: 5 pages you haven't opened"
      → TRASH_CANDIDATE (marketing, no action)
    "Security alert: sign-in from Chrome on Windows"
      → TRASH_CANDIDATE (routine, user's own device)
    "Slack: 2 new messages in #general"
      → NEUTRAL (might be relevant, user decides)

User (input):
  Classify the following 30 emails. Output JSON:
    {"classifications": [
      {"msg_id": "...", "category": "IMPORTANT|NEUTRAL|TRASH_CANDIDATE",
       "summary": "one short sentence on what this email is"}
    ]}
  
  Emails:
    [msg_id: 192abc] From: recruiter@company.io | Subject: ... | Body excerpt: ...
    [msg_id: 192def] From: ...
    ...
```

Few-shot examples are the primary tuning lever. When misclassified, edit the list and redeploy — no config file, no DSL.

Haiku 4.5 is sufficient for this task; cost is negligible (<$0.01/day at 30 emails).

## Digest HTML

Constraints: Gmail strips `<script>`, `<form>` inputs with submit buttons survive but look inconsistent across clients. Use simple tables + inline CSS. All "interactive" elements are `<a>` links.

Layout:

```
📬 Gmail Triage — Monday, Apr 12, 2026

IMPORTANT (3)
─────────────
▸ recruiter@talent.io  |  Software Engineer role at Stripe
  Reaching out about a senior role, mentions comp range $300k+
  [Open in Gmail]

▸ billing@chase.com  |  Your statement is ready
  $1,247.33 balance, due Apr 28
  [Open in Gmail]

▸ ...

OTHER (8)
─────────
• @slack.com   — 3 new DMs in #general
• @notion.so   — Page shared by colleague
• ...

SUGGESTED TRASH (12)
────────────────────
☐  @linkedin.com   — People you may know: 5 new suggestions   [Delete]
☐  @spotify.com    — Your monthly wrapped is ready            [Delete]
☐  @marketing.brand.com  — 40% off this weekend only          [Delete]
...
```

- `[Open in Gmail]` is a deep link: `https://mail.google.com/mail/u/0/#inbox/<msg_id>`.
- `[Delete]` is a signed Worker link: `https://worker-gmail.<account>.workers.dev/trash?t=<token>`.
- `☐` is a Unicode character (U+2610), not a real checkbox.

Render as a plain HTML email (MIME `text/html`). Include `text/plain` fallback with the same content sans links.

## Setup — one-time tasks

1. **Google Cloud project**
   - Create project, enable Gmail API
   - OAuth 2.0 client (type: "Desktop app" — easiest for obtain_refresh_token.py flow)
   - Consent screen:
     - User type: External
     - Scopes: `gmail.readonly`, `gmail.send`, `gmail.modify`
     - Publishing status: **Publish to production** (unverified). Click past the "unverified app" warning on first consent.
   - **Do not leave as "Testing"** — refresh token would expire after 7 days.

2. **Obtain refresh token**
   - `python scripts/gmail/obtain_refresh_token.py`
   - Opens browser, runs OAuth consent, prints refresh token to stdout
   - Save output — this is what goes into GH Secrets + Worker secret

3. **HMAC signing key**
   - `openssl rand -hex 32` → save as `SIGNING_KEY_v1`

4. **Cloudflare Worker**
   - `cd worker-gmail && npx wrangler deploy`
   - `npx wrangler secret put GMAIL_CLIENT_ID` (repeat for all 5 secrets)

5. **GitHub Secrets** (repo settings → secrets)
   - `GMAIL_CLIENT_ID`, `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN`
   - `ANTHROPIC_API_KEY`
   - `WORKER_SIGN_URL`, `WORKER_SIGN_SECRET`

6. **Dry run** — trigger workflow via `workflow_dispatch` before enabling cron. Verify digest arrives, trash links work.

## Error handling

Each failure mode has a defined behavior. No silent failures.

| Failure | Behavior |
|---|---|
| Gmail list/fetch 5xx | Retry 3× with exp backoff. On persistent failure: GH Actions fails → email notification from GitHub |
| Anthropic API timeout/5xx | Retry 2×. Then fallback: send digest with all emails as NEUTRAL + note "AI unavailable" |
| Worker /sign unreachable during GH Actions | Continue without trash tokens. TRASH_CANDIDATE emails appear with only "Open in Gmail" link, no ☐ |
| OAuth refresh token invalid | GH Actions exit 1. Notification. User re-runs `obtain_refresh_token.py` and updates secret |
| `/trash` token HMAC invalid | 400 "Invalid or tampered link" |
| `/trash` token expired | 410 "This link has expired (older than 7 days)" |
| Gmail trash returns 404 (msg already trashed/deleted) | 200 "Already gone" — friendly, not an error |
| Gmail trash returns 5xx | 503 "Gmail temporarily unavailable, try again in a minute" — user retries |
| Worker cold start exceeds 10ms CPU | N/A — trash handler is <5ms estimated; /sign called only during GH Actions; if this ever bites, GH Actions would have to sign locally instead |

## Security

| Threat | Mitigation |
|---|---|
| Someone finds/forwards my digest email | They get read-only view of my subjects (already leaked) + ability to trash THOSE specific emails (recoverable from Gmail Trash for 30d). Not attacker-useful enough to warrant per-click auth. |
| Email scanner prefetches `/trash` link | GET renders confirm page. Actual trash requires POST from a human click. |
| Worker `/sign` called by random caller | `X-Signing-Secret` header must match env secret. Returns 401 otherwise. |
| OAuth refresh token leaked | Revoke via Google Account settings → Third-party access. Run obtain_refresh_token.py again. Update secrets. |
| HMAC signing key leaked | Rotate: add `SIGNING_KEY_v2`, increment key_version in new tokens. Old-version tokens keep working until expiry (7d). |
| Prompt injection via email content (malicious email tries to make Claude misclassify) | Classification is advisory only — no auto-action. Worst case: an important email gets put in TRASH_CANDIDATE. User notices wrong category, ignores the link, it doesn't auto-delete. |
| Accidentally permanent-delete | Only use `messages.trash` (reversible for 30d). Never `messages.delete`. |

## Testing

### Python triage script

- `test_classify.py`: mock Anthropic client, feed synthetic email fixtures, assert categories
- `test_digest.py`: golden HTML snapshot tests for digest rendering with 0 / 1 / 30 emails
- `test_sign.py`: mock Worker endpoint, assert HTTP round-trip
- `test_oauth.py`: mock token refresh, assert 401 triggers re-auth

### Worker

- `token.test.ts`: HMAC sign → verify roundtrip, tampering detection, expiry check
- Manual integration: `wrangler dev` + curl GET + POST /trash with a real test message

### End-to-end

- `python scripts/gmail/triage.py --dry-run`: prints digest HTML to stdout, skips send
- `--dry-run --limit 5`: process only 5 emails (faster iteration)

## Open questions (decide before implementation)

1. **Timezone handling** — cron is UTC-based. User's local is presumably +07 or +08 (China). Pick one (e.g., `0 22 * * *` = 07:00 +08 winter) and document; skip DST (China doesn't do DST; if user travels, they still get digest at a predictable UTC time).
2. **"Delete all" bulk link** — add to v1 or defer to v2? Recommendation: defer. Watch usage for a week; if user clicks every single trash link anyway, add bulk in v2.
3. **Cost monitoring** — Anthropic API is ~$0.01/day. Add a note in digest footer with token count for observability? Skip for v1.
4. **"I read this already" feedback loop** — none in v1. If false-negative (important email marked TRASH_CANDIDATE), user just ignores link. If false-positive (trash email marked IMPORTANT), user ignores. Few-shot tuning is manual.

## Out of scope for v1

- Auto-applying labels / archiving
- Reply drafting  
- "Unsubscribe for me" automation
- Bulk-delete link
- Mobile app / push notification
- Multi-language UI (digest is bilingual by fact that the source emails are mixed Chinese/English)
- Analytics on click-through rate
- Retroactive triage of old emails

## Success criteria

- Daily digest arrives at 7am local without manual intervention for 2+ weeks
- ≤1 false-positive per week (important email in TRASH_CANDIDATE)
- User clicks trash link without it failing > 95% of the time
- Total monthly cost < $0.50 (Anthropic API only; everything else free tier)
