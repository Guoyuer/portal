# Gmail Auto-Triage — Design v2 (2026-04-12)

> v2 pivot (2026-04-12): dropped digest email delivery. Portal is user's one-stop entry, so triage results surface in a new `/mail` tab instead of landing in Gmail inbox. Same GH Actions + app password + IMAP foundation. New: D1 storage + Portal React page. Out: SMTP send, HMAC signed links, `etl.email_report` reuse.

## Goal

A `/mail` tab in Portal showing the last 7 days of classified Gmail (IMPORTANT / NEUTRAL / TRASH_CANDIDATE). One-click delete on trash candidates calls back to the Worker, which trashes via IMAP. Classification runs once a day on GH Actions, results cached in D1 so the tab opens instantly.

## Non-goals

- Digest email delivery (removed — Portal UI replaces it).
- Reply drafting.
- Marking-as-read from Portal (use Gmail for that).
- Multi-user.
- Real-time inbox sync (daily cron + "classified N minutes ago" timestamp is enough).

## Architecture

```
                ┌──────────────────────────────────┐
                │  GitHub Actions (daily)          │
                │    cron: "0 22 * * *"  UTC       │
                │    runs: triage.py --sync        │
                └───────────────┬──────────────────┘
                                │
                                ▼
            ┌──────────────────────────────────┐
            │  Python: pipeline/scripts/gmail/ │
            │   1. IMAP fetch 24h unread       │
            │   2. Claude Haiku classify       │
            │   3. POST Worker /mail/sync      │
            │      with list of classifications│
            └───────────────┬──────────────────┘
                            │
                            ▼
            ┌──────────────────────────────────┐
            │  worker-gmail/  (Cloudflare)     │
            │                                  │
            │   POST /mail/sync   (SYNC_SECRET)│
            │     → INSERT OR REPLACE into D1  │
            │                                  │
            │   GET  /mail/list   (USER_KEY)   │
            │     → SELECT last 7d from D1     │
            │     → JSON response              │
            │                                  │
            │   POST /mail/trash  (USER_KEY)   │
            │     → IMAP trash the msg_id      │
            │     → UPDATE D1 status=trashed   │
            │                                  │
            │   D1: triaged_emails table       │
            └──────────────────────────────────┘
                            ▲
                            │
            ┌──────────────────────────────────┐
            │  Portal Next.js: /mail tab       │
            │    src/app/mail/page.tsx         │
            │    Loads on user visit           │
            │    Delete button → POST /trash   │
            └──────────────────────────────────┘
```

**Why this shape:**
- **Python on GH Actions** owns fetch + classify — avoids Worker's 10ms CPU cap (MIME parsing + Claude response parsing together risks exceeding).
- **Worker + D1** is the service layer: serves cached classifications to Portal instantly, handles trash on click.
- **Portal** is purely a UI — no business logic beyond rendering and delete confirmation.
- **One Gmail app password** still covers everything (Python IMAP read + Worker IMAP trash).
- **URL-key auth** for the Portal-facing endpoints — no SSO, no domain gymnastics. Worker validates a 32-char shared secret on every request.

## Data flows

### 1. Daily classification (GH Actions cron)

```
GH Actions cron "0 22 * * *" UTC = 07:00 +08
  → python pipeline/scripts/gmail/triage.py --sync
  ↓
imaplib.IMAP4_SSL + app password login
M.select('INBOX')
M.uid('SEARCH', 'UNSEEN', 'SINCE', '12-Apr-2026')
  → ~30 UIDs
  ↓
FETCH BODY[] for each → MIME parse → ParsedMessage list
  ↓
anthropic.messages.create(model=haiku-4-5)
  → {classifications: [{msg_id, category, summary}]}
  ↓
POST {WORKER_URL}/mail/sync   (Header: X-Sync-Secret: ...)
  Body: {
    classified_at: "2026-04-12T22:01:34Z",
    emails: [
      {msg_id, received_at, sender, subject, summary, category}
    ]
  }
  ↓
Worker:
  - Verify X-Sync-Secret
  - For each email: INSERT OR IGNORE into triaged_emails
    (status defaults to 'active'; existing rows not overwritten so user
     interactions like status=trashed survive a re-sync)
  - Return 200 {inserted: N, skipped_existing: M}
```

