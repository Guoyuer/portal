import { upsertEmails, listActiveLast7Days } from "./db.js";
import type { UpsertInput } from "./types.js";

interface Env {
  DB: D1Database;
  SYNC_SECRET: string;
  USER_KEY: string;
  SMTP_USER: string;
  SMTP_PASSWORD: string;
}

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
  // Prevent browser/CDN caching of user-specific classified mail
  h.set("Cache-Control", "no-store");
  return h;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

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
        return Response.json({ error: "unauthorized" }, { status: 401, headers: corsHeaders() });
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
    if (url.pathname === "/mail/trash" && request.method === "POST") {
      return new Response("not implemented", { status: 501 });
    }
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
