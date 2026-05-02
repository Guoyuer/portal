// ── Worker: thin R2 API facade ───────────────────────────────────────────
// Production data is published as endpoint-shaped JSON artifacts in R2.
// The Worker owns only same-origin routing, manifest lookup, object
// streaming, cache headers, and explicit failure responses.

import { TTLS } from "./config";
import {
  cachedJson,
  errorResponse,
  notFoundResponse,
} from "./utils";

interface Env {
  PORTAL_DATA?: R2Bucket;
}

type ManifestObject = {
  key: string;
  sha256: string;
  bytes: number;
  contentType: string;
};

type R2Manifest = {
  version: string;
  generatedAt: string;
  objects: {
    timeline: ManifestObject;
    econ: ManifestObject;
    prices: ManifestObject;
  };
};

const MANIFEST_KEY = "manifest.json";
const MANIFEST_CACHE_MS = 30_000;

let r2ManifestCache: { expiresAt: number; manifest: R2Manifest } | null = null;

export function __resetR2ManifestCacheForTests(): void {
  r2ManifestCache = null;
}

function r2Unavailable(): Response {
  return errorResponse("PORTAL_DATA R2 binding is missing", 500);
}

function r2StreamResponse(object: R2ObjectBody): Response {
  const headers = new Headers({
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "no-cache",
    "Content-Type": "application/json",
  });
  object.writeHttpMetadata(headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Cache-Control", "no-cache");
  if (!headers.get("Content-Type")) headers.set("Content-Type", "application/json");
  return new Response(object.body, { headers });
}

function validManifestObject(value: unknown): value is ManifestObject {
  if (!value || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  return (
    typeof obj.key === "string"
    && typeof obj.sha256 === "string"
    && typeof obj.bytes === "number"
    && typeof obj.contentType === "string"
  );
}

function validManifest(value: unknown): value is R2Manifest {
  if (!value || typeof value !== "object") return false;
  const obj = value as Record<string, unknown>;
  const objects = obj.objects as Record<string, unknown> | undefined;
  return (
    typeof obj.version === "string"
    && typeof obj.generatedAt === "string"
    && !!objects
    && validManifestObject(objects.timeline)
    && validManifestObject(objects.econ)
    && validManifestObject(objects.prices)
  );
}

async function loadR2Manifest(env: Env): Promise<R2Manifest | Response> {
  if (!env.PORTAL_DATA) return r2Unavailable();
  const now = Date.now();
  if (r2ManifestCache && r2ManifestCache.expiresAt > now) {
    return r2ManifestCache.manifest;
  }

  const object = await env.PORTAL_DATA.get(MANIFEST_KEY);
  if (!object) return errorResponse("R2 manifest missing", 503);

  let payload: unknown;
  try {
    payload = await object.json();
  } catch (e) {
    return errorResponse(
      `R2 manifest is not valid JSON: ${e instanceof Error ? e.message : "unknown"}`,
      502,
    );
  }
  if (!validManifest(payload)) return errorResponse("R2 manifest has invalid shape", 502);

  r2ManifestCache = { manifest: payload, expiresAt: now + MANIFEST_CACHE_MS };
  return payload;
}

async function streamR2Object(env: Env, descriptor: ManifestObject): Promise<Response> {
  if (!env.PORTAL_DATA) return r2Unavailable();
  const object = await env.PORTAL_DATA.get(descriptor.key);
  if (!object) return errorResponse(`R2 object missing: ${descriptor.key}`, 503);
  return r2StreamResponse(object);
}

async function handleR2Endpoint(
  env: Env,
  select: (manifest: R2Manifest) => ManifestObject,
): Promise<Response> {
  const manifest = await loadR2Manifest(env);
  if (manifest instanceof Response) return manifest;
  return streamR2Object(env, select(manifest));
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const API_PREFIX = "/api";
    let pathname = url.pathname;
    if (pathname === API_PREFIX || pathname.startsWith(API_PREFIX + "/")) {
      pathname = pathname.slice(API_PREFIX.length) || "/";
    }

    if (pathname === "/econ") {
      return cachedJson(request, ctx, TTLS.econ, () => handleR2Endpoint(env, (m) => m.objects.econ));
    }

    if (pathname === "/prices") {
      return cachedJson(request, ctx, TTLS.prices, () => handleR2Endpoint(env, (m) => m.objects.prices));
    }

    if (pathname === "/timeline") {
      return cachedJson(request, ctx, TTLS.timeline, () => handleR2Endpoint(env, (m) => m.objects.timeline));
    }

    return notFoundResponse();
  },
} satisfies ExportedHandler<Env>;
