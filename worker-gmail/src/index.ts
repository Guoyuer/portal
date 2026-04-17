import { upsertEmails, listActiveLast7Days, markTrashed } from "./db.js";
import type { Category, UpsertInput } from "./types.js";

// Mail-sync body validator — no Zod dep to keep the worker bundle small.
// Returns the validated rows on success, or a short error string on failure.
// (Don't echo the offending row back — avoids reflecting user-supplied data.)
const CATEGORIES: readonly Category[] = ["IMPORTANT", "NEUTRAL", "TRASH_CANDIDATE"];
const ISO_DATE_LIKE = /^\d{4}-\d{2}-\d{2}/;

function validateUpsertInput(x: unknown): UpsertInput | string {
  if (typeof x !== "object" || x === null) return "row not an object";
  const r = x as Record<string, unknown>;
  if (typeof r.msg_id !== "string" || !r.msg_id) return "msg_id missing";
  if (typeof r.received_at !== "string" || !ISO_DATE_LIKE.test(r.received_at)) return "received_at invalid";
  if (typeof r.classified_at !== "string" || !ISO_DATE_LIKE.test(r.classified_at)) return "classified_at invalid";
  if (typeof r.sender !== "string") return "sender missing";
  if (typeof r.subject !== "string") return "subject missing";
  if (typeof r.summary !== "string") return "summary missing";
  if (!CATEGORIES.includes(r.category as Category)) return "category invalid";
  return {
    msg_id: r.msg_id,
    received_at: r.received_at,
    classified_at: r.classified_at,
    sender: r.sender,
    subject: r.subject,
    summary: r.summary,
    category: r.category as Category,
  };
}

function validateSyncBody(body: unknown): UpsertInput[] | string {
  if (typeof body !== "object" || body === null) return "body not an object";
  const emails = (body as Record<string, unknown>).emails;
  if (!Array.isArray(emails)) return "emails array missing";
  const out: UpsertInput[] = [];
  for (const e of emails) {
    const parsed = validateUpsertInput(e);
    if (typeof parsed === "string") return parsed;
    out.push(parsed);
  }
  return out;
}
import { imapOk, parseSearchUid } from "./imap-parse.js";
import { connect } from "cloudflare:sockets";

// Browser paths (/api/mail/list, /api/mail/trash) arrive via the
// `portal.guoyuer.com/api/mail/*` zone route and are pre-authenticated by
// the existing Cloudflare Access application on portal.guoyuer.com. The
// Worker trusts Access — no in-Worker user auth is needed for those paths.
//
// `/mail/sync` arrives via `portal-mail.guoyuer.com` (no Access in front)
// and keeps its shared-secret check so the GH Actions cron can reach it.
interface Env {
  DB: D1Database;
  SYNC_SECRET: string;
  SMTP_USER: string;
  SMTP_PASSWORD: string;
}

// ── IMAP client (Gmail-specific, minimal) ────────────────────────────────────

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
  const re = new RegExp(`^${tag} (OK|NO|BAD)[^\\r\\n]*\\r?\\n`, "m");
  let buf = "";
  while (Date.now() < deadline) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    if (re.test(buf)) return buf;
  }
  throw new Error("imap read timeout");
}

export async function imapTrashMessage(
  user: string, password: string, msgId: string,
): Promise<TrashResult> {
  let socket: Socket | undefined;
  try {
    socket = connect(
      { hostname: "imap.gmail.com", port: 993 },
      { secureTransport: "on", allowHalfOpen: false },
    );
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
  } catch (err) {
    // Surfaces in `wrangler tail`. Credentials never included in the thrown
    // errors above (timeouts, not auth failures — those return "auth_failed").
    console.error("imap trash error:", err instanceof Error ? err.message : String(err));
    return "error";
  } finally {
    try { await socket?.close(); } catch {}
  }
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const pathname = url.pathname;

    // Route table uses the FULL incoming path — the two audiences live at
    // disjoint prefixes:
    //   /api/mail/list, /api/mail/trash   → browser on portal.guoyuer.com
    //                                       (pre-gated by the portal Access app)
    //   /mail/sync                        → GitHub Actions cron on
    //                                       portal-mail.guoyuer.com (SYNC_SECRET)
    // Bare /mail/list and /mail/trash no longer respond: they'd only be
    // reachable by hitting portal-mail.guoyuer.com directly, which this
    // Worker intentionally does not expose to user sessions.

    if (pathname === "/mail/sync" && request.method === "POST") {
      if (request.headers.get("X-Sync-Secret") !== env.SYNC_SECRET) {
        return Response.json({ error: "unauthorized" }, { status: 401 });
      }
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return Response.json({ error: "invalid json" }, { status: 400 });
      }
      const rows = validateSyncBody(body);
      if (typeof rows === "string") {
        return Response.json({ error: rows }, { status: 400 });
      }
      const result = await upsertEmails(env.DB, rows);
      return Response.json({ inserted: result.inserted, skipped_existing: result.skipped });
    }
    if (pathname === "/api/mail/list" && request.method === "GET") {
      const rows = await listActiveLast7Days(env.DB);
      // Cache-Control: no-store so the browser re-fetches fresh state after a
      // trash action (the optimistic UI rollback relies on a follow-up list).
      return Response.json(
        { emails: rows, as_of: new Date().toISOString() },
        { headers: { "Cache-Control": "no-store" } },
      );
    }
    if (pathname === "/api/mail/trash" && request.method === "POST") {
      let body: unknown;
      try {
        body = await request.json();
      } catch {
        return Response.json({ error: "invalid json" }, { status: 400 });
      }
      const msgId = (body as Record<string, unknown> | null)?.msg_id;
      if (typeof msgId !== "string" || !msgId) {
        return Response.json({ error: "missing msg_id" }, { status: 400 });
      }
      // Re-bind so subsequent uses stay typed as `string`
      const parsedMsgId: string = msgId;

      const result = await imapTrashMessage(env.SMTP_USER, env.SMTP_PASSWORD, parsedMsgId);

      if (result === "trashed") {
        await markTrashed(env.DB, parsedMsgId);
        return Response.json({ status: "trashed" });
      }
      if (result === "not_found") {
        // Email already gone from Gmail (user trashed elsewhere). Update D1 to match.
        await markTrashed(env.DB, parsedMsgId);
        return Response.json({ status: "already_gone" });
      }
      if (result === "auth_failed") {
        return Response.json({ status: "auth_failed" }, { status: 503 });
      }
      return Response.json({ status: "error" }, { status: 503 });
    }
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
