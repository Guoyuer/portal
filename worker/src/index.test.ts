import { afterEach, describe, expect, it, vi } from "vitest";

import worker from "./index";

function makeCtx(): ExecutionContext {
  return {
    waitUntil: vi.fn(),
    passThroughOnException: vi.fn(),
  };
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
    vi.unstubAllGlobals();
  });

  it("streams manifest-referenced endpoint artifacts", async () => {
    const env = {
      PORTAL_DATA: makeR2({
        "manifest.json": manifest(),
        "snapshots/2026-05-02T170000Z/timeline.json": '{"ok":"timeline"}',
      }),
    };

    const res = await worker.fetch(new Request("http://localhost/api/timeline"), env, makeCtx());

    expect(res.status).toBe(200);
    expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
    expect(res.headers.get("Cache-Control")).toBe("no-store");
    await expect(res.json()).resolves.toEqual({ ok: "timeline" });
  });

  it("streams the bundled prices artifact without Worker-side symbol lookup", async () => {
    const env = {
      PORTAL_DATA: makeR2({
        "manifest.json": manifest(),
        "snapshots/2026-05-02T170000Z/prices.json": '{"VOO":{"symbol":"VOO","prices":[],"transactions":[]}}',
      }),
    };

    const res = await worker.fetch(new Request("http://localhost/api/prices"), env, makeCtx());

    expect(res.status).toBe(200);
    await expect(res.json()).resolves.toEqual({ VOO: { symbol: "VOO", prices: [], transactions: [] } });
  });

  it("does not expose the old per-symbol price route", async () => {
    const r2 = makeR2({ "manifest.json": manifest() });
    const env = { PORTAL_DATA: r2 };

    const res = await worker.fetch(new Request("http://localhost/api/prices/VOO"), env, makeCtx());

    expect(res.status).toBe(404);
  });

  it("does not cache endpoint artifacts across manifest updates", async () => {
    const firstManifest = manifest();
    const secondManifest = JSON.stringify({
      ...JSON.parse(firstManifest),
      version: "2026-05-02T180000Z",
      objects: {
        ...JSON.parse(firstManifest).objects,
        timeline: {
          key: "snapshots/2026-05-02T180000Z/timeline.json",
          sha256: "x",
          bytes: 2,
          contentType: "application/json",
        },
      },
    });
    let activeManifest = firstManifest;
    const r2 = {
      get: vi.fn((key: string) => {
        const objects: Record<string, string> = {
          "manifest.json": activeManifest,
          "snapshots/2026-05-02T170000Z/timeline.json": '{"version":"old"}',
          "snapshots/2026-05-02T180000Z/timeline.json": '{"version":"new"}',
        };
        const body = objects[key];
        return Promise.resolve(body === undefined ? null : makeR2Object(key, body));
      }),
    } as unknown as R2Bucket;
    const env = { PORTAL_DATA: r2 };

    const first = await worker.fetch(new Request("http://localhost/api/timeline"), env, makeCtx());
    activeManifest = secondManifest;
    const second = await worker.fetch(new Request("http://localhost/api/timeline"), env, makeCtx());

    await expect(first.json()).resolves.toEqual({ version: "old" });
    await expect(second.json()).resolves.toEqual({ version: "new" });
  });
});
