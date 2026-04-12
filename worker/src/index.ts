// ── Worker: GET /timeline, GET /econ ─────────────────────────────────────────

interface Env {
  DB: D1Database;
}

const ALLOWED_ORIGINS = ["https://portal.guoyuer.com", "http://localhost:3000", "http://localhost:3100"];

function isAllowedOrigin(origin: string | null): origin is string {
  return origin !== null && ALLOWED_ORIGINS.includes(origin);
}

function corsHeaders(origin: string | null): HeadersInit {
  const base: Record<string, string> = {
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
  if (isAllowedOrigin(origin)) {
    base["Access-Control-Allow-Origin"] = origin;
  }
  return base;
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get("Origin");

    if (request.method === "OPTIONS") {
      if (!isAllowedOrigin(origin)) {
        return new Response(null, { status: 403 });
      }
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    const url = new URL(request.url);

    if (url.pathname === "/econ") {
      try {
        const [seriesRows, syncMetaRows] = await Promise.all([
          env.DB.prepare("SELECT key, date, value FROM v_econ_series").all(),
          env.DB.prepare("SELECT key, value FROM sync_meta").all(),
        ]);

        // Group rows into {key: [{date, value}]}
        const series: Record<string, { date: string; value: number }[]> = {};
        const snapshot: Record<string, number> = {};
        for (const r of seriesRows.results as { key: string; date: string; value: number }[]) {
          if (!series[r.key]) series[r.key] = [];
          series[r.key].push({ date: r.date, value: r.value });
        }
        // Snapshot = last value per series
        for (const [key, points] of Object.entries(series)) {
          if (points.length > 0) {
            snapshot[key] = points[points.length - 1].value;
          }
        }

        const syncMeta: Record<string, string> = {};
        for (const r of syncMetaRows.results as { key: string; value: string }[]) {
          syncMeta[r.key] = r.value;
        }

        return Response.json(
          {
            generatedAt: syncMeta.last_sync ?? new Date().toISOString(),
            snapshot,
            series,
          },
          { headers: { ...corsHeaders(origin), "Cache-Control": "no-cache" } },
        );
      } catch (e) {
        return Response.json(
          { error: "Database query failed", detail: e instanceof Error ? e.message : "unknown" },
          { status: 502, headers: corsHeaders(origin) },
        );
      }
    }

    // ── GET /prices/:symbol — on-demand daily close prices + transactions ────
    const priceMatch = url.pathname.match(/^\/prices\/([A-Za-z0-9.^=-]+)$/);
    if (priceMatch) {
      const symbol = decodeURIComponent(priceMatch[1]).toUpperCase();
      try {
        const [priceRows, txnRows] = await Promise.all([
          env.DB.prepare("SELECT date, close FROM daily_close WHERE symbol = ? ORDER BY date")
            .bind(symbol).all(),
          env.DB.prepare(
            "SELECT run_date AS runDate, action_type AS actionType, quantity, price, amount FROM fidelity_transactions WHERE symbol = ? ORDER BY id"
          ).bind(symbol).all(),
        ]);
        return Response.json(
          { symbol, prices: priceRows.results, transactions: txnRows.results },
          { headers: { ...corsHeaders(origin), "Cache-Control": "no-cache" } },
        );
      } catch (e) {
        return Response.json(
          { error: "Database query failed", detail: e instanceof Error ? e.message : "unknown" },
          { status: 502, headers: corsHeaders(origin) },
        );
      }
    }

    if (url.pathname !== "/timeline") {
      return new Response("Not found", { status: 404, headers: corsHeaders(origin) });
    }

    try {
      const [daily, tickers, fidelity, qianji, indices, indicators, holdings, syncMetaRows] =
        await Promise.all([
          env.DB.prepare("SELECT * FROM v_daily").all(),
          env.DB.prepare("SELECT * FROM v_daily_tickers").all(),
          env.DB.prepare("SELECT * FROM v_fidelity_txns").all(),
          env.DB.prepare("SELECT * FROM v_qianji_txns").all(),
          env.DB.prepare("SELECT * FROM v_market_indices").all(),
          env.DB.prepare("SELECT * FROM v_market_indicators").all(),
          env.DB.prepare("SELECT * FROM v_holdings_detail").all(),
          env.DB.prepare("SELECT key, value FROM sync_meta").all(),
        ]);

      if (!daily.results.length) {
        return Response.json(
          { error: "No data available" },
          { status: 503, headers: corsHeaders(origin) },
        );
      }

      // Indicators -> flat object (Zod fills missing keys with null via .nullable().default(null))
      const meta: Record<string, number> = {};
      for (const r of indicators.results as { key: string; value: number }[]) {
        meta[r.key] = r.value;
      }

      // Sync metadata
      const syncMeta: Record<string, string> = {};
      for (const r of syncMetaRows.results as { key: string; value: string }[]) {
        syncMeta[r.key] = r.value;
      }

      return Response.json(
        {
          daily: daily.results,
          dailyTickers: tickers.results,
          fidelityTxns: fidelity.results,
          qianjiTxns: qianji.results,
          market: {
            indices: (indices.results as Record<string, unknown>[]).map(r => ({
              ...r,
              sparkline: JSON.parse(r.sparkline as string) as number[],
            })),
            ...meta,
          },
          holdingsDetail: holdings.results,
          syncMeta: Object.keys(syncMeta).length > 0 ? syncMeta : null,
        },
        {
          headers: {
            ...corsHeaders(origin),
            "Cache-Control": "no-cache",
          },
        },
      );
    } catch (e) {
      return Response.json(
        { error: "Database query failed", detail: e instanceof Error ? e.message : "unknown" },
        { status: 502, headers: corsHeaders(origin) },
      );
    }
  },
} satisfies ExportedHandler<Env>;
