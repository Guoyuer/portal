import { describe, it, expect, vi } from "vitest";
import { cachedJson, type CacheLike } from "./utils";

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