### 2. User visits Portal `/mail`

```
Browser → GET https://portal.example.com/mail
  (key read from localStorage or ?key= on first visit)
  ↓
Next.js static page loads
  → useEffect fetches GET {WORKER_URL}/mail/list?key=<USER_KEY>
  ↓
Worker:
  - Verify key == env.USER_KEY
  - SELECT msg_id, received_at, sender, subject, summary, category, status, classified_at
    FROM triaged_emails
    WHERE classified_at > datetime('now', '-7 days')
      AND status = 'active'
    ORDER BY received_at DESC
  - Return JSON
  ↓
Portal renders 3 sections (IMPORTANT / NEUTRAL / TRASH_CANDIDATE), shows:
  "as of <classified_at of newest row> · <count> active"
```

### 3. User clicks [Delete]

```
User clicks [Delete] on a TRASH_CANDIDATE row
  ↓
Portal: optimistically fade the row (client-side state)
  → POST {WORKER_URL}/mail/trash?key=<USER_KEY>
    Body: {msg_id: "..."}
  ↓
Worker:
  - Verify key
  - Open IMAP: connect imap.gmail.com:993 + TLS + LOGIN
  - UID SEARCH HEADER "Message-ID" <msg_id>  → uid
  - UID STORE uid +X-GM-LABELS "\\Trash"
  - UPDATE triaged_emails SET status='trashed' WHERE msg_id=?
  - Return 200 {status: 'trashed'}
On error:
  - IMAP not found:  200 {status: 'already_gone'} + set status='trashed' in D1
  - IMAP auth fail:  503 {error: 'auth_failed'}
  - IMAP timeout:    503 {error: 'timeout'}
  ↓
Portal:
  - Success → row stays removed
  - Error  → row un-fades, show inline error
```

## Components

### Python: `pipeline/scripts/gmail/`

```
gmail/
├── triage.py            # CLI: --sync / --dry-run
├── imap_client.py       # IMAP connect, search, fetch (read-only)
├── classify.py          # Anthropic call + prompt with few-shot
├── worker_sync.py       # httpx POST /mail/sync wrapper
├── tests/
│   ├── test_imap_client.py
│   ├── test_classify.py
│   └── test_worker_sync.py
├── requirements.txt     # anthropic, httpx
└── README.md
```

Removed from v1: `digest_html.py`, `worker_sign.py`. No SMTP send anywhere.

### Cloudflare Worker: `worker-gmail/`

```
worker-gmail/
├── src/
│   ├── index.ts         # routes + auth + IMAP — single file, <500 LoC
│   └── db.ts            # D1 query helpers (INSERT OR IGNORE, SELECT, UPDATE)
├── schema.sql           # D1 table definition
├── wrangler.jsonc       # D1 binding, nodejs_compat, secrets
├── package.json
└── tsconfig.json
```

**Routes:**

| Method | Path | Auth | Purpose |
|---|---|---|---|
| POST | `/mail/sync` | `X-Sync-Secret` header | Upsert classifications from Python |
| GET | `/mail/list?key=` | `USER_KEY` query param (or `X-Mail-Key` header) | Return last 7 days of active rows |
| POST | `/mail/trash?key=` | `USER_KEY` | IMAP-trash msg_id + mark status=trashed |

**D1 schema** (`worker-gmail/schema.sql`):

