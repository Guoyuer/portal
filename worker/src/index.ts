// ── Worker: thin API adapter over D1 ─────────────────────────────────────
// All data-shape work lives in D1 views; this file is SELECT → shape → JSON.
// Critical (daily) failures return 503; optional (market/holdings/txns) failures
// degrade to null + a human-readable entry in `errors`.
//
// Schema drift is checked once, on the frontend (use-bundle.ts). The Worker
// does NOT re-validate the payload with Zod — doubling the parse on the
// shared schema burned ~200ms CPU per /timeline call with zero extra
// safety, because both sides run the exact same definitions from
// src/lib/schemas.

import type { TimelineErrors } from "../../src/lib/schemas";
import {
  cachedJson,
  dbError,
  errorResponse,
  jsonResponse,
  notFoundResponse,
  settled,
} from "./utils";

interface Env {
  DB: D1Database;
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
    return errorResponse({ error: "No data available" }, 503);
  }

  if (!categories.results.length) {
    return errorResponse({ error: "No category metadata" }, 503);
  }

  // Optional queries — each failure becomes a null section + errors entry.
  // D1's `.all<T>()` generic is a compile-time shape hint (not runtime-checked),
  // so we still lean on the frontend Zod parse for actual validation. But the
  // generic replaces later `as` casts and catches refactor typos.
  type KVRow = { key: string; value: string };
  const [tickers, fidelity, qianji, indices, holdings, syncMetaRows] =
    await Promise.all([
      settled(env.DB.prepare("SELECT * FROM v_daily_tickers").all()),
      settled(env.DB.prepare("SELECT * FROM v_fidelity_txns").all()),
      settled(env.DB.prepare("SELECT * FROM v_qianji_txns").all()),
      settled(env.DB.prepare("SELECT * FROM v_market_indices").all()),
      settled(env.DB.prepare("SELECT * FROM v_holdings_detail").all()),
      settled(env.DB.prepare("SELECT key, value FROM sync_meta").all<KVRow>()),
    ]);

  const errors: TimelineErrors = {};

  // Transactions: if either side fails, surface a single error message.
  const txnErrors: string[] = [];
  if (!fidelity.ok) txnErrors.push(`fidelity: ${fidelity.error}`);
  if (!qianji.ok) txnErrors.push(`qianji: ${qianji.error}`);
  if (!tickers.ok) txnErrors.push(`tickers: ${tickers.error}`);
  if (txnErrors.length) errors.txns = txnErrors.join("; ");

  // Market: indices section is null on failure; macro lives on /econ now.
  const market = indices.ok ? { indices: indices.value.results } : null;
  if (!indices.ok) errors.market = `indices: ${indices.error}`;

  // Holdings: null the section on failure.
  if (!holdings.ok) errors.holdings = holdings.error;

  // syncMeta is informational — failure is silent (not included in errors).
  const syncMeta: Record<string, string> | null = syncMetaRows.ok
    ? Object.fromEntries(syncMetaRows.value.results.map((r) => [r.key, r.value]))
    : null;

  const payload = {
    daily: daily.results,
    dailyTickers: tickers.ok ? tickers.value.results : [],
    fidelityTxns: fidelity.ok ? fidelity.value.results : [],
    qianjiTxns: qianji.ok ? qianji.value.results : [],
    categories: categories.results,
    market,
    holdingsDetail: holdings.ok ? holdings.value.results : null,
    syncMeta: syncMeta && Object.keys(syncMeta).length > 0 ? syncMeta : null,
    errors,
  };

  return jsonResponse(payload);
}

// ── /econ ────────────────────────────────────────────────────────────────

async function handleEcon(env: Env): Promise<Response> {
  type SeriesRow = { key: string; points: string };
  type NumKVRow = { key: string; value: number };
  type StrKVRow = { key: string; value: string };
  try {
    const [seriesRows, snapshotRows, syncMetaRows] = await Promise.all([
      env.DB.prepare("SELECT key, points FROM v_econ_series_grouped").all<SeriesRow>(),
      env.DB.prepare("SELECT key, value FROM v_econ_snapshot").all<NumKVRow>(),
      env.DB.prepare("SELECT key, value FROM sync_meta").all<StrKVRow>(),
    ]);

    // Each row's `points` is a JSON string (json_group_array output); the
    // client unpacks it via EconDataSchema's EconPointsSchema transform.
    const series: Record<string, string> = {};
    for (const r of seriesRows.results) {
      series[r.key] = r.points;
    }

    const snapshot: Record<string, number> = {};
    for (const r of snapshotRows.results) {
      snapshot[r.key] = r.value;
    }

    const syncMeta: Record<string, string> = {};
    for (const r of syncMetaRows.results) {
      syncMeta[r.key] = r.value;
    }

    const payload = {
      generatedAt: syncMeta.last_sync ?? new Date().toISOString(),
      snapshot,
      series,
    };
    return jsonResponse(payload);
  } catch (e) {
    return dbError(e);
  }
}

// ── /prices/:symbol ──────────────────────────────────────────────────────

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

// Edge-cache TTLs (seconds). /timeline refreshes on nightly sync + local
// sync; 60s staleness is invisible to a human reloading the dashboard.
// /econ and /prices rarely change intraday so we cache harder.
const TTL_TIMELINE_S = 60;
const TTL_ECON_S = 600;
const TTL_PRICES_S = 300;

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

    if (pathname === "/econ") {
      return cachedJson(request, ctx, TTL_ECON_S, () => handleEcon(env));
    }

    const priceMatch = pathname.match(/^\/prices\/([A-Za-z0-9.^=-]+)$/);
    if (priceMatch) {
      const symbol = decodeURIComponent(priceMatch[1]).toUpperCase();
      return cachedJson(request, ctx, TTL_PRICES_S, () => handlePrices(env, symbol));
    }

    if (pathname === "/timeline") {
      return cachedJson(request, ctx, TTL_TIMELINE_S, () => handleTimeline(env));
    }

    return notFoundResponse();
  },
} satisfies ExportedHandler<Env>;
