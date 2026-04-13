# Gmail Auto-Triage v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the v2 Gmail auto-triage system designed in `docs/gmail-triage-design-2026-04-12.md`. A GH Actions cron runs a Python script that fetches unread Gmail (IMAP), classifies via Claude Haiku, and POSTs results to a Cloudflare Worker. The Worker caches classifications in D1 and serves them to a new `/mail` tab in the Portal Next.js app. Delete button → Worker IMAP trash + D1 status update.

**Architecture:** 4 components — (1) Python classifier on GH Actions, (2) Cloudflare Worker with D1, (3) Portal Next.js `/mail` page, (4) GH Actions workflow. Auth: `SYNC_SECRET` header for GH → Worker, `USER_KEY` query param for Portal → Worker.

**Tech Stack:** Python 3.13 + stdlib `imaplib` + `anthropic` + `httpx` · TypeScript Worker + `cloudflare:sockets` + D1 · Next.js 16 + React + Zod · GitHub Actions cron.

**File structure** (all new):

```
worker-gmail/
├── src/
│   ├── index.ts           # routes + auth + IMAP — <500 LoC single file
│   └── db.ts              # D1 helpers
├── schema.sql
├── wrangler.jsonc
├── package.json
└── tsconfig.json

pipeline/scripts/gmail/
├── __init__.py
├── triage.py              # CLI: --sync / --dry-run
├── imap_client.py
├── classify.py
├── worker_sync.py
├── requirements.txt
└── README.md

pipeline/tests/unit/gmail/
├── __init__.py
├── conftest.py
├── test_imap_client.py
├── test_classify.py
└── test_worker_sync.py

src/app/mail/
└── page.tsx

src/components/mail/
├── mail-list.tsx
├── mail-row.tsx
└── delete-button.tsx

src/lib/schemas/mail.ts
src/lib/use-mail.ts

.github/workflows/
└── gmail-sync.yml
```

---

## Task 1: Worker — scaffold + D1 binding

**Files:**
- Create: `worker-gmail/package.json`, `worker-gmail/tsconfig.json`, `worker-gmail/wrangler.jsonc`, `worker-gmail/src/index.ts`

- [ ] **Step 1: Create `worker-gmail/package.json`**

```json
{
  "name": "worker-gmail",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "wrangler dev --local",
    "deploy": "wrangler deploy"
  },
  "devDependencies": {
    "@cloudflare/workers-types": "^4.20240909.0",
    "typescript": "^5.5.0",
    "wrangler": "^3.78.0"
  }
}
```