```sql
CREATE TABLE IF NOT EXISTS triaged_emails (
  msg_id        TEXT PRIMARY KEY,       -- Message-ID header incl. angle brackets
  received_at   TEXT NOT NULL,          -- ISO 8601, from email Date: header
  classified_at TEXT NOT NULL,          -- ISO 8601, when Python classified it
  sender        TEXT NOT NULL,
  subject       TEXT NOT NULL,
  summary       TEXT NOT NULL,          -- Claude one-sentence summary
  category      TEXT NOT NULL
                CHECK (category IN ('IMPORTANT','NEUTRAL','TRASH_CANDIDATE')),
  status        TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','trashed'))
);

CREATE INDEX IF NOT EXISTS idx_triaged_classified_at
  ON triaged_emails(classified_at DESC);
CREATE INDEX IF NOT EXISTS idx_triaged_category_status
  ON triaged_emails(category, status);
```

**Retention**: no automatic purge in v1. At ~30 rows/day × 30 days ≈ 900 rows. D1 free tier is 5 GB; this is nothing. Add a scheduled `DELETE WHERE classified_at < '-90 days'` if the table ever grows unwieldy.

**IMAP client**: reuse the hand-rolled minimal IMAP client from v1 design (LOGIN → SELECT → UID SEARCH HEADER → UID STORE +X-GM-LABELS `\Trash`). ~150 LoC.

### Portal frontend: `src/app/mail/`

```
src/app/mail/
└── page.tsx             # full page, client-side-fetched

src/components/mail/
├── mail-list.tsx        # 3 sections
├── mail-row.tsx         # single email row with actions
└── delete-button.tsx    # isolates POST /trash + optimistic state

src/lib/
├── schemas/mail.ts      # Zod: TriagedEmail, MailListResponse
└── use-mail.ts          # fetch hook, key resolution, delete mutator
```

**Key resolution logic** (`src/lib/use-mail.ts`):

```ts
function resolveKey(): string | null {
  // 1. If ?key= in URL → save to localStorage + strip from URL
  // 2. Else read from localStorage
  // 3. Else null → UI prompts user to paste key
}
```

**UI layout (ASCII wireframe):**

```
┌─────────────────────────────────────────────────┐
│  Portal    Finance   Econ   Mail                │
├─────────────────────────────────────────────────┤
│                                                 │
│  Mail — as of Apr 12 07:15 (3 hours ago)        │
│                                                 │
│  📌 IMPORTANT (3)                                │
│  ┌─────────────────────────────────────────┐    │
│  │ ▸ recruiter@talent.io                   │    │
│  │   Software Engineer role at Stripe      │    │
│  │   Stripe role, $300k+ mentioned         │    │
│  │                    [Open in Gmail]      │    │
│  └─────────────────────────────────────────┘    │
│  ┌─────────────────────────────────────────┐    │
│  │ ▸ billing@chase.com                     │    │
│  │   Your statement is ready               │    │
│  │   $1,247.33 due Apr 28                  │    │
│  │                    [Open in Gmail]      │    │
│  └─────────────────────────────────────────┘    │
│                                                 │
│  📨 OTHER (8)                                    │
│  • @slack.com  —  3 new DMs                     │
│  • @notion.so  —  Page shared by colleague      │
│  ...                                            │
│                                                 │
│  🗑️ SUGGESTED TRASH (12)                        │
│  ┌─────────────────────────────────────────┐    │
│  │ ☐ @linkedin.com                         │    │
│  │   People you may know                   │    │
│  │   LI connection spam                    │    │
│  │          [Delete]  [Open in Gmail]      │    │
│  └─────────────────────────────────────────┘    │
│  ...                                            │
└─────────────────────────────────────────────────┘
```

- Style matches existing finance tab cards (borrow CSS tokens from `src/app/globals.css`).
- Dark-mode aware if Portal already supports it.
- "Mail" nav link added to whichever component renders the top nav (likely `src/components/layout/*`).
- Mobile: sections stack, rows keep action buttons on the right.

**Optimistic delete behavior:** on click, row fades (`opacity: 0.3`) + button disabled. On success, row is removed from local state. On error, row re-appears with an inline error message below the actions.

**Refresh:** no polling. User refreshes the browser to re-fetch. Add a "Last classified: X ago" stamp so they know what's fresh.

## Auth strategy

Two secrets, distinct roles:

