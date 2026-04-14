import { describe, it, expect, vi } from "vitest";
import { z } from "zod";
import { cachedJson, dbError, settled, validatedResponse, type CacheLike } from "./utils";

// ── validatedResponse ───────────────────────────────────────────────────

describe("validatedResponse", () => {
  const schema = z.object({ x: z.number(), y: z.string() });

  it("returns 200 + parsed JSON when payload matches", async () => {
    const res = validatedResponse(schema, { x: 1, y: "ok" });
    expect(res.status).toBe(200);
    expect(res.headers.get("Cache-Control")).toBe("no-cache");
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    await expect(res.json()).resolves.toEqual({ x: 1, y: "ok" });
  });

  it("returns 500 with detail on schema mismatch", async () => {
    const res = validatedResponse(schema, { x: "not-a-number" });
    expect(res.status).toBe(500);
    const body = (await res.json()) as { error: string; detail: string };
    expect(body.error).toBe("schema drift");
    expect(body.detail).toContain("x");
  });
});

// ── dbError ─────────────────────────────────────────────────────────────

describe("dbError", () => {
  it("returns 502 with Error.message detail", async () => {
    const res = dbError(new Error("view missing"));
    expect(res.status).toBe(502);
    const body = (await res.json()) as { error: string; detail: string };
    expect(body.error).toBe("Database query failed");
    expect(body.detail).toBe("view missing");
  });

  it("falls back to 'unknown' for non-Error rejections", async () => {
    const res = dbError("string thrown");
    const body = (await res.json()) as { detail: string };
    expect(body.detail).toBe("unknown");
  });
});

// ── settled ─────────────────────────────────────────────────────────────

describe("settled", () => {
  it("wraps resolved values in {ok:true, value}", async () => {
    const r = await settled(Promise.resolve(42));
    expect(r).toEqual({ ok: true, value: 42 });
  });

  it("wraps rejections in {ok:false, error} with Error.message", async () => {
    const r = await settled(Promise.reject(new Error("boom")));
    expect(r).toEqual({ ok: false, error: "boom" });
  });

  it("falls back to 'unknown' when the rejection is not an Error", async () => {
    const r = await settled(Promise.reject("plain-string"));
    expect(r).toEqual({ ok: false, error: "unknown" });
  });
});

// ── cachedJson ──────────────────────────────────────────────────────────

function makeCache(initial?: Response): CacheLike & { putCalls: Array<Response> } {
  let store = initial;
  const putCalls: Response[] = [];
  return {
    putCalls,
    async match() { return store; },
    async put(_req: Request, res: Response) { store = res; putCalls.push(res); },
  };
}

function makeCtx() {
  const waits: Array<Promise<unknown>> = [];
  return {
    waits,
    waitUntil(p: Promise<unknown>) { waits.push(p); },
  };
}

describe("cachedJson", () => {
  const REQ = new Request("http://localhost/timeline");

  it("calls produce() on miss, sets Cache-Control, and stores in cache", async () => {
    const cache = makeCache(undefined);
    const ctx = makeCtx();
    const produce = vi.fn(() => Promise.resolve(Response.json({ ok: 1 })));

    const res = await cachedJson(REQ, ctx, 60, produce, cache);

    expect(produce).toHaveBeenCalledTimes(1);
    expect(res.headers.get("Cache-Control")).toBe("public, max-age=60");
    await Promise.all(ctx.waits);
    expect(cache.putCalls).toHaveLength(1);
    expect(cache.putCalls[0].headers.get("Cache-Control")).toBe("public, max-age=60");
  });

  it("returns the cached response on hit without calling produce()", async () => {
    const stored = new Response(JSON.stringify({ cached: true }), {
      headers: { "Cache-Control": "public, max-age=60", "content-type": "application/json" },
    });
    const cache = makeCache(stored);
    const ctx = makeCtx();
    const produce = vi.fn(() => Promise.resolve(Response.json({ fresh: true })));

    const res = await cachedJson(REQ, ctx, 60, produce, cache);

    expect(produce).not.toHaveBeenCalled();
    await expect(res.json()).resolves.toEqual({ cached: true });
  });

  it("does NOT cache non-2xx responses", async () => {
    const cache = makeCache(undefined);
    const ctx = makeCtx();
    const produce = vi.fn(() => Promise.resolve(
      Response.json({ error: "down" }, { status: 503 }),
    ));

    const res = await cachedJson(REQ, ctx, 60, produce, cache);

    expect(res.status).toBe(503);
    await Promise.all(ctx.waits);
    expect(cache.putCalls).toHaveLength(0);
  });
});