- [ ] **Step 2: Create `worker-gmail/tsconfig.json`**

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022"],
    "module": "ES2022",
    "moduleResolution": "Bundler",
    "types": ["@cloudflare/workers-types"],
    "strict": true,
    "noImplicitAny": true,
    "noEmit": true,
    "isolatedModules": true,
    "esModuleInterop": true,
    "skipLibCheck": true
  },
  "include": ["src/**/*.ts"]
}
```

- [ ] **Step 3: Create `worker-gmail/wrangler.jsonc`**

```jsonc
{
  "$schema": "node_modules/wrangler/config-schema.json",
  "name": "worker-gmail",
  "main": "src/index.ts",
  "compatibility_date": "2024-09-01",
  "compatibility_flags": ["nodejs_compat"],
  "d1_databases": [
    {
      "binding": "DB",
      "database_name": "portal-gmail",
      "database_id": "REPLACE_WITH_REAL_ID_AFTER_CREATE"
    }
  ]
}
```

(`database_id` is filled in during the deploy step — `wrangler d1 create portal-gmail` prints it.)

- [ ] **Step 4: Create `worker-gmail/src/index.ts` skeleton**

```ts
interface Env {
  DB: D1Database;
  SYNC_SECRET: string;
  USER_KEY: string;
  SMTP_USER: string;
  SMTP_PASSWORD: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (url.pathname === "/mail/sync" && request.method === "POST") {
      return new Response("not implemented", { status: 501 });
    }
    if (url.pathname === "/mail/list" && request.method === "GET") {
      return new Response("not implemented", { status: 501 });
    }
    if (url.pathname === "/mail/trash" && request.method === "POST") {
      return new Response("not implemented", { status: 501 });
    }
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
```

- [ ] **Step 5: Install + typecheck**

```bash
cd worker-gmail
npm install
npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 6: Commit**

```bash
git add worker-gmail/
git commit -m "feat(gmail-triage): scaffold worker-gmail with D1 binding and 3 route stubs"
```

---

## Task 2: Worker — D1 schema

**Files:**
- Create: `worker-gmail/schema.sql`

- [ ] **Step 1: Create `worker-gmail/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS triaged_emails (
  msg_id        TEXT PRIMARY KEY,
  received_at   TEXT NOT NULL,
  classified_at TEXT NOT NULL,
  sender        TEXT NOT NULL,
  subject       TEXT NOT NULL,
  summary       TEXT NOT NULL,
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

- [ ] **Step 2: Commit**

```bash
git add worker-gmail/schema.sql
git commit -m "feat(gmail-triage): D1 schema — triaged_emails table"
```

---

## Task 3: Worker — POST /mail/sync

**Files:**
- Create: `worker-gmail/src/db.ts`
- Modify: `worker-gmail/src/index.ts`

- [ ] **Step 1: Create `worker-gmail/src/types.ts` first (db.ts imports from it)**

```ts
export type Category = "IMPORTANT" | "NEUTRAL" | "TRASH_CANDIDATE";

export interface UpsertInput {
  msg_id: string;
  received_at: string;
  classified_at: string;
  sender: string;
  subject: string;
  summary: string;
  category: Category;
}

export interface TriagedEmail extends UpsertInput {
  status: "active" | "trashed";
}
```

- [ ] **Step 2: Create `worker-gmail/src/db.ts`**

```ts
import type { TriagedEmail, UpsertInput } from "./types.js";

export async function upsertEmails(db: D1Database, rows: UpsertInput[]): Promise<{ inserted: number; skipped: number }> {
  let inserted = 0;
  let skipped = 0;
  // INSERT OR IGNORE preserves status='trashed' for rows the user has already acted on.
  const stmt = db.prepare(
    `INSERT OR IGNORE INTO triaged_emails
       (msg_id, received_at, classified_at, sender, subject, summary, category)
     VALUES (?, ?, ?, ?, ?, ?, ?)`
  );
  for (const r of rows) {
    const result = await stmt
      .bind(r.msg_id, r.received_at, r.classified_at, r.sender, r.subject, r.summary, r.category)
      .run();
    if (result.meta.changes === 1) inserted++;
    else skipped++;
  }
  return { inserted, skipped };
}

export async function listActiveLast7Days(db: D1Database): Promise<TriagedEmail[]> {
  const { results } = await db
    .prepare(
      `SELECT msg_id, received_at, classified_at, sender, subject, summary, category, status
         FROM triaged_emails
        WHERE status = 'active'
          AND classified_at > datetime('now', '-7 days')
        ORDER BY received_at DESC`
    )
    .all<TriagedEmail>();
  return results ?? [];
}

export async function markTrashed(db: D1Database, msgId: string): Promise<boolean> {
  const result = await db
    .prepare(`UPDATE triaged_emails SET status = 'trashed' WHERE msg_id = ? AND status = 'active'`)
    .bind(msgId)
    .run();
  return result.meta.changes > 0;
}
```

- [ ] **Step 3: Replace the `/mail/sync` stub in `index.ts`**

Add to the top of the file (after Env interface):

```ts
import { upsertEmails, listActiveLast7Days, markTrashed } from "./db.js";
import type { UpsertInput, Category } from "./types.js";
```

Then replace the `/mail/sync` branch:

```ts
    if (url.pathname === "/mail/sync" && request.method === "POST") {
      if (request.headers.get("X-Sync-Secret") !== env.SYNC_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }
      let body: { classified_at?: string; emails?: UpsertInput[] };
      try {
        body = await request.json();
      } catch {
        return new Response("invalid json", { status: 400 });
      }
      if (!Array.isArray(body.emails)) {
        return new Response("missing emails array", { status: 400 });
      }
      // Minimal validation — reject rows missing required fields. Full schema
      // enforcement is done client-side in Python before POST.
      for (const e of body.emails) {
        if (!e.msg_id || !e.sender || !e.category || !e.received_at) {
          return new Response(`invalid email row: ${JSON.stringify(e)}`, { status: 400 });
        }
      }
      const result = await upsertEmails(env.DB, body.emails);
      return Response.json({ inserted: result.inserted, skipped_existing: result.skipped });
    }
```

- [ ] **Step 4: Typecheck**

```bash
cd worker-gmail
npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 5: Commit**

```bash
git add worker-gmail/src/
git commit -m "feat(gmail-triage): POST /mail/sync with SYNC_SECRET auth + D1 upsert"
```

---

## Task 4: Worker — GET /mail/list

**Files:**
- Modify: `worker-gmail/src/index.ts`

- [ ] **Step 1: Add a key-auth helper near the top of `index.ts`**

Before the default export:

```ts
function authUser(request: Request, url: URL, env: Env): boolean {
  const headerKey = request.headers.get("X-Mail-Key");
  const queryKey = url.searchParams.get("key");
  const provided = headerKey ?? queryKey ?? "";
  if (!provided) return false;
  // Constant-time compare
  if (provided.length !== env.USER_KEY.length) return false;
  let diff = 0;
  for (let i = 0; i < provided.length; i++) diff |= provided.charCodeAt(i) ^ env.USER_KEY.charCodeAt(i);
  return diff === 0;
}

function corsHeaders(): Headers {
  const h = new Headers();
  h.set("Access-Control-Allow-Origin", "*");
  h.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type, X-Mail-Key");
  return h;
}
```

Note on CORS: v1 of this plan can accept `Access-Control-Allow-Origin: *`. When Portal's deployed domain is known, change to that specific origin. Justification: the endpoint is key-protected, so CORS is not the security boundary.

- [ ] **Step 2: Replace `/mail/list` branch**

```ts
    if (url.pathname === "/mail/list" && request.method === "GET") {
      if (!authUser(request, url, env)) {
        return new Response("unauthorized", { status: 401, headers: corsHeaders() });
      }
      const rows = await listActiveLast7Days(env.DB);
      return Response.json(
        { emails: rows, as_of: new Date().toISOString() },
        { headers: corsHeaders() },
      );
    }
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }
```

- [ ] **Step 3: Local smoke test**

```bash
cd worker-gmail
# D1 local uses a sqlite file under .wrangler/. Initialize it:
npx wrangler d1 execute portal-gmail --local --file=schema.sql
# Start dev server (pass required secrets as vars for local)
npx wrangler dev --local \
  --var SYNC_SECRET:sync-test \
  --var USER_KEY:user-test-abcdef \
  --var SMTP_USER:x --var SMTP_PASSWORD:x
```

In another terminal:

```bash
# 1. Unauthorized without key
curl -i "http://127.0.0.1:8787/mail/list"
# Expected: 401

# 2. Sync a row
curl -s -X POST http://127.0.0.1:8787/mail/sync \
  -H "X-Sync-Secret: sync-test" \
  -H "Content-Type: application/json" \
  -d '{"classified_at":"2026-04-12T22:00:00Z","emails":[{
    "msg_id":"<t1@x>",
    "received_at":"2026-04-12T10:00:00Z",
    "classified_at":"2026-04-12T22:00:00Z",
    "sender":"a@b",
    "subject":"Hi",
    "summary":"test",
    "category":"IMPORTANT"
  }]}'
# Expected: {"inserted":1,"skipped_existing":0}

# 3. List with key
curl -s "http://127.0.0.1:8787/mail/list?key=user-test-abcdef" | jq
# Expected: {"emails":[{...t1...}], "as_of": "..."}
```

Stop `wrangler dev` with Ctrl+C.

- [ ] **Step 4: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): GET /mail/list with USER_KEY auth + CORS"
```

---

## Task 5: Worker — hand-rolled IMAP client

**Files:**
- Modify: `worker-gmail/src/index.ts`

Gmail IMAP is a text protocol. For trashing one message by Message-ID, we need only 4 tagged commands: LOGIN, SELECT INBOX, UID SEARCH HEADER, UID STORE.

- [ ] **Step 1: Add IMAP client block to `index.ts`**

Before the default export, after `corsHeaders`:

```ts
// ── IMAP client (Gmail-specific, minimal) ────────────────────────────────────

import { connect, type Socket } from "cloudflare:sockets";

const enc = new TextEncoder();
const dec = new TextDecoder();

type TrashResult = "trashed" | "not_found" | "auth_failed" | "error";

async function readUntilFirstLine(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  timeoutMs = 10000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let buf = "";
  while (Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    if (buf.includes("\r\n")) return buf;
  }
  throw new Error("imap greeting timeout");
}

async function readUntilTag(
  reader: ReadableStreamDefaultReader<Uint8Array>,
  tag: string,
  timeoutMs = 10000,
): Promise<string> {
  const deadline = Date.now() + timeoutMs;
  let buf = "";
  while (Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const re = new RegExp(`^${tag} (OK|NO|BAD)[^\\r\\n]*\\r?\\n`, "m");
    if (re.test(buf)) return buf;
  }
  throw new Error("imap read timeout");
}

function imapOk(response: string, tag: string): boolean {
  return new RegExp(`^${tag} OK`, "m").test(response);
}

function parseSearchUid(response: string): string | null {
  const m = response.match(/^\* SEARCH\s+(\d+)/m);
  return m ? m[1] : null;
}