| Secret | Who holds it | Where used |
|---|---|---|
| `SYNC_SECRET` | GH Actions + Worker | `X-Sync-Secret` header on `/mail/sync` |
| `USER_KEY` | User's browser + Worker | `?key=<USER_KEY>` on `/mail/list` + `/mail/trash` |

`USER_KEY` is a 32-char random string (`openssl rand -hex 32`). User visits `/mail?key=<USER_KEY>` on first setup; Portal stores it in `localStorage` and strips from URL. Subsequent visits just work from any browser where the key is saved.

**Key rotation** (on leak): regenerate, update Worker secret, update localStorage on each device used (paste new URL). Outstanding trash calls from old key return 401.

**Threat model:** a leaked `USER_KEY` grants: read classifications + trash listed emails + see summaries. Does NOT grant: reading arbitrary Gmail, accessing non-trash scopes, reading other Portal tabs (finance/econ are on a separate worker without the key).

## Classification prompt

Unchanged from v1 — stored in `classify.py`. IMPORTANT / NEUTRAL / TRASH_CANDIDATE + few-shot:

```
IMPORTANT — recruiter outreach, bills with due dates, security alerts
            needing response, human email asking a direct question.
TRASH_CANDIDATE — promotional newsletters, routine system notifications,
                  duplicate marketing.
NEUTRAL — anything else. When in doubt, NEUTRAL.

Few-shot: 5 examples covering recruiter, bill, newsletter, security,
Slack ping. Tune via source edits.
```

Haiku 4.5, ~$0.001/day.

## Setup — one-time tasks

1. **Gmail app password** (2 min). Reuse the one `email_report` uses if it's already in `PORTAL_SMTP_PASSWORD`.
2. **Enable IMAP** in Gmail Settings.
3. **Generate secrets:**
   ```bash
   SYNC_SECRET=$(openssl rand -hex 32)
   USER_KEY=$(openssl rand -hex 32)
   ```
4. **Cloudflare Worker setup:**
   ```bash
   cd worker-gmail
   npx wrangler d1 create portal-gmail       # copy the database_id to wrangler.jsonc
   npx wrangler d1 execute portal-gmail --file=schema.sql --remote
   npx wrangler secret put SYNC_SECRET
   npx wrangler secret put USER_KEY
   npx wrangler secret put SMTP_USER
   npx wrangler secret put SMTP_PASSWORD
   npx wrangler deploy
   ```
5. **GitHub Secrets** — repo Settings → Secrets → Actions:
   - `PORTAL_SMTP_USER`, `PORTAL_SMTP_PASSWORD`, `ANTHROPIC_API_KEY`
   - `PORTAL_GMAIL_CRON_URL`, `PORTAL_GMAIL_SYNC_SECRET`
6. **GitHub Actions workflow** — create `.github/workflows/gmail-sync.yml` with daily cron `"0 22 * * *"`.
7. **Portal frontend** — deploy with new `/mail` route. No new env vars on the Pages side; the Worker URL is hardcoded or read from `process.env.NEXT_PUBLIC_GMAIL_WORKER_URL` at build time.
8. **First visit**: open `https://portal.example.com/mail?key=<USER_KEY>` once in each browser you use.

## Error handling

| Failure | Behavior |
|---|---|
| Python IMAP auth 535 | GH Actions job fails, workflow email notifies user |
| Python Anthropic 5xx | Retry 2×; fallback = emails classified NEUTRAL, `/mail/sync` still called so user sees *something* |
| POST `/mail/sync` 401 | Wrong `SYNC_SECRET` — GH Actions fails |
| Worker D1 write fails | 500 to Python; GH Actions retries next day |
| GET `/mail/list` 401 | Portal shows "Invalid key — paste a new one" |
| GET `/mail/list` 500 | Portal shows "Service unavailable, try again" |
| POST `/mail/trash` IMAP auth fail | 503; Portal shows row un-faded with "Gmail auth issue" inline |
| POST `/mail/trash` IMAP not-found | 200 `{status:"already_gone"}`; Portal removes row (same UX as trashed) |
| POST `/mail/trash` IMAP timeout | 503; Portal un-fades + "timeout, retry" |
| Classified email already in D1 with status=trashed, cron re-INSERT | `INSERT OR IGNORE` preserves the trashed status (no re-appearance) |
| User manually trashed in Gmail between syncs | Next trash click: IMAP returns not-found → UX same as already_gone, row removed |
| Portal offline (no network) | fetch fails; show "Can't reach server" banner |

