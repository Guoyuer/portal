// ── Worker utility helpers ───────────────────────────────────────────────
// Pure helpers used by the main Worker entrypoint. Kept separate so tests
// can import without pulling in the default handler, and so the entrypoint
// exports stay focused on the Worker fetch contract.

import type { z } from "zod";

// ── Auth response helper ─────────────────────────────────────────────────
// The auth decision itself (`isAllowedUser`, `AuthEnv`) lives in the shared
// `src/lib/worker-auth` module — both workers validate against the same
// contract. Only the response wrapper is Worker-specific because it carries
// portal-api's own CORS headers.

export function unauthorized(origin: string | null): Response {
  return Response.json(
    { error: "unauthorized" },
    { status: 401, headers: corsHeaders(origin) },
  );
}

// ── CORS ─────────────────────────────────────────────────────────────────

export const ALLOWED_ORIGINS = ["https://portal.guoyuer.com", "http://localhost:3000", "http://localhost:3100"];

export function isAllowedOrigin(origin: string | null): origin is string {
  return origin !== null && ALLOWED_ORIGINS.includes(origin);
}

export function corsHeaders(origin: string | null): HeadersInit {
  const base: Record<string, string> = {
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
  if (isAllowedOrigin(origin)) {
    base["Access-Control-Allow-Origin"] = origin;
    // Required so the browser includes the CF Access session cookie on
    // cross-origin fetches from portal.guoyuer.com → portal-api.guoyuer.com.
    base["Access-Control-Allow-Credentials"] = "true";
  }
  return base;
}

// ── Validation + JSON helper ──────────────────────────────────────────────

export function validatedResponse<T>(
  schema: z.ZodType<T>,
  payload: unknown,
  origin: string | null,
): Response {
  const parsed = schema.safeParse(payload);
  if (!parsed.success) {
    const detail = parsed.error.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
    return Response.json(
      { error: "schema drift", detail },
      { status: 500, headers: corsHeaders(origin) },
    );
  }
  return Response.json(parsed.data, {
    headers: { ...corsHeaders(origin), "Cache-Control": "no-cache" },
  });
}

export function dbError(origin: string | null, e: unknown): Response {
  return Response.json(
    { error: "Database query failed", detail: e instanceof Error ? e.message : "unknown" },
    { status: 502, headers: corsHeaders(origin) },
  );
}

// ── Settled helper (optional queries) ────────────────────────────────────

export type SettledResult<T> = { ok: true; value: T } | { ok: false; error: string };

export async function settled<T>(p: Promise<T>): Promise<SettledResult<T>> {
  try {
    return { ok: true, value: await p };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "unknown" };
  }
}