export async function imapTrashMessage(
  user: string, password: string, msgId: string,
): Promise<TrashResult> {
  let socket: Socket | undefined;
  try {
    socket = connect({
      hostname: "imap.gmail.com", port: 993,
      secureTransport: "on", allowHalfOpen: false,
    });
    const writer = socket.writable.getWriter();
    const reader = socket.readable.getReader();

    const send = async (line: string) => {
      await writer.write(enc.encode(line + "\r\n"));
    };

    // Greeting: * OK Gimap ready ...
    await readUntilFirstLine(reader);

    // RFC 3501 quote escaping: double backslash + double-quote in the actual string
    const esc = (s: string) => s.replaceAll("\\", "\\\\").replaceAll("\"", "\\\"");

    await send(`A1 LOGIN "${esc(user)}" "${esc(password)}"`);
    const loginResp = await readUntilTag(reader, "A1");
    if (!imapOk(loginResp, "A1")) return "auth_failed";

    await send(`A2 SELECT INBOX`);
    const selResp = await readUntilTag(reader, "A2");
    if (!imapOk(selResp, "A2")) return "error";

    // UID SEARCH HEADER — msgId typically contains <angle brackets>, quote the whole thing
    await send(`A3 UID SEARCH HEADER "Message-ID" "${esc(msgId)}"`);
    const searchResp = await readUntilTag(reader, "A3");
    if (!imapOk(searchResp, "A3")) return "error";
    const uid = parseSearchUid(searchResp);
    if (!uid) return "not_found";

    // Gmail-specific: X-GM-LABELS with "\Trash" — send two backslashes over the wire
    await send(`A4 UID STORE ${uid} +X-GM-LABELS "\\\\Trash"`);
    const storeResp = await readUntilTag(reader, "A4");
    if (!imapOk(storeResp, "A4")) return "error";

    await send(`A5 LOGOUT`);
    try { await writer.close(); } catch {}

    return "trashed";
  } catch {
    return "error";
  } finally {
    try { await socket?.close(); } catch {}
  }
}
```

- [ ] **Step 2: Typecheck**

```bash
cd worker-gmail
npx tsc --noEmit
```

Expected: zero errors.

- [ ] **Step 3: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): hand-rolled Gmail IMAP trash helper in Worker"
```

---

## Task 6: Worker — POST /mail/trash

**Files:**
- Modify: `worker-gmail/src/index.ts`

- [ ] **Step 1: Replace the `/mail/trash` branch**

```ts
    if (url.pathname === "/mail/trash" && request.method === "POST") {
      if (!authUser(request, url, env)) {
        return new Response("unauthorized", { status: 401, headers: corsHeaders() });
      }
      let body: { msg_id?: string };
      try {
        body = await request.json();
      } catch {
        return new Response("invalid json", { status: 400, headers: corsHeaders() });
      }
      if (!body.msg_id) {
        return new Response("missing msg_id", { status: 400, headers: corsHeaders() });
      }

      const result = await imapTrashMessage(env.SMTP_USER, env.SMTP_PASSWORD, body.msg_id);

      if (result === "trashed") {
        await markTrashed(env.DB, body.msg_id);
        return Response.json({ status: "trashed" }, { headers: corsHeaders() });
      }
      if (result === "not_found") {
        // Also mark as trashed locally — the row is gone from Gmail regardless.
        await markTrashed(env.DB, body.msg_id);
        return Response.json({ status: "already_gone" }, { headers: corsHeaders() });
      }
      if (result === "auth_failed") {
        return Response.json({ status: "auth_failed" }, { status: 503, headers: corsHeaders() });
      }
      return Response.json({ status: "error" }, { status: 503, headers: corsHeaders() });
    }
```

- [ ] **Step 2: Typecheck**

```bash
cd worker-gmail
npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): POST /mail/trash performs IMAP trash + D1 status update"
```

---

## Task 7: Worker — deploy + live smoke test

**Files:** none (operational).

- [ ] **Step 1: Create D1 database (one-time)**

```bash
cd worker-gmail
npx wrangler d1 create portal-gmail
# Output includes the database_id — paste it into wrangler.jsonc
```

- [ ] **Step 2: Apply schema to remote D1**

```bash
cd worker-gmail
npx wrangler d1 execute portal-gmail --remote --file=schema.sql
```

- [ ] **Step 3: Set secrets**

```bash
cd worker-gmail
npx wrangler secret put SYNC_SECRET     # paste: openssl rand -hex 32
npx wrangler secret put USER_KEY        # paste: openssl rand -hex 32
npx wrangler secret put SMTP_USER       # paste: your.account@gmail.com
npx wrangler secret put SMTP_PASSWORD   # paste: Gmail app password
```

- [ ] **Step 4: Deploy**

```bash
cd worker-gmail
npx wrangler deploy
# Note the deployed URL, e.g. https://worker-gmail.<account>.workers.dev
```

- [ ] **Step 5: End-to-end smoke test**

```bash
export WORKER=https://worker-gmail.<account>.workers.dev
export SYNC=<the SYNC_SECRET you set>
export KEY=<the USER_KEY you set>

# Push a test row
curl -s -X POST $WORKER/mail/sync \
  -H "X-Sync-Secret: $SYNC" -H "Content-Type: application/json" \
  -d '{"classified_at":"2026-04-12T22:00:00Z","emails":[{
    "msg_id":"<smoke@test>",
    "received_at":"2026-04-12T10:00:00Z",
    "classified_at":"2026-04-12T22:00:00Z",
    "sender":"smoke@test",
    "subject":"Smoke",
    "summary":"test",
    "category":"NEUTRAL"
  }]}'

# Read it back
curl -s "$WORKER/mail/list?key=$KEY" | jq
# Expected: emails array with the smoke row

# Clean up
npx wrangler d1 execute portal-gmail --remote \
  --command "DELETE FROM triaged_emails WHERE msg_id='<smoke@test>'"
```

- [ ] **Step 6: Test trash with a real email**

Send yourself a test email in Gmail. Grab its Message-ID via Gmail web → Show original.

```bash
MSGID='<paste-message-id-here>'

# Insert a row so markTrashed has something to UPDATE (optional — markTrashed is harmless if row is absent)
curl -s -X POST $WORKER/mail/sync \
  -H "X-Sync-Secret: $SYNC" -H "Content-Type: application/json" \
  -d "{\"classified_at\":\"$(date -u +%FT%TZ)\",\"emails\":[{
    \"msg_id\":\"$MSGID\",
    \"received_at\":\"$(date -u +%FT%TZ)\",
    \"classified_at\":\"$(date -u +%FT%TZ)\",
    \"sender\":\"self\",
    \"subject\":\"trash test\",
    \"summary\":\"smoke\",
    \"category\":\"TRASH_CANDIDATE\"
  }]}"

# Trash it
curl -s -X POST "$WORKER/mail/trash?key=$KEY" \
  -H "Content-Type: application/json" \
  -d "{\"msg_id\":\"$MSGID\"}"
# Expected: {"status":"trashed"} within ~1 second

# Verify in Gmail UI — email should be in Trash folder
```

- [ ] **Step 7: Commit (none — this was operational verification)**

No files changed; no commit needed.

---

## Task 8: Python — scaffold

**Files:**
- Create: `pipeline/scripts/gmail/__init__.py`
- Create: `pipeline/scripts/gmail/requirements.txt`
- Create: `pipeline/scripts/gmail/README.md`
- Create: `pipeline/tests/unit/gmail/__init__.py`
- Create: `pipeline/tests/unit/gmail/conftest.py`