## Security

| Threat | Mitigation |
|---|---|
| USER_KEY leaked (e.g. Portal URL screenshot posted) | Attacker gets read of classified summaries + ability to trash listed emails (30d reversible). No broader Gmail access. Rotate: regen key, update Worker + localStorage. |
| SYNC_SECRET leaked | Attacker could POST fake classifications into D1. Rotate same way. Worst case: temporarily poisoned D1 table — next cron overwrites with real data. |
| App password leaked | Same as v1: revoke in Google Account, regen, update Worker + GH Secrets. |
| Portal page prefetches `/mail/trash` link | Not applicable — delete is a button that POSTs, not a GET link. |
| Prompt injection via email body | Classification advisory-only. Wrong category ≠ auto-delete — user still clicks. |
| Accidental permanent delete | Only use `X-GM-LABELS \Trash`, never EXPUNGE outside Trash folder. |
| Cross-origin request from random site hitting `/mail/trash` | Worker checks `USER_KEY`; without it, 401. CORS allow-origin set to Portal's domain only. |
| SQL injection in `/mail/list` | Only parameter is `msg_id` for trash + internal fields — all via prepared statements (D1 bindings). No user free-text. |

## Testing

### Python

- `test_imap_client.py`: mock `imaplib.IMAP4_SSL`, assert SEARCH/FETCH sequence.
- `test_classify.py`: mock `anthropic`, assert category/summary parsing + fallback.
- `test_worker_sync.py`: mock `httpx`, assert POST body shape.

### Worker

- Manual integration with `wrangler dev` + `curl`:
  - `POST /mail/sync` with right/wrong secret
  - `GET /mail/list` with right/wrong key
  - `POST /mail/trash` against a test email
- D1 inspection: `wrangler d1 execute portal-gmail --command="SELECT * FROM triaged_emails LIMIT 5"`.

### Portal

- Component-level Vitest for `MailList` rendering (3 sections, empty-state, error-state).
- Zod schema round-trip test for `MailListResponse`.
- Playwright e2e: happy path (load page, click delete, row disappears).

## Open questions (decisions)

1. ✅ Auth: URL key (32-char random) stored in localStorage
2. ✅ Classification timing: daily GH Actions cron + D1 cache
3. ✅ UI shows all 3 categories (IMPORTANT / NEUTRAL / TRASH_CANDIDATE)
4. ✅ Retention: keep 30–90 days in D1, UI shows last 7 days
5. ✅ Delete UX: optimistic update, error un-fades row
6. Frontend styling: match existing Portal card patterns from finance tab. Details deferred to implementation.
7. Portal nav: add "Mail" link to whichever component renders the Finance/Econ links today. Implementation task resolves the exact file during execution.

## Out of scope for v1

- Digest email (removed — Portal is the interface now)
- Reply drafting
- Manual category override / "mark as important" feedback loop
- Bulk delete ("trash all TRASH_CANDIDATE")
- Refresh-now button (triggers immediate classify)
- Auto-unsubscribe
- Mobile app / push
- Multi-user
- Click-through analytics
- Search / filter within the list

## Success criteria

- `/mail` tab loads in <500 ms from D1 cache.
- Daily cron populates D1 within 5 min of 07:00 local.
- Click [Delete] → Gmail Trash within ~1 s, success rate > 95%.
- ≤ 1 false-positive / week (important email in TRASH_CANDIDATE).
- Monthly cost < $0.50 (Anthropic only; everything else free tier).
- Zero SMTP / email-sending code anywhere.
