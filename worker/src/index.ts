// ── Worker: GET /timeline ────────────────────────────────────────────────────

interface Env {
  DB: D1Database;
}

const CORS_HEADERS: HeadersInit = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type",
};

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: CORS_HEADERS });
    }

    const url = new URL(request.url);
    if (url.pathname !== "/timeline") {
      return new Response("Not found", { status: 404 });
    }

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

    // Top 5 / bottom 5 (already sorted DESC by view)
    const all = holdings.results as Record<string, unknown>[];
    const holdingsDetail = {
      topPerformers: all.slice(0, 5),
      bottomPerformers: all.length > 5 ? all.slice(-5).reverse() : [],
      upcomingEarnings: [] as Record<string, unknown>[],
    };

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
        holdingsDetail,
      },
      {
        headers: {
          ...CORS_HEADERS,
          "Cache-Control": "public, max-age=3600",
        },
      },
    );
  },
} satisfies ExportedHandler<Env>;