- [ ] **Step 1: Create package markers**

```bash
touch pipeline/scripts/gmail/__init__.py
touch pipeline/tests/unit/gmail/__init__.py
```

- [ ] **Step 2: Create `pipeline/scripts/gmail/requirements.txt`**

```txt
anthropic>=0.40.0
httpx>=0.28.0
```

- [ ] **Step 3: Create `pipeline/scripts/gmail/README.md`**

```markdown
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
```

- [ ] **Step 4: Create `pipeline/tests/unit/gmail/conftest.py`**

```python
"""Shared fixtures for Gmail triage tests."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeEmail:
    msg_id: str
    sender: str
    subject: str
    body_excerpt: str
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/gmail/ pipeline/tests/unit/gmail/
git commit -m "feat(gmail-triage): scaffold Python package under pipeline/scripts/gmail"
```

---

## Task 9: Python — IMAP client

**Files:**
- Create: `pipeline/scripts/gmail/imap_client.py`
- Create: `pipeline/tests/unit/gmail/test_imap_client.py`

- [ ] **Step 1: Write the failing test**

`pipeline/tests/unit/gmail/test_imap_client.py`:

```python
"""Tests for IMAP client fetch/search wrappers.

Mocks ``imaplib.IMAP4_SSL`` directly and verifies the wrapper calls the
expected sequence of IMAP commands.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from gmail.imap_client import ImapConfig, fetch_unread_last_24h, parse_message


class TestFetchUnreadLast24h:
    @patch("imaplib.IMAP4_SSL")
    def test_happy_path(self, mock_imap_cls: MagicMock) -> None:
        m = MagicMock()
        mock_imap_cls.return_value = m
        m.login.return_value = ("OK", [b"LOGIN ok"])
        m.select.return_value = ("OK", [b"42"])
        m.uid.side_effect = [
            ("OK", [b"1 2 3"]),
            ("OK", [(b"1 (BODY[] {...})", b"From: a@x\r\nSubject: A\r\nMessage-ID: <1@x>\r\n\r\nbody1")]),
            ("OK", [(b"2 (BODY[] {...})", b"From: b@x\r\nSubject: B\r\nMessage-ID: <2@x>\r\n\r\nbody2")]),
            ("OK", [(b"3 (BODY[] {...})", b"From: c@x\r\nSubject: C\r\nMessage-ID: <3@x>\r\n\r\nbody3")]),
        ]
        cfg = ImapConfig(user="me@gmail.com", password="pw")
        emails = fetch_unread_last_24h(cfg)
        assert len(emails) == 3
        assert emails[0].msg_id == "<1@x>"
        assert emails[0].subject == "A"
        m.login.assert_called_once_with("me@gmail.com", "pw")
        m.select.assert_called_once_with("INBOX")

    @patch("imaplib.IMAP4_SSL")
    def test_empty_inbox(self, mock_imap_cls: MagicMock) -> None:
        m = MagicMock()
        mock_imap_cls.return_value = m
        m.login.return_value = ("OK", [b"ok"])
        m.select.return_value = ("OK", [b"0"])
        m.uid.return_value = ("OK", [b""])
        cfg = ImapConfig(user="me@gmail.com", password="pw")
        assert fetch_unread_last_24h(cfg) == []


class TestParseMessage:
    def test_extracts_core_fields(self) -> None:
        raw = (
            b"From: Foo <foo@example.com>\r\n"
            b"Subject: Test Subject\r\n"
            b"Message-ID: <abc123@example.com>\r\n"
            b"Date: Mon, 12 Apr 2026 10:00:00 +0000\r\n"
            b"\r\n"
            b"Hello world. This is the body."
        )
        msg = parse_message(raw)
        assert msg.msg_id == "<abc123@example.com>"
        assert msg.sender == "Foo <foo@example.com>"
        assert msg.subject == "Test Subject"
        assert msg.received_at.startswith("2026-04-12")
        assert "Hello world" in msg.body_excerpt

    def test_handles_missing_subject(self) -> None:
        raw = b"From: x@y\r\nMessage-ID: <m@y>\r\nDate: Mon, 12 Apr 2026 10:00:00 +0000\r\n\r\nbody"
        assert parse_message(raw).subject == ""

    def test_handles_missing_date(self) -> None:
        raw = b"From: x@y\r\nSubject: s\r\nMessage-ID: <m@y>\r\n\r\nbody"
        # Fallback to empty string — worker_sync will reject such rows
        assert parse_message(raw).received_at == ""
```

- [ ] **Step 2: Run — expect failure**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_imap_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'gmail.imap_client'`.

- [ ] **Step 3: Implement `pipeline/scripts/gmail/imap_client.py`**

```python
"""Minimal Gmail IMAP client: connect, login, search unread last 24h, fetch.

Stdlib imaplib + email. Returns plain dataclasses so downstream modules
don't depend on imaplib's awkward response shapes.
"""
from __future__ import annotations

import email
import email.policy
import email.utils
import imaplib
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta


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
        try:
            m.logout()
        except Exception:
            pass


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
```

- [ ] **Step 4: Run — expect pass**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_imap_client.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/gmail/imap_client.py pipeline/tests/unit/gmail/test_imap_client.py
git commit -m "feat(gmail-triage): IMAP fetch + MIME parse with received_at extraction"
```

---

## Task 10: Python — classify

**Files:**
- Create: `pipeline/scripts/gmail/classify.py`
- Create: `pipeline/tests/unit/gmail/test_classify.py`

- [ ] **Step 1: Write the failing test**

`pipeline/tests/unit/gmail/test_classify.py`:

```python
"""Tests for Anthropic classification call and response parsing."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from gmail.classify import Category, classify_emails
from gmail.imap_client import ParsedMessage


def _msg(msg_id: str, subject: str) -> ParsedMessage:
    return ParsedMessage(
        msg_id=msg_id, received_at="2026-04-12T10:00:00+00:00",
        sender="x@example.com", subject=subject, body_excerpt="",
    )


class TestClassifyEmails:
    @patch("gmail.classify.Anthropic")
    def test_parses_response(self, mock_anthropic_cls: MagicMock) -> None:
        client = MagicMock()
        mock_anthropic_cls.return_value = client
        response_text = json.dumps({
            "classifications": [
                {"msg_id": "<1>", "category": "IMPORTANT", "summary": "recruiter"},
                {"msg_id": "<2>", "category": "TRASH_CANDIDATE", "summary": "marketing"},
                {"msg_id": "<3>", "category": "NEUTRAL", "summary": "slack ping"},
            ]
        })
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text=response_text)],
        )
        emails = [_msg("<1>", "Role"), _msg("<2>", "Sale"), _msg("<3>", "Slack")]
        result = classify_emails(emails, api_key="sk-test")
        assert result["<1>"].category == Category.IMPORTANT
        assert result["<2>"].category == Category.TRASH_CANDIDATE
        assert result["<3>"].category == Category.NEUTRAL

    @patch("gmail.classify.Anthropic")
    def test_empty_list_skips_api(self, mock_anthropic_cls: MagicMock) -> None:
        result = classify_emails([], api_key="sk-test")
        assert result == {}
        mock_anthropic_cls.return_value.messages.create.assert_not_called()

    @patch("gmail.classify.Anthropic")
    def test_fallback_on_parse_error(self, mock_anthropic_cls: MagicMock) -> None:
        client = MagicMock()
        mock_anthropic_cls.return_value = client
        client.messages.create.return_value = MagicMock(
            content=[MagicMock(text="not json")],
        )
        result = classify_emails([_msg("<1>", "x")], api_key="sk-test")
        assert result["<1>"].category == Category.NEUTRAL
        assert "AI unavailable" in result["<1>"].summary
```

