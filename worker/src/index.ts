// ── Worker: thin R2 API facade ───────────────────────────────────────────
// Production data is published as endpoint-shaped JSON artifacts in R2.
// The Worker owns only same-origin routing, manifest lookup, object
// streaming, no-store headers, and explicit failure responses.

interface Env {
  PORTAL_DATA?: R2Bucket;
}

type ManifestObject = {
  key: string;
  sha256: string;
  bytes: number;
  contentType: string;
};

const ENDPOINTS = ["timeline", "econ", "prices"] as const;
type Endpoint = (typeof ENDPOINTS)[number];

type R2Manifest = {
  version: string;
  generatedAt: string;
  objects: Record<Endpoint, ManifestObject>;
};

const MANIFEST_KEY = "manifest.json";
// Worker is mounted same-origin as Pages in prod (portal.guoyuer.com/api/*),
// so the browser never applies CORS. The wildcard keeps local Next -> wrangler
// dev requests working; requests carry no credentials.
const RESPONSE_HEADERS: HeadersInit = {
  "Access-Control-Allow-Origin": "*",
  "Cache-Control": "no-store",
};

function errorResponse(message: string, status: number): Response {
  return Response.json({ error: message }, { status, headers: RESPONSE_HEADERS });
}

function r2Unavailable(): Response {
  return errorResponse("PORTAL_DATA R2 binding is missing", 500);
}

function r2StreamResponse(object: R2ObjectBody): Response {
  const headers = new Headers({
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "no-store",
    "Content-Type": "application/json",
  });
  object.writeHttpMetadata(headers);
  headers.set("Access-Control-Allow-Origin", "*");
  headers.set("Cache-Control", "no-store");
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
    && ENDPOINTS.every((endpoint) => validManifestObject(objects[endpoint]))
  );
}

async function loadR2Manifest(env: Env): Promise<R2Manifest | Response> {
  if (!env.PORTAL_DATA) return r2Unavailable();

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
  endpoint: Endpoint,
): Promise<Response> {
  const manifest = await loadR2Manifest(env);
  if (manifest instanceof Response) return manifest;
  return streamR2Object(env, manifest.objects[endpoint]);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const API_PREFIX = "/api";
    let pathname = url.pathname;
    if (pathname === API_PREFIX || pathname.startsWith(API_PREFIX + "/")) {
      pathname = pathname.slice(API_PREFIX.length) || "/";
    }

    const endpoint = ENDPOINTS.find((name) => pathname === `/${name}`);
    if (endpoint) return handleR2Endpoint(env, endpoint);

    return new Response("Not found", { status: 404, headers: RESPONSE_HEADERS });
  },
} satisfies ExportedHandler<Env>;
