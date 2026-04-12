# Gmail Auto-Triage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Gmail auto-triage system designed in `docs/gmail-triage-design-2026-04-12.md`: a GH Actions cron runs a Python script that reads unread Gmail (IMAP), classifies via Claude Haiku, and emails a digest back to the user. The digest contains signed links to a Cloudflare Worker that trashes emails on click via IMAP.

**Architecture:** One Gmail app password covers everything (Python SMTP+IMAP + Worker IMAP). Worker is a single-file TypeScript service with 3 routes: POST `/sign` (internal HMAC), GET `/trash` (confirm page), POST `/trash` (IMAP-trash). Python is a small orchestrator under `pipeline/scripts/gmail/` with thin, testable modules.

**Tech Stack:**
- Python 3.13, stdlib `imaplib`, `anthropic`, `httpx`
- Cloudflare Workers (TypeScript), Web Crypto for HMAC, `cloudflare:sockets` for IMAP
- GitHub Actions (cron schedule)
- Reuses `etl.email_report` (SMTP send) from main

**File structure** (all new):

```
worker-gmail/
├── src/index.ts          # all Worker code — single file, <400 LoC
├── wrangler.jsonc
├── package.json
└── tsconfig.json

pipeline/scripts/gmail/
├── __init__.py
├── triage.py             # CLI entry point
├── imap_client.py        # IMAP connect, search, fetch
├── classify.py           # Anthropic call + prompt
├── digest_html.py        # HTML + text rendering
├── worker_sign.py        # httpx POST /sign wrapper
├── requirements.txt
└── README.md

pipeline/tests/unit/gmail/
├── __init__.py
├── conftest.py
├── fixtures/             # sample raw emails (.eml fixtures)
├── test_imap_client.py
├── test_classify.py
├── test_digest_html.py
└── test_worker_sign.py

.github/workflows/
└── gmail-digest.yml
```

---

## Task 1: Worker — scaffold

**Files:**
- Create: `worker-gmail/package.json`
- Create: `worker-gmail/tsconfig.json`
- Create: `worker-gmail/wrangler.jsonc`
- Create: `worker-gmail/src/index.ts`

- [ ] **Step 1: Create `worker-gmail/package.json`**

```json
{
  "name": "worker-gmail",
  "version": "0.1.0",
  "private": true,
  "scripts": {
    "dev": "wrangler dev",
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
  "compatibility_flags": ["nodejs_compat"]
}
```

Secrets (`SIGNING_KEY`, `SIGNING_SECRET`, `SMTP_USER`, `SMTP_PASSWORD`) will be set via `wrangler secret put` at deploy time. No vars section needed.

- [ ] **Step 4: Create `worker-gmail/src/index.ts` skeleton**

```ts
interface Env {
  SIGNING_KEY: string;
  SIGNING_SECRET: string;
  SMTP_USER: string;
  SMTP_PASSWORD: string;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    if (url.pathname === "/sign" && request.method === "POST") {
      return new Response("not implemented", { status: 501 });
    }
    if (url.pathname === "/trash") {
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

Expected: no errors.

- [ ] **Step 6: Commit**

```bash
git add worker-gmail/
git commit -m "feat(gmail-triage): scaffold worker-gmail with 3 route stubs"
```

---

## Task 2: Worker — HMAC sign/verify

**Files:**
- Modify: `worker-gmail/src/index.ts` (add `signToken` / `verifyToken` functions)

Token format per spec: `base64url(msg_id + "|" + expiry_unix + "|" + HMAC_SHA256(key, msg_id + "|" + expiry_unix))`.

- [ ] **Step 1: Add HMAC helpers**

Insert before the default export:

```ts
// ── HMAC token helpers ───────────────────────────────────────────────────────

const enc = new TextEncoder();
const dec = new TextDecoder();