- [ ] **Step 2: Run — expect failure**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_classify.py -v
```

- [ ] **Step 3: Implement `pipeline/scripts/gmail/classify.py`**

```python
"""Anthropic classification of Gmail messages.

Fails open: on any Anthropic error or unparseable response, every email
falls back to NEUTRAL with a note so the sync still ships a result for
each email (the UI always shows *something*).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from enum import Enum

from anthropic import Anthropic

from gmail.imap_client import ParsedMessage

log = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 4096


class Category(str, Enum):
    IMPORTANT = "IMPORTANT"
    NEUTRAL = "NEUTRAL"
    TRASH_CANDIDATE = "TRASH_CANDIDATE"


@dataclass(frozen=True)
class Classification:
    category: Category
    summary: str


def _system_prompt() -> str:
    return """\
You triage Gmail for the user. Return STRICT JSON only.

Categories:
  IMPORTANT       — recruiter outreach (猎头), emails demanding user action
                    (bills with due dates, security alerts needing response,
                    time-sensitive invitations, emails asking a direct question).
  TRASH_CANDIDATE — promotional newsletters the user doesn't engage with,
                    routine system notifications (login-success, "your weekly
                    summary"), duplicate marketing.
  NEUTRAL         — anything else. When in doubt, NEUTRAL.

Few-shot examples (user's taste calibration):
  "Software Engineer role at Stripe — competitive comp"
    → IMPORTANT (recruiter)
  "Your statement is ready — Chase Freedom"
    → IMPORTANT (bill action)
  "Notion's weekly digest: 5 pages you haven't opened"
    → TRASH_CANDIDATE (marketing)
  "Security alert: sign-in from Chrome on Windows"
    → TRASH_CANDIDATE (routine, own device)
  "Slack: 2 new messages in #general"
    → NEUTRAL
"""


def _user_prompt(emails: list[ParsedMessage]) -> str:
    lines = ["Classify the following emails. Output JSON:"]
    lines.append(
        '  {"classifications": [{"msg_id": "...", '
        '"category": "IMPORTANT|NEUTRAL|TRASH_CANDIDATE", '
        '"summary": "one short sentence"}]}'
    )
    lines.append("")
    lines.append("Emails:")
    for e in emails:
        lines.append(f"[msg_id: {e.msg_id}] From: {e.sender} | Subject: {e.subject}")
        excerpt = e.body_excerpt.replace("\n", " ")[:300]
        if excerpt:
            lines.append(f"  Body: {excerpt}")
    return "\n".join(lines)


def _fallback(emails: list[ParsedMessage], reason: str) -> dict[str, Classification]:
    return {
        e.msg_id: Classification(category=Category.NEUTRAL, summary=f"AI unavailable — {reason}")
        for e in emails
    }


def classify_emails(
    emails: list[ParsedMessage], *, api_key: str,
) -> dict[str, Classification]:
    if not emails:
        return {}

    client = Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_system_prompt(),
            messages=[{"role": "user", "content": _user_prompt(emails)}],
        )
    except Exception as e:  # noqa: BLE001 — fail-open is the contract
        log.warning("anthropic call failed: %s", e)
        return _fallback(emails, "classifier failed")

    text = response.content[0].text if response.content else ""
    try:
        parsed = json.loads(text)
        items = parsed["classifications"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        log.warning("anthropic returned unparseable response: %s", e)
        return _fallback(emails, "unparseable response")

    out: dict[str, Classification] = {}
    for item in items:
        try:
            out[item["msg_id"]] = Classification(
                category=Category(item["category"]),
                summary=str(item.get("summary", "")),
            )
        except (KeyError, ValueError) as e:
            log.warning("skipping malformed classification item: %s", e)

    for e in emails:
        out.setdefault(e.msg_id, Classification(Category.NEUTRAL, "no classification returned"))
    return out
```

- [ ] **Step 4: Run — expect pass**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_classify.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/gmail/classify.py pipeline/tests/unit/gmail/test_classify.py
git commit -m "feat(gmail-triage): Claude Haiku classifier with fail-open JSON parse"
```

---

## Task 11: Python — worker_sync client

**Files:**
- Create: `pipeline/scripts/gmail/worker_sync.py`
- Create: `pipeline/tests/unit/gmail/test_worker_sync.py`

- [ ] **Step 1: Write the failing test**

`pipeline/tests/unit/gmail/test_worker_sync.py`:

```python
"""Tests for POST /mail/sync HTTP wrapper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gmail.worker_sync import SyncResult, WorkerSyncClient, WorkerSyncError


class TestWorkerSyncClient:
    @patch("gmail.worker_sync.httpx.Client")
    def test_returns_result_on_200(self, mock_cls: MagicMock) -> None:
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"inserted": 5, "skipped_existing": 3}),
        )
        c = WorkerSyncClient(base_url="https://w.example", secret="s")
        r = c.sync(
            classified_at="2026-04-12T22:00:00Z",
            emails=[{
                "msg_id": "<1>", "received_at": "2026-04-12T10:00:00Z",
                "classified_at": "2026-04-12T22:00:00Z",
                "sender": "a@b", "subject": "hi", "summary": "test",
                "category": "IMPORTANT",
            }],
        )
        assert r == SyncResult(inserted=5, skipped=3)

        call = client.post.call_args
        assert call.args[0] == "https://w.example/mail/sync"
        assert call.kwargs["headers"]["X-Sync-Secret"] == "s"
        body = call.kwargs["json"]
        assert len(body["emails"]) == 1

    @patch("gmail.worker_sync.httpx.Client")
    def test_raises_on_non_200(self, mock_cls: MagicMock) -> None:
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.return_value = MagicMock(status_code=401, text="unauthorized")
        c = WorkerSyncClient(base_url="https://w.example", secret="wrong")
        with pytest.raises(WorkerSyncError):
            c.sync(classified_at="x", emails=[])

    @patch("gmail.worker_sync.httpx.Client")
    def test_raises_on_network_error(self, mock_cls: MagicMock) -> None:
        import httpx
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.side_effect = httpx.ConnectError("dns failed")
        c = WorkerSyncClient(base_url="https://w.example", secret="s")
        with pytest.raises(WorkerSyncError):
            c.sync(classified_at="x", emails=[])
```

- [ ] **Step 2: Run — expect failure**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_worker_sync.py -v
```

- [ ] **Step 3: Implement `pipeline/scripts/gmail/worker_sync.py`**

