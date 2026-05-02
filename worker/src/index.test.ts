import { afterEach, describe, expect, it, vi } from "vitest";

import worker, { __resetR2ManifestCacheForTests } from "./index";

function makeCtx(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  };
}

function installCache(): void {
  vi.stubGlobal("caches", {
    default: {
      match: vi.fn(() => Promise.resolve(undefined)),
      put: vi.fn(() => Promise.resolve()),
    },
  });
}

function makeR2Object(key: string, bodyText: string): R2ObjectBody {
  const bytes = new TextEncoder().encode(bodyText);
  return {
    key,
    version: "test",
    size: bytes.byteLength,
    etag: "test",
    httpEtag: "test",
    checksums: {},
    uploaded: new Date("2026-05-02T00:00:00Z"),
    storageClass: "Standard",
    writeHttpMetadata(headers: Headers) {
      headers.set("Content-Type", "application/json");
    },
    get body() {
      return new ReadableStream({
        start(controller) {
          controller.enqueue(bytes);
          controller.close();
        },
      });
    },
    bodyUsed: false,
    arrayBuffer: () => Promise.resolve(bytes.buffer.slice(0)),
    bytes: () => Promise.resolve(bytes),
    text: () => Promise.resolve(bodyText),
    json: <T>() => Promise.resolve(JSON.parse(bodyText) as T),
    blob: () => Promise.resolve(new Blob([bytes], { type: "application/json" })),
  } as R2ObjectBody;
}

function makeR2(objects: Record<string, string>): R2Bucket {
  return {
    get: vi.fn((key: string) => {
      const body = objects[key];
      return Promise.resolve(body === undefined ? null : makeR2Object(key, body));
    }),
  } as unknown as R2Bucket;
}

function manifest(): string {
  return JSON.stringify({
    version: "2026-05-02T170000Z",
    generatedAt: "2026-05-02T17:00:00Z",
    objects: {
      timeline: {
        key: "snapshots/2026-05-02T170000Z/timeline.json",
        sha256: "x",
        bytes: 2,
        contentType: "application/json",
      },
      econ: {
        key: "snapshots/2026-05-02T170000Z/econ.json",
        sha256: "x",
        bytes: 2,
        contentType: "application/json",
      },
      prices: {
        key: "snapshots/2026-05-02T170000Z/prices.json",
        sha256: "x",
        bytes: 2,
        contentType: "application/json",
      },
    },
  });
}

describe("Worker R2 path", () => {
  afterEach(() => {
    __resetR2ManifestCacheForTests();
    vi.unstubAllGlobals();
  });

  it("streams manifest-referenced endpoint artifacts", async () => {
    installCache();
    const env = {
      DATA_BACKEND: "r2",
      PORTAL_DATA: makeR2({
        "manifest.json": manifest(),
        "snapshots/2026-05-02T170000Z/timeline.json": '{"ok":"timeline"}',
      }),
    };

    const res = await worker.fetch(new Request("http://localhost/api/timeline"), env, makeCtx());

    expect(res.status).toBe(200);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    await expect(res.json()).resolves.toEqual({ ok: "timeline" });
  });

  it("streams the bundled prices artifact without Worker-side symbol lookup", async () => {
    installCache();
    const env = {
      DATA_BACKEND: "r2",
      PORTAL_DATA: makeR2({
        "manifest.json": manifest(),
        "snapshots/2026-05-02T170000Z/prices.json": '{"VOO":{"symbol":"VOO","prices":[],"transactions":[]}}',
      }),
    };

    const res = await worker.fetch(new Request("http://localhost/api/prices"), env, makeCtx());

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({ VOO: { symbol: "VOO", prices: [], transactions: [] } });
  });

  it("rejects path-unsafe price symbols before reading R2", async () => {
    installCache();
    const r2 = makeR2({ "manifest.json": manifest() });
    const env = { DATA_BACKEND: "r2", PORTAL_DATA: r2 };

    const res = await worker.fetch(new Request("http://localhost/api/prices/BAD%2FSYM"), env, makeCtx());

    expect(res.status).toBe(400);
  });
});
