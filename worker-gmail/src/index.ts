import { upsertEmails, listActiveLast7Days, markTrashed } from "./db.js";
import type { UpsertInput } from "./types.js";
import { imapOk, parseSearchUid } from "./imap-parse.js";
import { cfAccessEmailMatches, type AuthEnv } from "../../src/lib/worker-auth";
import { connect } from "cloudflare:sockets";

interface Env extends AuthEnv {
  DB: D1Database;
  SYNC_SECRET: string;
  USER_KEY: string;
  SMTP_USER: string;
  SMTP_PASSWORD: string;
}

function authUser(request: Request, url: URL, env: Env): boolean {
  // Prod mode: trust the CF Access JWT header (verified by Access before
  // the request reaches us). `USER_KEY` becomes dead code once the dashboard
  // migration is complete; follow-up PR will remove it + the frontend
  // localStorage key path.
  if (env.REQUIRE_AUTH === "true") return cfAccessEmailMatches(request, env);
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

const ALLOWED_ORIGINS = new Set([
  "https://portal.guoyuer.com",
  "http://localhost:3000",
  "http://localhost:3100",
]);

function corsHeaders(origin: string | null): Headers {
  const h = new Headers();
  // Echo a specific allowed origin (not `*`) so the browser can include the
  // CF Access session cookie. Falls back to the production origin if the
  // incoming request lacks a recognised Origin header.
  const allowOrigin = origin && ALLOWED_ORIGINS.has(origin) ? origin : "https://portal.guoyuer.com";
  h.set("Access-Control-Allow-Origin", allowOrigin);
  h.set("Access-Control-Allow-Credentials", "true");
  h.set("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
  h.set("Access-Control-Allow-Headers", "Content-Type, X-Mail-Key");
  h.set("Vary", "Origin");
  // Prevent browser/CDN caching of user-specific classified mail
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
    const origin = request.headers.get("Origin");

    // CORS preflight — handle before any path-specific routing. Only the
    // user-facing /mail/list and /mail/trash routes expect cross-origin
    // browser calls; /mail/sync is server-to-server and wouldn't preflight
    // in practice. Responding 204 for any OPTIONS is harmless.
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (url.pathname === "/mail/sync" && request.method === "POST") {
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
    if (url.pathname === "/mail/list" && request.method === "GET") {
      if (!authUser(request, url, env)) {
        return Response.json({ error: "unauthorized" }, { status: 401, headers: corsHeaders(origin) });
      }
      const rows = await listActiveLast7Days(env.DB);
      return Response.json(
        { emails: rows, as_of: new Date().toISOString() },
        { headers: corsHeaders(origin) },
      );
    }
    if (url.pathname === "/mail/trash" && request.method === "POST") {
      if (!authUser(request, url, env)) {
        return Response.json({ error: "unauthorized" }, { status: 401, headers: corsHeaders(origin) });
      }
      let body: { msg_id?: string };
      try {
        body = await request.json();
      } catch {
        return Response.json({ error: "invalid json" }, { status: 400, headers: corsHeaders(origin) });
      }
      if (!body.msg_id) {
        return Response.json({ error: "missing msg_id" }, { status: 400, headers: corsHeaders(origin) });
      }

      const result = await imapTrashMessage(env.SMTP_USER, env.SMTP_PASSWORD, body.msg_id);

      if (result === "trashed") {
        await markTrashed(env.DB, body.msg_id);
        return Response.json({ status: "trashed" }, { headers: corsHeaders(origin) });
      }
      if (result === "not_found") {
        // Email already gone from Gmail (user trashed elsewhere). Update D1 to match.
        await markTrashed(env.DB, body.msg_id);
        return Response.json({ status: "already_gone" }, { headers: corsHeaders(origin) });
      }
      if (result === "auth_failed") {
        return Response.json({ status: "auth_failed" }, { status: 503, headers: corsHeaders(origin) });
      }
      return Response.json({ status: "error" }, { status: 503, headers: corsHeaders(origin) });
    }
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