```python
"""POST /mail/sync client.

Hits the Worker with all classified emails in one batch. Raises
WorkerSyncError on any non-200 or network error — the daily cron fails loudly
so GH Actions sends a notification. No retries in v1 (GitHub's own retry
settings + the daily cadence cover transient failures).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class WorkerSyncError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncResult:
    inserted: int
    skipped: int


class WorkerSyncClient:
    def __init__(self, *, base_url: str, secret: str, timeout: float = 15.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._timeout = timeout

    def sync(self, *, classified_at: str, emails: list[dict[str, Any]]) -> SyncResult:
        body = {"classified_at": classified_at, "emails": emails}
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self._base_url}/mail/sync",
                    headers={"X-Sync-Secret": self._secret, "Content-Type": "application/json"},
                    json=body,
                )
        except httpx.HTTPError as e:
            raise WorkerSyncError(f"network error: {e}") from e

        if r.status_code != 200:
            raise WorkerSyncError(f"sync returned {r.status_code}: {r.text[:200]}")

        data = r.json()
        try:
            return SyncResult(
                inserted=int(data["inserted"]),
                skipped=int(data["skipped_existing"]),
            )
        except (KeyError, TypeError, ValueError) as e:
            raise WorkerSyncError(f"sync returned invalid body: {data!r}") from e
```

- [ ] **Step 4: Run — expect pass**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_worker_sync.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/gmail/worker_sync.py pipeline/tests/unit/gmail/test_worker_sync.py
git commit -m "feat(gmail-triage): httpx POST /mail/sync client"
```

---

## Task 12: Python — triage CLI

**Files:**
- Create: `pipeline/scripts/gmail/triage.py`

Glue tying fetch → classify → sync. Tested end-to-end via `--dry-run`.

- [ ] **Step 1: Implement `pipeline/scripts/gmail/triage.py`**

```python
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
```

- [ ] **Step 2: Dry-run smoke test**

```bash
cd pipeline
PORTAL_SMTP_USER=<real-gmail> PORTAL_SMTP_PASSWORD=<app-pw> ANTHROPIC_API_KEY=<sk-ant-...> \
  .venv/Scripts/python.exe scripts/gmail/triage.py --sync --dry-run | head -40
```

Expected: JSON with `classified_at` + `emails` array. Verify categories look sensible.

- [ ] **Step 3: Commit**

```bash
git add pipeline/scripts/gmail/triage.py
git commit -m "feat(gmail-triage): CLI orchestrator — fetch→classify→sync"
```

---

## Task 13: Frontend — Zod schemas

**Files:**
- Create: `src/lib/schemas/mail.ts`

- [ ] **Step 1: Create `src/lib/schemas/mail.ts`**

```ts
// ── Gmail triage schemas ─────────────────────────────────────────────────────

import { z } from "zod";

export const CategorySchema = z.enum(["IMPORTANT", "NEUTRAL", "TRASH_CANDIDATE"]);
export type Category = z.infer<typeof CategorySchema>;

export const StatusSchema = z.enum(["active", "trashed"]);
export type Status = z.infer<typeof StatusSchema>;

export const TriagedEmailSchema = z.object({
  msg_id: z.string(),
  received_at: z.string(),
  classified_at: z.string(),
  sender: z.string(),
  subject: z.string(),
  summary: z.string(),
  category: CategorySchema,
  status: StatusSchema,
});
export type TriagedEmail = z.infer<typeof TriagedEmailSchema>;

export const MailListResponseSchema = z.object({
  emails: z.array(TriagedEmailSchema),
  as_of: z.string(),
});
export type MailListResponse = z.infer<typeof MailListResponseSchema>;

export const TrashResponseSchema = z.object({
  status: z.enum(["trashed", "already_gone", "auth_failed", "error"]),
});
export type TrashResponse = z.infer<typeof TrashResponseSchema>;
```

- [ ] **Step 2: Typecheck (from repo root)**

```bash
npm run build  # or `npx tsc --noEmit` if build is slow
```

Expected: no new type errors.

- [ ] **Step 3: Commit**

```bash
git add src/lib/schemas/mail.ts
git commit -m "feat(gmail-triage): Zod schemas for MailListResponse and TrashResponse"
```

---

## Task 14: Frontend — `use-mail` hook

**Files:**
- Create: `src/lib/use-mail.ts`

- [ ] **Step 1: Create `src/lib/use-mail.ts`**

```ts
"use client";

// ── Gmail triage data hook ───────────────────────────────────────────────────

import { useCallback, useEffect, useState } from "react";
import {
  MailListResponseSchema,
  TrashResponseSchema,
  type MailListResponse,
  type TriagedEmail,
} from "@/lib/schemas/mail";

const WORKER_URL = process.env.NEXT_PUBLIC_GMAIL_WORKER_URL ?? "";
const KEY_STORAGE = "portal:gmail:key";

function resolveKey(): string | null {
  if (typeof window === "undefined") return null;
  // Prefer URL ?key=...: save it, strip it, and reload with clean URL.
  const url = new URL(window.location.href);
  const fromQuery = url.searchParams.get("key");
  if (fromQuery) {
    window.localStorage.setItem(KEY_STORAGE, fromQuery);
    url.searchParams.delete("key");
    window.history.replaceState(null, "", url.toString());
    return fromQuery;
  }
  return window.localStorage.getItem(KEY_STORAGE);
}

export interface UseMailState {
  loading: boolean;
  error: string | null;
  data: MailListResponse | null;
  keyMissing: boolean;
  deleteEmail: (msgId: string) => Promise<void>;
  refetch: () => void;
}

export function useMail(): UseMailState {
  const [data, setData] = useState<MailListResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [keyMissing, setKeyMissing] = useState(false);
  const [refreshTick, setRefreshTick] = useState(0);

  useEffect(() => {
    const key = resolveKey();
    if (!key) {
      setKeyMissing(true);
      setLoading(false);
      return;
    }
    if (!WORKER_URL) {
      setError("NEXT_PUBLIC_GMAIL_WORKER_URL not configured");
      setLoading(false);
      return;
    }

    const ctrl = new AbortController();
    setLoading(true);
    setError(null);

    fetch(`${WORKER_URL}/mail/list`, {
      headers: { "X-Mail-Key": key },
      signal: ctrl.signal,
    })
      .then(async (r) => {
        if (r.status === 401) {
          window.localStorage.removeItem(KEY_STORAGE);
          setKeyMissing(true);
          return null;
        }
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const json = await r.json();
        return MailListResponseSchema.parse(json);
      })
      .then((parsed) => {
        if (parsed) setData(parsed);
      })
      .catch((e: unknown) => {
        if (e instanceof Error && e.name !== "AbortError") setError(e.message);
      })
      .finally(() => setLoading(false));

    return () => ctrl.abort();
  }, [refreshTick]);

  const deleteEmail = useCallback(async (msgId: string) => {
    const key = window.localStorage.getItem(KEY_STORAGE);
    if (!key) throw new Error("no key");

    // Optimistic: drop from local state immediately.
    setData((prev) => prev && { ...prev, emails: prev.emails.filter((e) => e.msg_id !== msgId) });

    const r = await fetch(`${WORKER_URL}/mail/trash`, {
      method: "POST",
      headers: { "X-Mail-Key": key, "Content-Type": "application/json" },
      body: JSON.stringify({ msg_id: msgId }),
    });
    const json = await r.json();
    const parsed = TrashResponseSchema.parse(json);

    if (parsed.status === "trashed" || parsed.status === "already_gone") {
      // Success — nothing to undo.
      return;
    }
    // Rollback: re-fetch to restore the row.
    setRefreshTick((t) => t + 1);
    throw new Error(parsed.status);
  }, []);

  const refetch = useCallback(() => setRefreshTick((t) => t + 1), []);

  return { loading, error, data, keyMissing, deleteEmail, refetch };
}

