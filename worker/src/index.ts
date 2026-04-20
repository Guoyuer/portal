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
      return cachedJson(request, ctx, TTLS.econ, () => handleEcon(env));
    }

    const priceMatch = pathname.match(/^\/prices\/([A-Za-z0-9.^=-]+)$/);
    if (priceMatch) {
      const symbol = decodeURIComponent(priceMatch[1]).toUpperCase();
      return cachedJson(request, ctx, TTLS.prices, () => handlePrices(env, symbol));
    }

    if (pathname === "/timeline") {
      return cachedJson(request, ctx, TTLS.timeline, () => handleTimeline(env));
    }

    return notFoundResponse();
  },
} satisfies ExportedHandler<Env>;