function base64urlEncode(bytes: Uint8Array): string {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

function base64urlDecode(s: string): Uint8Array {
  const pad = s.length % 4 === 0 ? "" : "=".repeat(4 - (s.length % 4));
  const bin = atob(s.replaceAll("-", "+").replaceAll("_", "/") + pad);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function hmacSha256(keyBytes: Uint8Array, data: Uint8Array): Promise<Uint8Array> {
  const key = await crypto.subtle.importKey(
    "raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const sig = await crypto.subtle.sign("HMAC", key, data);
  return new Uint8Array(sig);
}

function timingSafeEqual(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a[i] ^ b[i];
  return diff === 0;
}

async function signToken(msgId: string, expiryUnix: number, keyHex: string): Promise<string> {
  const keyBytes = hexToBytes(keyHex);
  const payload = `${msgId}|${expiryUnix}`;
  const sig = await hmacSha256(keyBytes, enc.encode(payload));
  const combined = enc.encode(payload + "|");
  const out = new Uint8Array(combined.length + sig.length);
  out.set(combined, 0);
  out.set(sig, combined.length);
  return base64urlEncode(out);
}

async function verifyToken(
  token: string, keyHex: string,
): Promise<{ msgId: string; expiryUnix: number } | null> {
  let bytes: Uint8Array;
  try {
    bytes = base64urlDecode(token);
  } catch {
    return null;
  }
  const text = dec.decode(bytes);
  const lastPipe = text.lastIndexOf("|");
  if (lastPipe < 0) return null;
  const payload = text.slice(0, lastPipe);
  const sigStart = enc.encode(payload + "|").length;
  const providedSig = bytes.slice(sigStart);

  const [msgId, expiryStr] = payload.split("|");
  if (!msgId || !expiryStr) return null;
  const expiryUnix = parseInt(expiryStr, 10);
  if (!Number.isFinite(expiryUnix)) return null;
  if (expiryUnix < Math.floor(Date.now() / 1000)) return null;

  const keyBytes = hexToBytes(keyHex);
  const expected = await hmacSha256(keyBytes, enc.encode(payload));
  if (!timingSafeEqual(providedSig, expected)) return null;

  return { msgId, expiryUnix };
}

function hexToBytes(hex: string): Uint8Array {
  const out = new Uint8Array(hex.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  return out;
}
```

- [ ] **Step 2: Typecheck**

```bash
cd worker-gmail
npx tsc --noEmit
```

Expected: no errors.

- [ ] **Step 3: Smoke test with `wrangler dev` in a tmp script**

Create temporary `worker-gmail/scratch/sign.ts` (not committed — just for local verification):

```ts
// Run: npx ts-node --esm scratch/sign.ts
// Or paste into the Worker's fetch handler temporarily and hit it with curl.
```

Skip formal test. The routes below exercise HMAC and will fail fast if broken.

- [ ] **Step 4: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): HMAC sign/verify helpers in Worker"
```

---

## Task 3: Worker — POST /sign route

**Files:**
- Modify: `worker-gmail/src/index.ts`

- [ ] **Step 1: Implement /sign handler**

Replace the `if (url.pathname === "/sign" && request.method === "POST")` branch:

```ts
    if (url.pathname === "/sign" && request.method === "POST") {
      if (request.headers.get("X-Signing-Secret") !== env.SIGNING_SECRET) {
        return new Response("unauthorized", { status: 401 });
      }
      let body: { msg_id?: string; expiry?: number };
      try {
        body = await request.json();
      } catch {
        return new Response("invalid json", { status: 400 });
      }
      if (!body.msg_id || typeof body.msg_id !== "string") {
        return new Response("missing msg_id", { status: 400 });
      }
      const expiry = body.expiry ?? Math.floor(Date.now() / 1000) + 7 * 86400;
      const token = await signToken(body.msg_id, expiry, env.SIGNING_KEY);
      return Response.json({ token });
    }
```

- [ ] **Step 2: Local smoke test with `wrangler dev`**

```bash
cd worker-gmail
npx wrangler dev --local --var SIGNING_KEY:$(openssl rand -hex 32) --var SIGNING_SECRET:test-secret --var SMTP_USER:x --var SMTP_PASSWORD:x
```

In another terminal:

```bash
# Missing header → 401
curl -i -X POST http://127.0.0.1:8787/sign -d '{"msg_id":"abc"}'

# Happy path → 200 with token
curl -i -X POST http://127.0.0.1:8787/sign \
  -H "X-Signing-Secret: test-secret" \
  -H "Content-Type: application/json" \
  -d '{"msg_id":"<abc@example.com>"}'
```

Expected: second curl returns `{"token":"..."}` with a ~80 char base64url token.

Stop `wrangler dev` with Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): POST /sign route with HMAC + shared-secret auth"
```

---

## Task 4: Worker — GET /trash confirm page

**Files:**
- Modify: `worker-gmail/src/index.ts`

- [ ] **Step 1: Add a helper to render HTML pages**

Before the default export:

```ts
function htmlPage(title: string, body: string, status = 200): Response {
  const html = `<!DOCTYPE html><html><head><meta charset="utf-8"><title>${title}</title>
<style>body{font-family:-apple-system,Segoe UI,sans-serif;max-width:480px;margin:6rem auto;padding:0 1rem;color:#222;line-height:1.5}
button,input[type=submit]{background:#2e7d32;color:white;border:0;padding:.6rem 1.2rem;font-size:16px;border-radius:6px;cursor:pointer}
.muted{color:#666;font-size:14px}</style></head><body>${body}</body></html>`;
  return new Response(html, { status, headers: { "content-type": "text/html; charset=utf-8" } });
}
```

- [ ] **Step 2: Replace `/trash` branch to handle GET**

Replace the `if (url.pathname === "/trash")` branch:

```ts
    if (url.pathname === "/trash") {
      const token = url.searchParams.get("t") ?? "";
      const verified = await verifyToken(token, env.SIGNING_KEY);

      if (request.method === "GET") {
        if (!verified) {
          return htmlPage("Invalid link", `<h2>Invalid or expired link</h2>
<p class="muted">This link is either tampered, corrupted, or older than 7 days.</p>`, 410);
        }
        const safeId = verified.msgId.replace(/[<>&"]/g, (c) =>
          ({ "<": "&lt;", ">": "&gt;", "&": "&amp;", '"': "&quot;" })[c]!);
        return htmlPage("Trash email?", `<h2>Trash this email?</h2>
<p class="muted">Message-ID: <code>${safeId}</code></p>
<p class="muted">Recoverable for 30 days in Gmail Trash.</p>
<form method="POST" action="/trash">
  <input type="hidden" name="t" value="${token.replace(/"/g, "&quot;")}">
  <input type="submit" value="Confirm trash">
</form>`);
      }

      if (request.method === "POST") {
        // Implemented in Task 6
        return new Response("not implemented", { status: 501 });
      }

      return new Response("method not allowed", { status: 405 });
    }
```

- [ ] **Step 3: Local smoke test**

```bash
cd worker-gmail
npx wrangler dev --local --var SIGNING_KEY:... --var SIGNING_SECRET:test-secret --var SMTP_USER:x --var SMTP_PASSWORD:x
```

Get a token:

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8787/sign \
  -H "X-Signing-Secret: test-secret" -H "Content-Type: application/json" \
  -d '{"msg_id":"<abc@example.com>"}' | jq -r .token)

curl -s "http://127.0.0.1:8787/trash?t=$TOKEN" | grep -o "Confirm trash"
# Expected: Confirm trash

curl -s "http://127.0.0.1:8787/trash?t=invalid" | grep -o "Invalid or expired"
# Expected: Invalid or expired
```

- [ ] **Step 4: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): GET /trash confirm page with HMAC verify"
```

---

## Task 5: Worker — hand-rolled IMAP client

**Files:**
- Modify: `worker-gmail/src/index.ts`

Gmail IMAP is a text protocol. For our narrow use (trash one message by Message-ID), we only need 4 tagged commands: LOGIN, SELECT INBOX, UID SEARCH HEADER, UID STORE.

- [ ] **Step 1: Add IMAP client function**

Before the default export:

```ts
// ── IMAP client (Gmail-specific, minimal) ────────────────────────────────────

import { connect, type Socket } from "cloudflare:sockets";

type TrashResult = "trashed" | "not_found" | "auth_failed" | "error";

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
    // Look for line starting with our tag followed by OK/NO/BAD
    const match = buf.match(new RegExp(`^${tag} (OK|NO|BAD)[^\\r\\n]*\\r?\\n`, "m"));
    if (match) return buf;
  }
  throw new Error("imap read timeout");
}

function imapLineIsOk(line: string, tag: string): boolean {
  return new RegExp(`^${tag} OK`, "m").test(line);
}

function parseSearchUid(response: string): string | null {
  // Matches "* SEARCH 12345\r\n" or "* SEARCH 12345 67890\r\n" — take the first UID.
  const m = response.match(/^\* SEARCH\s+(\d+)/m);
  return m ? m[1] : null;
}

async function imapTrashMessage(
  user: string, password: string, msgId: string,
): Promise<TrashResult> {
  let socket: Socket | undefined;
  try {
    socket = connect({ hostname: "imap.gmail.com", port: 993, secureTransport: "on", allowHalfOpen: false });
    const writer = socket.writable.getWriter();
    const reader = socket.readable.getReader();

    const send = async (line: string) => {
      await writer.write(enc.encode(line + "\r\n"));
    };

    // Read initial greeting (* OK Gimap ready ...)
    await readUntilFirstLine(reader);

    // A1 LOGIN
    // IMPORTANT: escape " and \ in password per IMAP spec (RFC 3501)
    const escUser = user.replaceAll("\\", "\\\\").replaceAll("\"", "\\\"");
    const escPwd = password.replaceAll("\\", "\\\\").replaceAll("\"", "\\\"");
    await send(`A1 LOGIN "${escUser}" "${escPwd}"`);
    const loginResp = await readUntilTag(reader, "A1");
    if (!imapLineIsOk(loginResp, "A1")) return "auth_failed";

    // A2 SELECT INBOX
    await send(`A2 SELECT INBOX`);
    const selResp = await readUntilTag(reader, "A2");
    if (!imapLineIsOk(selResp, "A2")) return "error";

    // A3 UID SEARCH HEADER Message-ID
    // Message-ID often already has angle brackets — quote as-is
    const escMsgId = msgId.replaceAll("\\", "\\\\").replaceAll("\"", "\\\"");
    await send(`A3 UID SEARCH HEADER "Message-ID" "${escMsgId}"`);
    const searchResp = await readUntilTag(reader, "A3");
    if (!imapLineIsOk(searchResp, "A3")) return "error";
    const uid = parseSearchUid(searchResp);
    if (!uid) return "not_found";

    // A4 UID STORE uid +X-GM-LABELS "\Trash"
    // Must escape the backslash in \Trash (so send `\\Trash` over the wire)
    await send(`A4 UID STORE ${uid} +X-GM-LABELS "\\\\Trash"`);
    const storeResp = await readUntilTag(reader, "A4");
    if (!imapLineIsOk(storeResp, "A4")) return "error";

    // A5 LOGOUT (best-effort)
    await send(`A5 LOGOUT`);
    try { await writer.close(); } catch {}

    return "trashed";
  } catch (e) {
    return "error";
  } finally {
    try { await socket?.close(); } catch {}
  }
}

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
```

- [ ] **Step 2: Typecheck**

```bash
cd worker-gmail
npx tsc --noEmit
```

Expected: no errors. The `cloudflare:sockets` import type may need the compat flag acknowledged in types; if missing, `@cloudflare/workers-types` version >= 4.20240909 has it.

- [ ] **Step 3: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): hand-rolled Gmail IMAP trash helper in Worker"
```

---

## Task 6: Worker — POST /trash (wire IMAP)

**Files:**
- Modify: `worker-gmail/src/index.ts`

- [ ] **Step 1: Replace the POST branch inside `/trash`**

Replace the `if (request.method === "POST") { ... return 501 }` block:

```ts
      if (request.method === "POST") {
        // Token arrives in application/x-www-form-urlencoded body (from the confirm page form)
        const formData = await request.formData();
        const postToken = formData.get("t");
        if (typeof postToken !== "string") {
          return htmlPage("Bad request", `<h2>Bad request</h2>`, 400);
        }
        const v = await verifyToken(postToken, env.SIGNING_KEY);
        if (!v) {
          return htmlPage("Invalid link", `<h2>Invalid or expired link</h2>`, 410);
        }

        const result = await imapTrashMessage(env.SMTP_USER, env.SMTP_PASSWORD, v.msgId);

        if (result === "trashed") {
          return htmlPage("Trashed", `<h2>✓ Trashed</h2>
<p class="muted">Recoverable for 30 days in Gmail Trash.</p>`);
        }
        if (result === "not_found") {
          return htmlPage("Already gone", `<h2>Already gone</h2>
<p class="muted">This message isn't in your inbox — you probably trashed it on another device.</p>`);
        }
        if (result === "auth_failed") {
          return htmlPage("Auth failed", `<h2>Gmail auth failed</h2>
<p class="muted">The app password may need to be regenerated.</p>`, 503);
        }
        return htmlPage("Temporary error", `<h2>Gmail unavailable</h2>
<p class="muted">Try again in a minute.</p>`, 503);
      }
