import { upsertEmails, listActiveLast7Days, markTrashed } from "./db.js";
import type { UpsertInput } from "./types.js";
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

function corsHeaders(): Headers {
  const h = new Headers();
  // Same origin after the migration — the browser does not need CORS to talk
  // to /api/mail/* on portal.guoyuer.com. These headers remain as a no-op
  // safety net (and still apply on the portal-mail.guoyuer.com sync path,
  // which is server-to-server and ignores them anyway).
  h.set("Cache-Control", "no-store");
  return h;
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
      let body: { classified_at?: string; emails?: UpsertInput[] };
      try {
        body = await request.json();
      } catch {
        return Response.json({ error: "invalid json" }, { status: 400 });
      }
      if (!Array.isArray(body.emails)) {
        return Response.json({ error: "missing emails array" }, { status: 400 });
      }
      // Minimal validation — reject rows missing required fields. Full schema
      // enforcement is done client-side in Python before POST. Do not echo the
      // row back in the error (avoid reflecting user-supplied data).
      for (const e of body.emails) {
        if (!e.msg_id || !e.sender || !e.category || !e.received_at) {
          return Response.json({ error: "invalid email row" }, { status: 400 });
        }
      }
      const result = await upsertEmails(env.DB, body.emails);
      return Response.json({ inserted: result.inserted, skipped_existing: result.skipped });
    }
    if (pathname === "/api/mail/list" && request.method === "GET") {
      const rows = await listActiveLast7Days(env.DB);
      return Response.json(
        { emails: rows, as_of: new Date().toISOString() },
        { headers: corsHeaders() },
      );
    }
    if (pathname === "/api/mail/trash" && request.method === "POST") {
      let body: { msg_id?: string };
      try {
        body = await request.json();
      } catch {
        return Response.json({ error: "invalid json" }, { status: 400, headers: corsHeaders() });
      }
      if (!body.msg_id) {
        return Response.json({ error: "missing msg_id" }, { status: 400, headers: corsHeaders() });
      }

      const result = await imapTrashMessage(env.SMTP_USER, env.SMTP_PASSWORD, body.msg_id);

      if (result === "trashed") {
        await markTrashed(env.DB, body.msg_id);
        return Response.json({ status: "trashed" }, { headers: corsHeaders() });
      }
      if (result === "not_found") {
        // Email already gone from Gmail (user trashed elsewhere). Update D1 to match.
        await markTrashed(env.DB, body.msg_id);
        return Response.json({ status: "already_gone" }, { headers: corsHeaders() });
      }
      if (result === "auth_failed") {
        return Response.json({ status: "auth_failed" }, { status: 503, headers: corsHeaders() });
      }
      return Response.json({ status: "error" }, { status: 503, headers: corsHeaders() });
    }
    return new Response("Not found", { status: 404, headers: corsHeaders() });
  },
} satisfies ExportedHandler<Env>;
