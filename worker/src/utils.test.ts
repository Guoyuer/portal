import { describe, it, expect } from "vitest";
import { z } from "zod";
import { dbError, settled, validatedResponse } from "./utils";

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