export function groupByCategory(emails: TriagedEmail[]): {
  important: TriagedEmail[];
  neutral: TriagedEmail[];
  trash: TriagedEmail[];
} {
  return {
    important: emails.filter((e) => e.category === "IMPORTANT"),
    neutral: emails.filter((e) => e.category === "NEUTRAL"),
    trash: emails.filter((e) => e.category === "TRASH_CANDIDATE"),
  };
}
```

- [ ] **Step 2: Typecheck**

```bash
npx tsc --noEmit
```

- [ ] **Step 3: Commit**

```bash
git add src/lib/use-mail.ts
git commit -m "feat(gmail-triage): use-mail hook with key resolution + optimistic delete"
```

---

## Task 15: Frontend — `/mail` page + components

**Files:**
- Create: `src/components/mail/mail-row.tsx`
- Create: `src/components/mail/delete-button.tsx`
- Create: `src/components/mail/mail-list.tsx`
- Create: `src/app/mail/page.tsx`

- [ ] **Step 1: Create `src/components/mail/delete-button.tsx`**

```tsx
"use client";

import { useState } from "react";

interface Props {
  msgId: string;
  onDelete: (msgId: string) => Promise<void>;
}

export function DeleteButton({ msgId, onDelete }: Props) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const handleClick = async () => {
    setBusy(true);
    setErr(null);
    try {
      await onDelete(msgId);
    } catch (e: unknown) {
      setErr(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-flex flex-col items-end gap-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className="rounded border border-red-200 bg-red-50 px-2 py-1 text-sm text-red-700 hover:bg-red-100 disabled:opacity-50"
      >
        {busy ? "Deleting..." : "Delete"}
      </button>
      {err && <span className="text-xs text-red-600">{err}</span>}
    </div>
  );
}
```

- [ ] **Step 2: Create `src/components/mail/mail-row.tsx`**

```tsx
"use client";

import type { TriagedEmail } from "@/lib/schemas/mail";
import { DeleteButton } from "@/components/mail/delete-button";

interface Props {
  email: TriagedEmail;
  onDelete?: (msgId: string) => Promise<void>;
}

export function MailRow({ email, onDelete }: Props) {
  const gmailLink = `https://mail.google.com/mail/u/0/#inbox/${encodeURIComponent(email.msg_id)}`;
  const canDelete = email.category === "TRASH_CANDIDATE" && onDelete;

  return (
    <div className="flex items-start justify-between gap-3 border-b border-gray-100 py-2 last:border-b-0">
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium text-gray-900">{email.subject || "(no subject)"}</div>
        <div className="truncate text-xs text-gray-500">{email.sender}</div>
        {email.summary && <div className="mt-1 text-xs text-gray-700">{email.summary}</div>}
      </div>
      <div className="flex flex-shrink-0 flex-col items-end gap-1">
        <a href={gmailLink} target="_blank" rel="noreferrer" className="text-xs text-blue-600 underline">
          Open in Gmail
        </a>
        {canDelete && <DeleteButton msgId={email.msg_id} onDelete={onDelete!} />}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Create `src/components/mail/mail-list.tsx`**

```tsx
"use client";

import type { TriagedEmail } from "@/lib/schemas/mail";
import { MailRow } from "@/components/mail/mail-row";

interface Props {
  title: string;
  emoji: string;
  emails: TriagedEmail[];
  onDelete?: (msgId: string) => Promise<void>;
}

export function MailSection({ title, emoji, emails, onDelete }: Props) {
  return (
    <section className="mb-6">
      <h2 className="mb-2 text-base font-semibold text-gray-900">
        {emoji} {title} ({emails.length})
      </h2>
      {emails.length === 0 ? (
        <div className="text-sm text-gray-500 italic">None</div>
      ) : (
        <div className="rounded border border-gray-200 bg-white px-4">
          {emails.map((e) => (
            <MailRow key={e.msg_id} email={e} onDelete={onDelete} />
          ))}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Create `src/app/mail/page.tsx`**

```tsx
"use client";

import { groupByCategory, useMail } from "@/lib/use-mail";
import { MailSection } from "@/components/mail/mail-list";

export default function MailPage() {
  const { loading, error, data, keyMissing, deleteEmail } = useMail();

  if (keyMissing) {
    return (
      <main className="mx-auto max-w-3xl p-6">
        <h1 className="mb-4 text-2xl font-bold">Mail</h1>
        <div className="rounded bg-yellow-50 p-4 text-sm">
          <p className="font-semibold">Key required</p>
          <p className="mt-2 text-gray-700">
            Append <code>?key=YOUR_32_CHAR_KEY</code> to this page's URL once. The key is
            saved locally so future visits don't need it.
          </p>
        </div>
      </main>
    );
  }
  if (loading) return <main className="p-6"><p>Loading mail…</p></main>;
  if (error) return <main className="p-6"><p className="text-red-700">Error: {error}</p></main>;
  if (!data) return null;

  const { important, neutral, trash } = groupByCategory(data.emails);
  const asOf = new Date(data.as_of).toLocaleString();

  return (
    <main className="mx-auto max-w-3xl p-6">
      <h1 className="mb-2 text-2xl font-bold">Mail</h1>
      <p className="mb-6 text-sm text-gray-500">as of {asOf}</p>
      <MailSection title="IMPORTANT" emoji="📌" emails={important} />
      <MailSection title="OTHER" emoji="📨" emails={neutral} />
      <MailSection title="SUGGESTED TRASH" emoji="🗑️" emails={trash} onDelete={deleteEmail} />
    </main>
  );
}
```

- [ ] **Step 5: Typecheck + local dev test**

```bash
npm run build
# Or for dev preview: npm run dev; open http://localhost:3000/mail?key=<USER_KEY>
```

Expected: build succeeds. Dev-mode page loads with "Loading mail…" then data.

- [ ] **Step 6: Commit**

```bash
git add src/app/mail/ src/components/mail/
git commit -m "feat(gmail-triage): /mail page + MailRow/MailSection/DeleteButton components"
```

---

## Task 16: Frontend — add Mail nav link

**Files:**
- Modify: whichever file under `src/components/layout/` renders the Finance/Econ top-nav links. Common suspects: `src/components/layout/nav.tsx`, `src/components/layout/header.tsx`, or inline in `src/app/layout.tsx`.

- [ ] **Step 1: Find the nav**

```bash
grep -rn "/finance\|/econ" src/components/layout/ src/app/layout.tsx
```

Identify the file where the Finance and Econ links live.

- [ ] **Step 2: Add a Mail link**

Add a new link next to Finance/Econ, matching the pattern (className, wrapping element) already used there.

Example (shape depends on actual file):

```tsx
<Link href="/mail">Mail</Link>
```

- [ ] **Step 3: Verify — `npm run dev`, open page, confirm Mail link appears, click it, lands on `/mail?...`**

- [ ] **Step 4: Commit**

```bash
git add <modified-nav-file>
git commit -m "feat(gmail-triage): add Mail to top-nav"
```

---

## Task 17: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/gmail-sync.yml`

- [ ] **Step 1: Create the workflow**

```yaml
name: Gmail Sync
on:
  schedule:
    - cron: "0 22 * * *"     # 22:00 UTC = 07:00 +08 (no DST)
  workflow_dispatch: {}

jobs:
  sync:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.13"

      - name: Install dependencies
        run: |
          pip install --upgrade pip
          pip install -r pipeline/scripts/gmail/requirements.txt

      - name: Run triage
        working-directory: pipeline
        run: python scripts/gmail/triage.py --sync
        env:
          PORTAL_SMTP_USER:          ${{ secrets.PORTAL_SMTP_USER }}
          PORTAL_SMTP_PASSWORD:      ${{ secrets.PORTAL_SMTP_PASSWORD }}
          PORTAL_GMAIL_WORKER_URL:   ${{ secrets.PORTAL_GMAIL_WORKER_URL }}
          PORTAL_GMAIL_SYNC_SECRET:  ${{ secrets.PORTAL_GMAIL_SYNC_SECRET }}
          ANTHROPIC_API_KEY:         ${{ secrets.ANTHROPIC_API_KEY }}
```

- [ ] **Step 2: Add the 5 repo secrets via GitHub Settings**

`https://github.com/Guoyuer/portal/settings/secrets/actions` → New repository secret:
- `PORTAL_SMTP_USER`
- `PORTAL_SMTP_PASSWORD`
- `PORTAL_GMAIL_WORKER_URL` (Worker deployed URL from Task 7)
- `PORTAL_GMAIL_SYNC_SECRET` (same value as Worker's `SYNC_SECRET`)
- `ANTHROPIC_API_KEY`

Also add `NEXT_PUBLIC_GMAIL_WORKER_URL` to the Portal build env (Cloudflare Pages settings → Environment variables) so the `/mail` page can reach the Worker. Its value is the same Worker URL.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/gmail-sync.yml
git commit -m "feat(gmail-triage): GitHub Actions daily sync workflow"
```

- [ ] **Step 4: Push + trigger manually**

```bash
git push
gh workflow run gmail-sync.yml
gh run watch
```

Expected: job succeeds. Verify via `curl "$WORKER_URL/mail/list?key=$USER_KEY" | jq` that rows now exist in D1.

---

## Task 18: End-to-end verification

**Files:** none (manual).

- [ ] **Step 1: Visit `/mail` in a browser**

Navigate to `https://portal.example.com/mail?key=<USER_KEY>` once. Verify:
- URL updates to `/mail` (key is saved to localStorage)
- Page renders with 3 sections
- Subject lines appear, summaries appear
- "Open in Gmail" links work
- Second visit (no key in URL) still loads

- [ ] **Step 2: Click Delete on a TRASH_CANDIDATE**

Verify:
- Row immediately disappears (optimistic)
- No error shown
- Open Gmail, confirm email is in Trash folder

- [ ] **Step 3: Invalid-key flow**

In DevTools Application tab, delete the `portal:gmail:key` localStorage entry. Reload `/mail`. Expected: "Key required" prompt.

- [ ] **Step 4: Run Python test suite**

```bash
cd pipeline
.venv/Scripts/pytest.exe -q
```

Expected: all tests pass (including new gmail/ tests).

- [ ] **Step 5: Ruff + mypy on new Python code**

```bash
cd pipeline
.venv/Scripts/ruff.exe check scripts/gmail tests/unit/gmail
.venv/Scripts/mypy.exe scripts/gmail --ignore-missing-imports
```

Expected: clean.

- [ ] **Step 6: Full frontend build**

```bash
npm run build
npm run test        # vitest unit tests
```

Expected: no build errors, existing vitest tests still pass.

- [ ] **Step 7: Merge PR**

Open PR: `docs/gmail-triage-design` (or renamed feature branch) → main. Title: `feat: Gmail auto-triage v2 (Portal /mail tab)`.

---

## Spec coverage review

| Spec section | Task(s) |
|---|---|
| Architecture §1 (GH Actions cron) | T17 |
| Architecture §2 (Python classifier) | T9 (imap), T10 (classify), T11 (sync), T12 (triage) |
| Architecture §3 (Worker + D1) | T1–T7 |
| Architecture §4 (Portal /mail) | T13–T16 |
| D1 schema | T2 |
| /mail/sync endpoint | T3 |
| /mail/list endpoint | T4 |
| /mail/trash endpoint | T6 |
| IMAP client (hand-rolled) | T5 |
| URL-key auth | T4 (authUser), T14 (resolveKey) |
| SYNC_SECRET auth | T3 |
| UI wireframe (3 sections + Delete + Open-in-Gmail) | T15 |
| Optimistic delete | T14 (useMail.deleteEmail), T15 (MailRow) |
| Classification prompt | T10 |
| Setup (8 steps) | T7 (D1, Worker secrets, deploy), T17 (GH Secrets + workflow) |
| Error handling | T6 (IMAP paths), T11 (sync errors), T14 (fetch errors) |
| Security (URL key constant-time compare) | T4 (authUser) |
| Retention (30-90d / UI shows 7d) | T2 (schema, no purge v1), T3 (db.ts SELECT) |

No spec gaps.

## Notes for executor

- **Order matters only loosely**: T1–T7 can run in parallel with T8–T12 (independent Python+Worker tracks). Frontend T13–T16 depends on Worker being reachable for dev-mode smoke tests but not for build.
- **`INSERT OR IGNORE` intentional**: once a user clicks Delete, `status='trashed'` must survive the next daily sync that sees the same Message-ID. Do not change to `INSERT OR REPLACE`.
- **CORS origin `*`** in Worker is acceptable for v1 because both `/mail/list` and `/mail/trash` are `USER_KEY`-gated. Tighten to the specific Portal domain if ever exposing unauthenticated routes.
- **Cron drift**: GitHub scheduled workflows may run up to ~15 min late during peak. UI shows "as of <timestamp>" so drift is visible.
- **Portal Pages env var**: `NEXT_PUBLIC_GMAIL_WORKER_URL` must be a build-time var because Next.js embeds it. If changing the Worker URL, redeploy the Pages project.
- **Frontend styling**: the Tailwind classes above are reasonable defaults but should be harmonized with Portal's existing card styles (look at `src/components/finance/metric-cards.tsx` for reference). Use existing UI primitives if any match (`src/components/ui/`).
