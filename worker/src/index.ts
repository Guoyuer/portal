// ── Worker: thin API adapter over D1 ─────────────────────────────────────
// All data-shape work lives in D1 views; this file is SELECT → Zod validate → JSON.
// Critical (daily) failures return 503; optional (market/holdings/txns) failures
// degrade to null + a human-readable entry in `errors`.

import {
  TimelineDataSchema,
  TickerPriceResponseSchema,
  EconDataSchema,
  type TimelineErrors,
} from "../../src/lib/schemas";
import {
  corsHeaders,
  dbError,
  isAllowedOrigin,
  isAllowedUser,
  settled,
  unauthorized,
  validatedResponse,
} from "./utils";

interface Env {
  DB: D1Database;
  REQUIRE_AUTH?: string;
  ALLOWED_EMAIL?: string;
}

// ── /timeline ────────────────────────────────────────────────────────────

async function handleTimeline(env: Env, origin: string | null): Promise<Response> {
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
    return dbError(origin, e);
  }

  if (!daily.results.length) {
    return Response.json(
      { error: "No data available" },
      { status: 503, headers: corsHeaders(origin) },
    );
  }

  if (!categories.results.length) {
    return Response.json(
      { error: "No category metadata" },
      { status: 503, headers: corsHeaders(origin) },
    );
  }

  // Optional queries — each failure becomes a null section + errors entry.
  const [tickers, fidelity, qianji, indices, holdings, syncMetaRows] =
    await Promise.all([
      settled(env.DB.prepare("SELECT * FROM v_daily_tickers").all()),
      settled(env.DB.prepare("SELECT * FROM v_fidelity_txns").all()),
      settled(env.DB.prepare("SELECT * FROM v_qianji_txns").all()),
      settled(env.DB.prepare("SELECT * FROM v_market_indices").all()),
      settled(env.DB.prepare("SELECT * FROM v_holdings_detail").all()),
      settled(env.DB.prepare("SELECT key, value FROM sync_meta").all()),
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
    ? Object.fromEntries(
        (syncMetaRows.value.results as { key: string; value: string }[]).map((r) => [r.key, r.value]),
      )
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

  return validatedResponse(TimelineDataSchema, payload, origin);
}

// ── /econ ────────────────────────────────────────────────────────────────

async function handleEcon(env: Env, origin: string | null): Promise<Response> {
  try {
    const [seriesRows, snapshotRows, syncMetaRows] = await Promise.all([
      env.DB.prepare("SELECT key, points FROM v_econ_series_grouped").all(),
      env.DB.prepare("SELECT key, value FROM v_econ_snapshot").all(),
      env.DB.prepare("SELECT key, value FROM sync_meta").all(),
    ]);

    // Each row's `points` is a JSON string (json_group_array output); the
    // client unpacks it via EconDataSchema's EconPointsSchema transform.
    const series: Record<string, string> = {};
    for (const r of seriesRows.results as { key: string; points: string }[]) {
      series[r.key] = r.points;
    }

    const snapshot: Record<string, number> = {};
    for (const r of snapshotRows.results as { key: string; value: number }[]) {
      snapshot[r.key] = r.value;
    }

    const syncMeta: Record<string, string> = {};
    for (const r of syncMetaRows.results as { key: string; value: string }[]) {
      syncMeta[r.key] = r.value;
    }

    const payload = {
      generatedAt: syncMeta.last_sync ?? new Date().toISOString(),
      snapshot,
      series,
    };
    return validatedResponse(EconDataSchema, payload, origin);
  } catch (e) {
    return dbError(origin, e);
  }
}

// ── /prices/:symbol ──────────────────────────────────────────────────────

async function handlePrices(env: Env, origin: string | null, symbol: string): Promise<Response> {
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
    return validatedResponse(TickerPriceResponseSchema, payload, origin);
  } catch (e) {
    return dbError(origin, e);
  }
}

// ── Entry ────────────────────────────────────────────────────────────────

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get("Origin");

    if (request.method === "OPTIONS") {
      if (!isAllowedOrigin(origin)) {
        return new Response(null, { status: 403 });
      }
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    if (!isAllowedUser(request, env)) return unauthorized(origin);

    const url = new URL(request.url);

    if (url.pathname === "/econ") return handleEcon(env, origin);

    const priceMatch = url.pathname.match(/^\/prices\/([A-Za-z0-9.^=-]+)$/);
    if (priceMatch) {
      const symbol = decodeURIComponent(priceMatch[1]).toUpperCase();
      return handlePrices(env, origin, symbol);
    }

    if (url.pathname === "/timeline") return handleTimeline(env, origin);

    return new Response("Not found", { status: 404, headers: corsHeaders(origin) });
  },
} satisfies ExportedHandler<Env>;