```

- [ ] **Step 2: Deploy to a dev worker and test with a throwaway email**

```bash
cd worker-gmail
# Set real secrets (for an actual Gmail account)
npx wrangler secret put SIGNING_KEY      # paste: openssl rand -hex 32
npx wrangler secret put SIGNING_SECRET   # paste: any random string
npx wrangler secret put SMTP_USER        # paste: your.account@gmail.com
npx wrangler secret put SMTP_PASSWORD    # paste: the 16-char Gmail app password
npx wrangler deploy
```

Copy the deployed URL.

Send yourself a test email in Gmail, grab its `Message-ID` header (Gmail web: More → Show original → Message-ID line).

Get a signed token via the deployed Worker:

```bash
export WORKER_URL=https://worker-gmail.<account>.workers.dev
export SIGNING_SECRET=...   # same value you set

TOKEN=$(curl -s -X POST $WORKER_URL/sign \
  -H "X-Signing-Secret: $SIGNING_SECRET" \
  -H "Content-Type: application/json" \
  -d "{\"msg_id\":\"<paste-message-id-here>\"}" | jq -r .token)

echo "Confirm URL: $WORKER_URL/trash?t=$TOKEN"
```

Open the URL in a browser, click Confirm, verify the test email moves to Gmail Trash within 1–2 seconds.

- [ ] **Step 3: Commit**

```bash
git add worker-gmail/src/index.ts
git commit -m "feat(gmail-triage): POST /trash performs IMAP trash with graceful error pages"
```

---

## Task 7: Python — scaffold

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

Stdlib `imaplib`, `email`, `smtplib` cover everything else. `etl.email_report` is imported from the existing pipeline package and has no additional deps.

- [ ] **Step 3: Create `pipeline/scripts/gmail/README.md`**

```markdown
# Gmail Triage

