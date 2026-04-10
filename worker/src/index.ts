// ── Worker: GET /timeline ────────────────────────────────────────────────────

interface Env {
  DB: D1Database;
}

const ALLOWED_ORIGINS = ["https://portal.guoyuer.com", "http://localhost:3000"];

function corsHeaders(origin: string | null): HeadersInit {
  const allowed = origin && ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
  };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const origin = request.headers.get("Origin");

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders(origin) });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/timeline") {
      return new Response("Not found", { status: 404, headers: corsHeaders(origin) });
    }

    try {
      const [daily, prefix, tickers, fidelity, qianji, indices, indicators, holdings] =
        await Promise.all([
          env.DB.prepare("SELECT * FROM v_daily").all(),
          env.DB.prepare("SELECT * FROM v_prefix").all(),
          env.DB.prepare("SELECT * FROM v_daily_tickers").all(),
          env.DB.prepare("SELECT * FROM v_fidelity_txns").all(),
          env.DB.prepare("SELECT * FROM v_qianji_txns").all(),
          env.DB.prepare("SELECT * FROM v_market_indices").all(),
          env.DB.prepare("SELECT * FROM v_market_indicators").all(),
          env.DB.prepare("SELECT * FROM v_holdings_detail").all(),
        ]);

      if (!daily.results.length) {
        return Response.json(
          { error: "No data available" },
          { status: 503, headers: corsHeaders(origin) },
        );
      }

      // Indicators -> flat object
      const meta: Record<string, number | null> = {
        fedRate: null,
        treasury10y: null,
        cpi: null,
        unemployment: null,
        vix: null,
        dxy: null,
        usdCny: null,
        goldReturn: null,
        btcReturn: null,
        portfolioMonthReturn: null,
      };
      for (const r of indicators.results as { key: string; value: number }[]) {
        meta[r.key] = r.value;
      }

      return Response.json(
        {
          daily: daily.results,
          prefix: prefix.results,
          dailyTickers: tickers.results,
          fidelityTxns: fidelity.results,
          qianjiTxns: qianji.results,
          market: {
            indices: (indices.results as Record<string, unknown>[]).map(r => ({
              ...r,
              sparkline: JSON.parse(r.sparkline as string),
            })),
            ...meta,
          },
          holdingsDetail: { allStocks: holdings.results },
        },
        {
          headers: {
            ...corsHeaders(origin),
            "Cache-Control": "public, max-age=3600",
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
