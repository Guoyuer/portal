import { upsertEmails } from "./db.js";
import type { UpsertInput } from "./types.js";

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
      return new Response("not implemented", { status: 501 });
    }
    if (url.pathname === "/mail/trash" && request.method === "POST") {
      return new Response("not implemented", { status: 501 });
    }
    return new Response("Not found", { status: 404 });
  },
} satisfies ExportedHandler<Env>;