Daily 07:00 local digest of unread Gmail, with important emails highlighted and
low-value emails listed with one-click delete links.

## Run locally (dry-run)

```bash
cd pipeline
.venv/Scripts/python.exe scripts/gmail/triage.py --digest --dry-run
```

Prints the HTML digest to stdout. No email is sent and no Worker calls are made.

## Env vars

See `docs/gmail-triage-design-2026-04-12.md` for the full list. For local
dry-runs you need only:
- `PORTAL_SMTP_USER`, `PORTAL_SMTP_PASSWORD` (for IMAP login)
- `ANTHROPIC_API_KEY` (classification)

## Deployment

Runs on GitHub Actions. See `.github/workflows/gmail-digest.yml`.
```

- [ ] **Step 4: Create `pipeline/tests/unit/gmail/conftest.py`**

```python
"""Shared fixtures for Gmail triage tests."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FakeEmail:
    """Lightweight stand-in for a parsed Gmail message."""
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

## Task 8: Python — IMAP client

**Files:**
- Create: `pipeline/scripts/gmail/imap_client.py`
- Create: `pipeline/tests/unit/gmail/test_imap_client.py`

- [ ] **Step 1: Write the failing test — `pipeline/tests/unit/gmail/test_imap_client.py`**

```python
"""Tests for IMAP client fetch/search wrappers.

We mock ``imaplib.IMAP4_SSL`` directly and verify the wrapper calls the
expected sequence of IMAP commands. Response parsing is tested against
real Gmail IMAP response shapes (RFC 3501 plus Gmail-specific extensions).
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gmail.imap_client import ImapConfig, fetch_unread_last_24h, parse_message


