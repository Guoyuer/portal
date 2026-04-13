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
