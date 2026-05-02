// ── Worker: thin API adapter ─────────────────────────────────────────────
// D1 remains the default serving path during the migration. R2 is a temporary
// PR1 path selected by DATA_BACKEND=r2 for local artifact testing; the final
// cleanup PR removes the D1 branch instead of keeping a long-lived switch.
//
// All D1 data-shape work lives in views; the R2 path streams endpoint-shaped
// artifacts generated from those same views.
// Critical (daily) failures return 503; optional (market/holdings/txns) failures
// degrade to null + a human-readable entry in `errors`.
//
// Schema drift is checked once, on the frontend (use-bundle.ts). The Worker
// does NOT re-validate the payload with Zod — doubling the parse on the
// shared schema burned ~200ms CPU per /timeline call with zero extra
// safety, because both sides run the exact same definitions from
// src/lib/schemas.

import type { TimelineErrors } from "../../src/lib/schemas";
import { TTLS } from "./config";
import {
  cachedJson,
  dbError,
  errorResponse,
  jsonResponse,
  notFoundResponse,
  querySyncMeta,
  settled,
} from "./utils";

interface Env {
  DB: D1Database;
  PORTAL_DATA?: R2Bucket;
  DATA_BACKEND?: string;
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

function wantsR2(env: Env): boolean {
  return env.DATA_BACKEND === "r2";
}

function isPathSafeSymbol(symbol: string): boolean {
  return /^[A-Z0-9._=^-]+$/.test(symbol) && !symbol.includes("..");
}

function normalizeSymbol(raw: string): string | null {
  try {
    const symbol = decodeURIComponent(raw).toUpperCase();
    return isPathSafeSymbol(symbol) ? symbol : null;
  } catch {
    return null;
  }
}

function r2Unavailable(): Response {
  return errorResponse("R2 backend requested but PORTAL_DATA binding is missing", 500);
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

// ── /timeline ────────────────────────────────────────────────────────────

async function handleTimeline(env: Env): Promise<Response> {
  // Critical: `daily` and `categories` must succeed — the allocation UI is
  // unusable without either. Everything else can fail-open.
  let daily;
  let categories;
  try {
    [daily, categories] = await Promise.all([
      env.DB.prepare("SELECT * FROM v_daily").all(),
      env.DB.prepare("SELECT * FROM v_categories").all(),
    ]);
  } catch (e) {
    return dbError(e);
  }

  if (!daily.results.length) {
    return errorResponse("No data available", 503);
  }

  if (!categories.results.length) {
    return errorResponse("No category metadata", 503);
  }

  // Optional queries — each failure becomes a null section + errors entry.
  // D1's `.all<T>()` generic is a compile-time shape hint (not runtime-checked),
  // so we still lean on the frontend Zod parse for actual validation. But the
  // generic replaces later `as` casts and catches refactor typos.
  const [tickers, fidelity, qianji, robinhood, empower, indices, holdings, syncMetaResult] =
    await Promise.all([
      settled(env.DB.prepare("SELECT * FROM v_daily_tickers").all()),
      settled(env.DB.prepare("SELECT * FROM v_fidelity_txns").all()),
      settled(env.DB.prepare("SELECT * FROM v_qianji_txns").all()),
      settled(env.DB.prepare("SELECT * FROM v_robinhood_txns").all()),
      settled(env.DB.prepare("SELECT * FROM v_empower_contributions").all()),
      settled(env.DB.prepare("SELECT * FROM v_market_indices").all()),
      settled(env.DB.prepare("SELECT * FROM v_holdings_detail").all()),
      settled(querySyncMeta(env.DB)),
    ]);

  const errors: TimelineErrors = {};

  // Transactions: if any source fails, surface a joined error message.
  const txnErrors = Object.entries({ fidelity, qianji, robinhood, empower, tickers })
    .flatMap(([name, r]) => (r.ok ? [] : [`${name}: ${r.error}`]));
  if (txnErrors.length) errors.txns = txnErrors.join("; ");

  // Market: indices section is null on failure; macro lives on /econ now.
  const market = indices.ok ? { indices: indices.value.results } : null;
  if (!indices.ok) errors.market = `indices: ${indices.error}`;

  // Holdings: null the section on failure.
  if (!holdings.ok) errors.holdings = holdings.error;

  // syncMeta is informational — failure is silent (not included in errors).
  const meta = syncMetaResult.ok ? syncMetaResult.value : {};
  const syncMeta = Object.keys(meta).length > 0 ? meta : null;

  const payload = {
    daily: daily.results,
    dailyTickers: tickers.ok ? tickers.value.results : [],
    fidelityTxns: fidelity.ok ? fidelity.value.results : [],
    qianjiTxns: qianji.ok ? qianji.value.results : [],
    robinhoodTxns: robinhood.ok ? robinhood.value.results : [],
    empowerContributions: empower.ok ? empower.value.results : [],
    categories: categories.results,
    market,
    holdingsDetail: holdings.ok ? holdings.value.results : null,
    syncMeta,
    errors,
  };

  return jsonResponse(payload);
}

// ── /econ ────────────────────────────────────────────────────────────────

async function handleEcon(env: Env): Promise<Response> {
  try {
    const [seriesRows, snapshotRows, syncMeta] = await Promise.all([
      env.DB.prepare("SELECT key, points FROM v_econ_series_grouped")
        .all<{ key: string; points: string }>(),
      env.DB.prepare("SELECT key, value FROM v_econ_snapshot")
        .all<{ key: string; value: number }>(),
      querySyncMeta(env.DB),
    ]);

    // Each row's `points` is a JSON string (json_group_array output); the
    // client unpacks it via EconDataSchema's EconPointsSchema transform.
    const payload = {
      generatedAt: syncMeta.last_sync ?? new Date().toISOString(),
      snapshot: Object.fromEntries(snapshotRows.results.map((r) => [r.key, r.value])),
      series: Object.fromEntries(seriesRows.results.map((r) => [r.key, r.points])),
    };
    return jsonResponse(payload);
  } catch (e) {
    return dbError(e);
  }
}

// ── /prices and /prices/:symbol ──────────────────────────────────────────

async function handlePricesBundle(env: Env): Promise<Response> {
  try {
    type SymbolRow = { symbol: string };
    type PriceRow = { symbol: string; date: string; close: number };
    type TxnRow = {
      symbol: string;
      runDate: string;
      actionType: string;
      quantity: number;
      price: number;
      amount: number;
    };
    const [symbolRows, priceRows, txnRows] = await Promise.all([
      env.DB.prepare(
        "SELECT symbol FROM daily_close WHERE symbol <> '' UNION SELECT symbol FROM fidelity_transactions WHERE symbol <> '' ORDER BY symbol",
      ).all<SymbolRow>(),
      env.DB.prepare("SELECT symbol, date, close FROM daily_close WHERE symbol <> '' ORDER BY symbol, date")
        .all<PriceRow>(),
      env.DB.prepare(
        "SELECT symbol, run_date AS runDate, action_type AS actionType, quantity, price, amount FROM fidelity_transactions WHERE symbol <> '' ORDER BY symbol, id",
      ).all<TxnRow>(),
    ]);
    const payload: Record<string, { symbol: string; prices: Array<Omit<PriceRow, "symbol">>; transactions: Array<Omit<TxnRow, "symbol">> }> = {};
    for (const row of symbolRows.results) {
      payload[row.symbol] = { symbol: row.symbol, prices: [], transactions: [] };
    }
    for (const { symbol, ...price } of priceRows.results) {
      (payload[symbol] ??= { symbol, prices: [], transactions: [] }).prices.push(price);
    }
    for (const { symbol, ...txn } of txnRows.results) {
      (payload[symbol] ??= { symbol, prices: [], transactions: [] }).transactions.push(txn);
    }
    return jsonResponse(payload);
  } catch (e) {
    return dbError(e);
  }
}

async function handlePrices(env: Env, symbol: string): Promise<Response> {
  try {
    const [priceRows, txnRows] = await Promise.all([
      env.DB.prepare("SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date")
        .bind(symbol).all(),
      env.DB.prepare(
        "SELECT run_date AS runDate, action_type AS actionType, quantity, price, amount FROM fidelity_transactions WHERE symbol = ? ORDER BY id",
      ).bind(symbol).all(),
    ]);
    const payload = {
      symbol,
      prices: priceRows.results,
      transactions: txnRows.results,
    };
    return jsonResponse(payload);
  } catch (e) {
    return dbError(e);
  }
}

// ── Entry ────────────────────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    // In prod the Worker is mounted at `portal.guoyuer.com/api/*` so requests
    // arrive with an `/api` prefix; strip it so the rest of this handler and
    // `wrangler dev` on `localhost:8787/timeline` share the same path table.
    // A bare `/api` (no trailing slash) normalises to `/` so it falls through
    // to the normal 404 instead of leaking the prefix into error responses.
    const API_PREFIX = "/api";
    let pathname = url.pathname;
    if (pathname === API_PREFIX || pathname.startsWith(API_PREFIX + "/")) {
      pathname = pathname.slice(API_PREFIX.length) || "/";
    }

    const useR2 = wantsR2(env);

    if (pathname === "/econ") {
      return cachedJson(
        request,
        ctx,
        TTLS.econ,
        () => (useR2 ? handleR2Endpoint(env, (m) => m.objects.econ) : handleEcon(env)),
      );
    }

    if (pathname === "/prices") {
      return cachedJson(
        request,
        ctx,
        TTLS.prices,
        () => (useR2 ? handleR2Endpoint(env, (m) => m.objects.prices) : handlePricesBundle(env)),
      );
    }

    const priceMatch = pathname.match(/^\/prices\/([^/]+)$/);
    if (priceMatch) {
      const symbol = normalizeSymbol(priceMatch[1]);
      if (!symbol) return errorResponse("Malformed price symbol", 400);
      if (useR2) return errorResponse("Use /prices for the bundled R2 price payload", 404);
      return cachedJson(
        request,
        ctx,
        TTLS.prices,
        () => handlePrices(env, symbol),
      );
    }

    if (pathname === "/timeline") {
      return cachedJson(
        request,
        ctx,
        TTLS.timeline,
        () => (useR2 ? handleR2Endpoint(env, (m) => m.objects.timeline) : handleTimeline(env)),
      );
    }

    return notFoundResponse();
  },
} satisfies ExportedHandler<Env>;