class TestFetchUnreadLast24h:
    @patch("imaplib.IMAP4_SSL")
    def test_happy_path(self, mock_imap_cls: MagicMock) -> None:
        m = MagicMock()
        mock_imap_cls.return_value = m
        m.login.return_value = ("OK", [b"LOGIN ok"])
        m.select.return_value = ("OK", [b"42"])
        m.uid.side_effect = [
            ("OK", [b"1 2 3"]),  # SEARCH returns 3 UIDs
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
        m.uid.return_value = ("OK", [b""])  # SEARCH returns nothing
        cfg = ImapConfig(user="me@gmail.com", password="pw")
        emails = fetch_unread_last_24h(cfg)
        assert emails == []


class TestParseMessage:
    def test_extracts_core_fields(self) -> None:
        raw = (
            b"From: Foo <foo@example.com>\r\n"
            b"Subject: Test Subject\r\n"
            b"Message-ID: <abc123@example.com>\r\n"
            b"\r\n"
            b"Hello world. This is the body."
        )
        msg = parse_message(raw)
        assert msg.msg_id == "<abc123@example.com>"
        assert msg.sender == "Foo <foo@example.com>"
        assert msg.subject == "Test Subject"
        assert "Hello world" in msg.body_excerpt

    def test_handles_missing_subject(self) -> None:
        raw = b"From: x@y\r\nMessage-ID: <m@y>\r\n\r\nbody"
        msg = parse_message(raw)
        assert msg.subject == ""
```

- [ ] **Step 2: Run test — expect failure**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_imap_client.py -v
```

Expected: `ModuleNotFoundError: No module named 'gmail.imap_client'`.

- [ ] **Step 3: Implement `pipeline/scripts/gmail/imap_client.py`**

```python
"""Minimal Gmail IMAP client: connect, login, search unread last 24h, fetch.

Stdlib imaplib + email. Returns plain dataclasses so downstream modules don't
depend on imaplib's awkward response shapes.
"""
from __future__ import annotations

import email
import email.policy
import imaplib
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class ImapConfig:
    user: str
    password: str
    host: str = "imap.gmail.com"
    port: int = 993


@dataclass(frozen=True)
class ParsedMessage:
    msg_id: str          # Message-ID header with angle brackets
    sender: str          # raw From: value
    subject: str
    body_excerpt: str    # first ~500 chars of text body


def _imap_date(d: date) -> str:
    """Format a date the way IMAP SEARCH expects (e.g. '12-Apr-2026')."""
    return d.strftime("%d-%b-%Y")


def fetch_unread_last_24h(config: ImapConfig) -> list[ParsedMessage]:
    """Return unread INBOX messages received since yesterday (SINCE is day-granular)."""
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
    """Parse a raw RFC 5322 message into a ParsedMessage."""
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    msg_id = (msg["Message-ID"] or "").strip()
    sender = (msg["From"] or "").strip()
    subject = (msg["Subject"] or "").strip()

    # Extract text/plain body; fall back to stripped HTML
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
    return ParsedMessage(msg_id=msg_id, sender=sender, subject=subject, body_excerpt=excerpt)
```

- [ ] **Step 4: Run test — expect pass**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_imap_client.py -v
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/gmail/imap_client.py pipeline/tests/unit/gmail/test_imap_client.py
git commit -m "feat(gmail-triage): IMAP fetch + MIME parse helpers with tests"
```

---

## Task 9: Python — classify

**Files:**
- Create: `pipeline/scripts/gmail/classify.py`
- Create: `pipeline/tests/unit/gmail/test_classify.py`

- [ ] **Step 1: Write the failing test — `pipeline/tests/unit/gmail/test_classify.py`**

```python
"""Tests for Anthropic classification call and response parsing."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from gmail.classify import Category, classify_emails
from gmail.imap_client import ParsedMessage


def _msg(msg_id: str, subject: str) -> ParsedMessage:
    return ParsedMessage(msg_id=msg_id, sender="x@example.com", subject=subject, body_excerpt="")


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

        emails = [_msg("<1>", "Stripe role"), _msg("<2>", "Sale!"), _msg("<3>", "Slack ping")]
        result = classify_emails(emails, api_key="sk-test")

        assert result["<1>"].category == Category.IMPORTANT
        assert result["<2>"].category == Category.TRASH_CANDIDATE
        assert result["<3>"].category == Category.NEUTRAL
        assert "recruiter" in result["<1>"].summary

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
        emails = [_msg("<1>", "Something")]
        result = classify_emails(emails, api_key="sk-test")
        # On unparseable response every email falls back to NEUTRAL
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

Takes a list of ParsedMessage and returns per-msg_id classification with a
one-sentence summary. Fails open: on any Anthropic error or unparseable
response, every email falls back to NEUTRAL with a note so the digest still
ships.
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

Few-shot examples (treat as the user's taste calibration):
  "Software Engineer role at Stripe — competitive comp"
    → IMPORTANT (recruiter)
  "Your statement is ready — Chase Freedom"
    → IMPORTANT (bill action)
  "Notion's weekly digest: 5 pages you haven't opened"
    → TRASH_CANDIDATE (marketing, no action)
  "Security alert: sign-in from Chrome on Windows"
    → TRASH_CANDIDATE (routine, user's own device)
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
    """Classify a batch of emails. Returns {msg_id: Classification}."""
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

    # Emails with no result → NEUTRAL fallback
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
git commit -m "feat(gmail-triage): Claude Haiku classifier with fail-open JSON parsing"
```

---

## Task 10: Python — Worker sign wrapper

**Files:**
- Create: `pipeline/scripts/gmail/worker_sign.py`
- Create: `pipeline/tests/unit/gmail/test_worker_sign.py`

- [ ] **Step 1: Write test — `pipeline/tests/unit/gmail/test_worker_sign.py`**

```python
"""Tests for Worker /sign HTTP wrapper."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from gmail.worker_sign import WorkerSignClient, WorkerUnavailable


class TestWorkerSignClient:
    @patch("gmail.worker_sign.httpx.Client")
    def test_returns_token_on_200(self, mock_cls: MagicMock) -> None:
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.return_value = MagicMock(
            status_code=200,
            json=MagicMock(return_value={"token": "abc123"}),
        )
        c = WorkerSignClient(base_url="https://w.example", secret="s")
        token = c.sign("<msg@id>")
        assert token == "abc123"

        call_args = client.post.call_args
        assert call_args.args[0] == "https://w.example/sign"
        assert call_args.kwargs["headers"]["X-Signing-Secret"] == "s"
        assert call_args.kwargs["json"]["msg_id"] == "<msg@id>"

    @patch("gmail.worker_sign.httpx.Client")
    def test_raises_on_non_200(self, mock_cls: MagicMock) -> None:
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.return_value = MagicMock(status_code=401, text="unauthorized")

        c = WorkerSignClient(base_url="https://w.example", secret="wrong")
        try:
            c.sign("<msg@id>")
        except WorkerUnavailable as e:
            assert "401" in str(e)
        else:
            raise AssertionError("expected WorkerUnavailable")

    @patch("gmail.worker_sign.httpx.Client")
    def test_raises_on_network_error(self, mock_cls: MagicMock) -> None:
        import httpx
        client = MagicMock()
        mock_cls.return_value.__enter__.return_value = client
        client.post.side_effect = httpx.ConnectError("dns failed")

        c = WorkerSignClient(base_url="https://w.example", secret="s")
        try:
            c.sign("<msg@id>")
        except WorkerUnavailable:
            pass
        else:
            raise AssertionError("expected WorkerUnavailable")
```

- [ ] **Step 2: Run — expect failure**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_worker_sign.py -v
```

- [ ] **Step 3: Implement `pipeline/scripts/gmail/worker_sign.py`**

```python
"""Thin HTTP wrapper around Worker POST /sign.

Raises WorkerUnavailable on any non-200 response or network error. Callers
decide whether to fail the whole digest or fall back to omitting trash links.
"""
from __future__ import annotations

import httpx


class WorkerUnavailable(RuntimeError):
    """Raised when the signing Worker is unreachable or rejects the call."""


class WorkerSignClient:
    def __init__(self, *, base_url: str, secret: str, timeout: float = 10.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._secret = secret
        self._timeout = timeout

    def sign(self, msg_id: str) -> str:
        """Return a signed token for the given Message-ID."""
        try:
            with httpx.Client(timeout=self._timeout) as client:
                r = client.post(
                    f"{self._base_url}/sign",
                    headers={"X-Signing-Secret": self._secret, "Content-Type": "application/json"},
                    json={"msg_id": msg_id},
                )
        except httpx.HTTPError as e:
            raise WorkerUnavailable(f"network error: {e}") from e

        if r.status_code != 200:
            raise WorkerUnavailable(f"sign returned {r.status_code}: {r.text[:200]}")

        data = r.json()
        token = data.get("token")
        if not isinstance(token, str):
            raise WorkerUnavailable(f"sign returned invalid body: {data!r}")
        return token
```

- [ ] **Step 4: Run — expect pass**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_worker_sign.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/gmail/worker_sign.py pipeline/tests/unit/gmail/test_worker_sign.py
git commit -m "feat(gmail-triage): httpx wrapper for Worker POST /sign"
```

---

## Task 11: Python — digest HTML renderer

**Files:**
- Create: `pipeline/scripts/gmail/digest_html.py`
- Create: `pipeline/tests/unit/gmail/test_digest_html.py`

- [ ] **Step 1: Write test — `pipeline/tests/unit/gmail/test_digest_html.py`**

```python
"""Golden snapshot tests for digest HTML + plain-text rendering."""
from __future__ import annotations

from datetime import date

from gmail.classify import Category, Classification
from gmail.digest_html import render_digest
from gmail.imap_client import ParsedMessage


def _msg(msg_id: str, sender: str, subject: str) -> ParsedMessage:
    return ParsedMessage(msg_id=msg_id, sender=sender, subject=subject, body_excerpt="")


class TestRenderDigest:
    def test_empty_digest(self) -> None:
        html, text = render_digest(
            emails=[], classifications={}, trash_tokens={},
            worker_url="https://w.example", as_of=date(2026, 4, 12),
        )
        assert "IMPORTANT (0)" in text
        assert "SUGGESTED TRASH (0)" in text
        assert "<pre" in html

    def test_single_important_email(self) -> None:
        emails = [_msg("<a@x>", "recruiter@co", "Role at Stripe")]
        cls = {"<a@x>": Classification(Category.IMPORTANT, "Stripe role, $300k")}
        html, text = render_digest(
            emails=emails, classifications=cls, trash_tokens={},
            worker_url="https://w.example", as_of=date(2026, 4, 12),
        )
        assert "IMPORTANT (1)" in text
        assert "Role at Stripe" in text
        assert "Stripe role, $300k" in text
        assert "https://mail.google.com/mail/u/0/#inbox/" in html
        assert "SUGGESTED TRASH (0)" in text

    def test_trash_candidate_has_link(self) -> None:
        emails = [_msg("<b@x>", "linkedin", "People you may know")]
        cls = {"<b@x>": Classification(Category.TRASH_CANDIDATE, "LI spam")}
        tokens = {"<b@x>": "tok123"}
        html, text = render_digest(
            emails=emails, classifications=cls, trash_tokens=tokens,
            worker_url="https://w.example", as_of=date(2026, 4, 12),
        )
        assert "SUGGESTED TRASH (1)" in text
        assert "https://w.example/trash?t=tok123" in html
        # Plain-text fallback strips the URL
        assert "https://w.example/trash?t=tok123" not in text

    def test_html_escapes_subject(self) -> None:
        emails = [_msg("<c@x>", "x@y", "<script>alert(1)</script>")]
        cls = {"<c@x>": Classification(Category.NEUTRAL, "code snippet")}
        html, _ = render_digest(
            emails=emails, classifications=cls, trash_tokens={},
            worker_url="https://w.example", as_of=date(2026, 4, 12),
        )
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
```

- [ ] **Step 2: Run — expect failure**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_digest_html.py -v
```

- [ ] **Step 3: Implement `pipeline/scripts/gmail/digest_html.py`**

```python
"""Render the triage digest in HTML + plain-text.

Mirrors the <pre>-based pattern from etl.changelog: plain-text body wrapped
in a single <pre> inside a minimal <html> skeleton with inline CSS. Links use
<a> tags (work inside <pre>).
"""
from __future__ import annotations

from datetime import date
from html import escape

from gmail.classify import Category, Classification
from gmail.imap_client import ParsedMessage


def _bucket(
    emails: list[ParsedMessage], classifications: dict[str, Classification], category: Category,
) -> list[tuple[ParsedMessage, Classification]]:
    return [(e, classifications[e.msg_id]) for e in emails if classifications.get(e.msg_id) and classifications[e.msg_id].category == category]


def render_digest(
    *,
    emails: list[ParsedMessage],
    classifications: dict[str, Classification],
    trash_tokens: dict[str, str],
    worker_url: str,
    as_of: date,
) -> tuple[str, str]:
    """Return (html, text). Text is the plain-text fallback (same body, links stripped)."""
    important = _bucket(emails, classifications, Category.IMPORTANT)
    neutral = _bucket(emails, classifications, Category.NEUTRAL)
    trash = _bucket(emails, classifications, Category.TRASH_CANDIDATE)

    date_str = as_of.strftime("%a %b %d")

    # Plain-text body (no links at all — the HTML version layers hrefs on top of this shape)
    text_lines: list[str] = []
    text_lines.append(f"Gmail Triage — {date_str}")
    text_lines.append("")
    text_lines.append(f"IMPORTANT ({len(important)})")
    text_lines.append("─" * 14)
    for e, c in important:
        text_lines.append(f"▸ {e.sender}  {e.subject}")
        if c.summary:
            text_lines.append(f"  {c.summary}")
        text_lines.append("")
    text_lines.append(f"OTHER ({len(neutral)})")
    text_lines.append("─" * 10)
    for e, _ in neutral:
        text_lines.append(f"• {e.sender}  —  {e.subject}")
    text_lines.append("")
    text_lines.append(f"SUGGESTED TRASH ({len(trash)})")
    text_lines.append("─" * 20)
    for e, c in trash:
        text_lines.append(f"☐ {e.sender}  {e.subject}")
        if c.summary:
            text_lines.append(f"  {c.summary}")
    text = "\n".join(text_lines)

    # HTML version: same shape, but with <a> tags inside a single <pre> block
    html_lines: list[str] = []
    html_lines.append(f"Gmail Triage — {escape(date_str)}")
    html_lines.append("")
    html_lines.append(f"IMPORTANT ({len(important)})")
    html_lines.append("─" * 14)
    for e, c in important:
        gmail_link = f'https://mail.google.com/mail/u/0/#inbox/{escape(e.msg_id)}'
        html_lines.append(f"▸ {escape(e.sender)}  {escape(e.subject)}")
        if c.summary:
            html_lines.append(f"  {escape(c.summary)}")
        html_lines.append(f'  <a href="{gmail_link}">Open in Gmail</a>')
        html_lines.append("")
    html_lines.append(f"OTHER ({len(neutral)})")
    html_lines.append("─" * 10)
    for e, _ in neutral:
        html_lines.append(f"• {escape(e.sender)}  —  {escape(e.subject)}")
    html_lines.append("")
    html_lines.append(f"SUGGESTED TRASH ({len(trash)})")
    html_lines.append("─" * 20)
    for e, c in trash:
        tok = trash_tokens.get(e.msg_id, "")
        delete_link = f'{escape(worker_url)}/trash?t={escape(tok)}' if tok else ""
        suffix = f'  <a href="{delete_link}">Delete</a>' if tok else ""
        html_lines.append(f"☐ {escape(e.sender)}  {escape(e.subject)}{suffix}")
        if c.summary:
            html_lines.append(f"  {escape(c.summary)}")

    body = "\n".join(html_lines)
    html = (
        "<html><body style=\"font-family:-apple-system,Segoe UI,sans-serif;color:#222\">"
        f"<h2 style=\"margin-bottom:8px\">Gmail Triage — {escape(date_str)}</h2>"
        "<pre style=\"font-family:Consolas,Menlo,monospace;font-size:13px;"
        "background:#f6f8fa;padding:14px 16px;border-radius:6px;"
        "white-space:pre-wrap;line-height:1.45\">"
        f"{body}"
        "</pre></body></html>"
    )
    return html, text


def build_subject(as_of: date, important_count: int, trash_count: int) -> str:
    return f"📬 Gmail Triage — {as_of.strftime('%b %d')} ({important_count} important, {trash_count} trash)"
```

- [ ] **Step 4: Run — expect pass**

```bash
cd pipeline
.venv/Scripts/pytest.exe tests/unit/gmail/test_digest_html.py -v
```

- [ ] **Step 5: Commit**

```bash
git add pipeline/scripts/gmail/digest_html.py pipeline/tests/unit/gmail/test_digest_html.py
git commit -m "feat(gmail-triage): HTML + plain-text digest renderer with escape tests"
```

---

## Task 12: Python — triage orchestrator (CLI)

**Files:**
- Create: `pipeline/scripts/gmail/triage.py`

This is the entry point wired into GH Actions. It orchestrates the modules we just built. No new units — just plumbing. Tested end-to-end via `--dry-run`.

- [ ] **Step 1: Implement `pipeline/scripts/gmail/triage.py`**

```python
"""Gmail triage CLI entry point.

    python scripts/gmail/triage.py --digest           # normal run: send digest email
    python scripts/gmail/triage.py --digest --dry-run # print HTML to stdout, skip Worker + send

Env vars (all required unless --dry-run):
    PORTAL_SMTP_USER, PORTAL_SMTP_PASSWORD
    PORTAL_GMAIL_WORKER_URL, PORTAL_GMAIL_WORKER_SECRET
    PORTAL_GMAIL_TOKEN_SIGNING_KEY  (not used here — the Worker signs; we only POST)
    ANTHROPIC_API_KEY
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date
from pathlib import Path

# Add pipeline/ (for `etl`) and pipeline/scripts/ (for `gmail`) to sys.path.
# Running `python scripts/gmail/triage.py` only puts scripts/gmail/ on sys.path
# by default — neither etl nor the gmail package itself is importable without help.
_pipeline_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_pipeline_dir))
sys.path.insert(0, str(_pipeline_dir / "scripts"))

from etl.email_report import EmailConfig, send as smtp_send  # noqa: E402
from gmail.classify import Category, classify_emails  # noqa: E402
from gmail.digest_html import build_subject, render_digest  # noqa: E402
from gmail.imap_client import ImapConfig, fetch_unread_last_24h  # noqa: E402
from gmail.worker_sign import WorkerSignClient, WorkerUnavailable  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("gmail.triage")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Gmail triage digest")
    p.add_argument("--digest", action="store_true", help="Fetch, classify, send digest")
    p.add_argument("--dry-run", action="store_true", help="Print digest HTML to stdout, skip Worker + send")
    return p.parse_args(argv)


def _require_env(name: str) -> str:
    v = os.environ.get(name)
    if not v:
        print(f"error: missing env var {name}", file=sys.stderr)
        sys.exit(1)
    return v


def run_digest(dry_run: bool) -> int:
    smtp_user = _require_env("PORTAL_SMTP_USER")
    smtp_password = _require_env("PORTAL_SMTP_PASSWORD")
    anthropic_key = _require_env("ANTHROPIC_API_KEY")

    log.info("fetching unread emails from last 24h...")
    emails = fetch_unread_last_24h(ImapConfig(user=smtp_user, password=smtp_password))
    log.info("fetched %d emails", len(emails))

    log.info("classifying...")
    classifications = classify_emails(emails, api_key=anthropic_key)

    trash_candidates = [e for e in emails if classifications.get(e.msg_id, None) and classifications[e.msg_id].category == Category.TRASH_CANDIDATE]
    log.info("trash candidates: %d", len(trash_candidates))

    # Get signed tokens for trash candidates
    trash_tokens: dict[str, str] = {}
    if trash_candidates and not dry_run:
        worker_url = _require_env("PORTAL_GMAIL_WORKER_URL")
        worker_secret = _require_env("PORTAL_GMAIL_WORKER_SECRET")
        client = WorkerSignClient(base_url=worker_url, secret=worker_secret)
        for e in trash_candidates:
            try:
                trash_tokens[e.msg_id] = client.sign(e.msg_id)
            except WorkerUnavailable as err:
                log.warning("sign failed for %s: %s — dropping trash link", e.msg_id, err)

    worker_url_for_html = os.environ.get("PORTAL_GMAIL_WORKER_URL", "https://worker-gmail.invalid")
    html, text = render_digest(
        emails=emails, classifications=classifications, trash_tokens=trash_tokens,
        worker_url=worker_url_for_html, as_of=date.today(),
    )

    important_count = sum(1 for c in classifications.values() if c.category == Category.IMPORTANT)
    subject = build_subject(date.today(), important_count, len(trash_tokens))

    if dry_run:
        print("=== SUBJECT ===")
        print(subject)
        print("=== HTML ===")
        print(html)
        print("=== TEXT ===")
        print(text)
        return 0

    cfg = EmailConfig.from_env()
    if cfg is None:
        print("error: PORTAL_SMTP_USER / PORTAL_SMTP_PASSWORD not set for send", file=sys.stderr)
        return 1
    log.info("sending digest email...")
    smtp_send(subject, html, text, cfg)
    log.info("done")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.digest:
        return run_digest(dry_run=args.dry_run)
    print("usage: triage.py --digest [--dry-run]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Dry-run smoke test (no real emails needed — just a failing IMAP login is acceptable)**

```bash
cd pipeline
# With fake credentials to test the argparse + import path only:
PORTAL_SMTP_USER=x@gmail.com PORTAL_SMTP_PASSWORD=x ANTHROPIC_API_KEY=sk-ant-fake \
  .venv/Scripts/python.exe scripts/gmail/triage.py --digest --dry-run
```

Expected: the script runs, attempts IMAP login, fails on fake credentials. Error should surface clearly. (For a true end-to-end dry run, use real credentials in your env and expect a live digest HTML on stdout.)

- [ ] **Step 3: Full end-to-end dry-run with real credentials**

Requires `PORTAL_SMTP_USER`, `PORTAL_SMTP_PASSWORD`, `ANTHROPIC_API_KEY` in shell.

```bash
cd pipeline
.venv/Scripts/python.exe scripts/gmail/triage.py --digest --dry-run > /tmp/digest.html
# Inspect /tmp/digest.html to verify classifications and rendering
```

- [ ] **Step 4: Commit**

```bash
git add pipeline/scripts/gmail/triage.py
git commit -m "feat(gmail-triage): CLI orchestrator wiring fetch→classify→sign→render→send"
```

---

## Task 13: GitHub Actions workflow

**Files:**
- Create: `.github/workflows/gmail-digest.yml`

- [ ] **Step 1: Create the workflow**

```yaml
name: Gmail Triage Digest
on:
  schedule:
    - cron: "0 22 * * *"     # 22:00 UTC = 07:00 +08 (no DST)
  workflow_dispatch: {}

jobs:
  digest:
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
        run: python scripts/gmail/triage.py --digest
        env:
          PORTAL_SMTP_USER:               ${{ secrets.PORTAL_SMTP_USER }}
          PORTAL_SMTP_PASSWORD:           ${{ secrets.PORTAL_SMTP_PASSWORD }}
          PORTAL_GMAIL_WORKER_URL:        ${{ secrets.PORTAL_GMAIL_WORKER_URL }}
          PORTAL_GMAIL_WORKER_SECRET:     ${{ secrets.PORTAL_GMAIL_WORKER_SECRET }}
          PORTAL_GMAIL_TOKEN_SIGNING_KEY: ${{ secrets.PORTAL_GMAIL_TOKEN_SIGNING_KEY }}
          ANTHROPIC_API_KEY:              ${{ secrets.ANTHROPIC_API_KEY }}
```

- [ ] **Step 2: Add the 6 repo secrets via GitHub UI**

Visit `https://github.com/Guoyuer/portal/settings/secrets/actions` → New repository secret, for each of:

- `PORTAL_SMTP_USER`
- `PORTAL_SMTP_PASSWORD`
- `PORTAL_GMAIL_WORKER_URL` (e.g. `https://worker-gmail.<account>.workers.dev`)
- `PORTAL_GMAIL_WORKER_SECRET` (same value as the Worker's `SIGNING_SECRET`)
- `PORTAL_GMAIL_TOKEN_SIGNING_KEY` (not currently used on Python side; still set for future)
- `ANTHROPIC_API_KEY`

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/gmail-digest.yml
git commit -m "feat(gmail-triage): GitHub Actions workflow (daily 07:00 +08)"
```

- [ ] **Step 4: Manually trigger the workflow via `gh workflow run gmail-digest.yml`**

After push, run:

```bash
git push
gh workflow run gmail-digest.yml
gh run watch
```

Expected: job succeeds, and your own Gmail receives a digest within ~1 min.

---

## Task 14: Final verification

**Files:** none (manual verification)

- [ ] **Step 1: Click a Delete link in the digest**

Open the digest email in Gmail, click a "Delete" link next to a TRASH_CANDIDATE, click Confirm. Verify the message moves to Trash within 1–2 seconds.

- [ ] **Step 2: Click an Open in Gmail link**

Verify the deep link opens the target email in Gmail.

- [ ] **Step 3: Verify confirm-page tamper detection**

Manually edit the URL (`?t=...Z` → `?t=...X`) and load it. Expected: "Invalid or expired link" page.

- [ ] **Step 4: Run full Python test suite to make sure nothing regressed**

```bash
cd pipeline
.venv/Scripts/pytest.exe -q
```

Expected: all tests pass.

- [ ] **Step 5: Ruff + mypy**

```bash
cd pipeline
.venv/Scripts/ruff.exe check scripts/gmail tests/unit/gmail
.venv/Scripts/mypy.exe scripts/gmail --ignore-missing-imports
```

Expected: clean.

- [ ] **Step 6: Merge PR**

Open a PR from `docs/gmail-triage-design` (or the feature branch this plan produces) → main. Title: `feat: Gmail auto-triage system`.

---

## Spec coverage review

| Spec section | Task(s) |
|---|---|
| Data flow §1 (digest generation) | T8 (IMAP), T9 (classify), T10 (sign), T11 (render), T12 (orchestrate), T13 (cron) |
| Data flow §2 (click → trash) | T3 (/sign), T4 (GET /trash), T5 (IMAP client), T6 (POST /trash) |
| Token format (HMAC + 7d expiry) | T2 |
| Classification prompt + few-shot | T9 |
| Digest HTML (`<pre>`-based) | T11 |
| Setup — 7 steps | T7 (README), T13 (workflow + secrets), T6 (Worker deploy) |
| Error handling (9 rows) | T9 (fallback), T10 (WorkerUnavailable), T6 (Worker error pages) |
| Security (7 rows) | T3 (header auth), T4 (GET confirm), T6 (trash only, no EXPUNGE), T2 (HMAC timing-safe compare) |
| Testing | T8, T9, T10, T11 tests; T3–T6 curl smoke tests |
| Reuse etl.email_report + changelog HTML pattern | T11 (matches <pre> pattern), T12 (imports `send`) |

No spec gaps.

## Notes for executor

- **HMAC key format**: the Worker expects a hex string in `SIGNING_KEY`. Generate with `openssl rand -hex 32`. If you later decide to use raw bytes or base64 instead, update both `hexToBytes` call in `signToken`/`verifyToken` AND the `wrangler secret put` value.
- **Gmail IMAP rate limit**: Gmail allows ~10 IMAP connections per account. Our usage is 1 per Worker `/trash` click + 1 per daily digest — no concern.
- **Workers CPU budget**: the IMAP client reads text responses in chunks via a ReadableStream. CPU for protocol parsing should stay <10 ms. If deploy + test reveals CPU overages, the mitigation is to reduce the `readUntilTag` buffer size or skip optional response validation (e.g. trust that A2 SELECT succeeded if no `* BYE` seen).
- **Cron drift**: GitHub Actions scheduled workflows can run up to ~15 min late during peak. Success criteria account for this.
