# Tech Upgrades TODO

Based on codebase audit (2026-04-09). Current stack: Next.js 16.2 (static export), React 19.2, Recharts 3.8, Tailwind v4, Cloudflare Pages + Workers/D1.

Sources: [Next.js 16](https://nextjs.org/blog/next-16) | [React 19.2](https://react.dev/blog/2025/10/01/react-19-2) | [Tailwind v4.2](https://www.infoq.com/news/2026/04/tailwind-css-4-2-webpack/) | [D1 Read Replication](https://www.infoq.com/news/2025/05/cloudflare-d1-global-replication/) | [Vinext](https://blog.cloudflare.com/vinext/) | [Speculation Rules](https://developer.mozilla.org/en-US/docs/Web/API/Speculation_Rules_API) | [Container Queries](https://blog.logrocket.com/container-queries-2026/)

---

## P0 — Drop-in improvements (no refactor needed)

### `content-visibility: auto` on off-screen sections
Finance page has 6+ heavy sections (charts, tables). Adding one CSS property to each `<section>` skips rendering until scroll.
```css
section { content-visibility: auto; contain-intrinsic-size: auto 400px; }
```
**Files**: `src/app/finance/page.tsx` — each `<section id="...">` block
**Effort**: 10 min

### Container Queries for dashboard cards
Metric cards use `grid grid-cols-3 gap-4` with `sm:`/`md:` breakpoints. Container queries let each card adapt to its own space — useful if grid layout changes or cards get reused elsewhere.
```css
.card-container { container-type: inline-size; }
@container (max-width: 200px) { .card { /* compact layout */ } }
```
**Files**: `src/components/finance/metric-cards.tsx` (line 113 grid), `src/components/finance/market-context.tsx` (index cards)
**Effort**: 1-2 hours
**Browser support**: 95%+ (Chrome 105, Firefox 110, Safari 16)

### Remove e2e `waitForTimeout` calls
16 calls in `e2e/finance.spec.ts`, 3 in `interactive-check.spec.ts`, 1 in `perf-brush.spec.ts`. Replace with Playwright auto-waiting (`waitFor`, `toBeVisible`, `expect.poll`).
**Effort**: 1-2 hours

---

## P1 — Moderate effort, clear benefit

### React Compiler (auto-memoization) — stable in Next.js 16
`use-bundle.ts` has 11 `useMemo`, 1 `useCallback`, plus 3 `React.memo()` wrappers on chart components. React Compiler auto-memoizes, removing most manual work. **Now stable** in Next.js 16 (no longer experimental after React Compiler 1.0).
```ts
const nextConfig = {
  output: "export",
  experimental: { reactCompiler: true },
};
```
**Risk**: Low — stable since Next.js 16. Test Recharts brush interaction after enabling.
**Effort**: 30 min to enable, 1-2 hours to validate and remove redundant `useMemo`/`memo`

### View Transitions API for page navigation
Finance <-> Economy page switch is a full reload. View Transitions gives a smooth cross-fade.
Next.js 16 supports this experimentally. Progressive enhancement — no-op in unsupported browsers.
**Files**: `next.config.ts`, `src/components/layout/sidebar.tsx` (nav links)
**Effort**: 30 min

### Speculation Rules API for page prefetching
Prerender the /econ page when user is on /finance (and vice versa) so navigation feels instant. Works in Chromium browsers (Chrome, Edge, Opera), no-op elsewhere. Static export is a perfect fit since pages are just HTML files.
```html
<script type="speculationrules">
{ "prerender": [{ "urls": ["/econ"] }] }
</script>
```
**Files**: `src/app/layout.tsx` or `src/app/finance/page.tsx`
**Effort**: 30 min
**Note**: Next.js 16.2 has `prefetchInlining` experimental flag that bundles prefetch into single request — could be combined.

### D1 Global Read Replication
D1 now supports automatic read replicas in all regions (beta, free). Cuts Worker API latency 40-60% by eliminating redundant network round trips.
**Files**: `worker/wrangler.toml` — add `read_replication = { mode = "auto" }` under `[d1_databases]`
**Effort**: 5 min config change, needs testing

### `"use cache"` directive (Next.js 16)
Next.js 16 introduces Cache Components with explicit `"use cache"` directive for pages, components, and functions. Not useful for static export today, but if you ever move to SSR/ISR with OpenNext, this replaces the old `revalidate` API with more granular caching control.

---

## P2 — Larger effort, evaluate ROI

### Migrate from `next-on-pages` to OpenNext (if applicable)
Current setup: pure static export (`output: "export"`), no Cloudflare adapter. If you ever need SSR/ISR/middleware, the path forward is [OpenNext](https://opennext.js.org/cloudflare), not `@cloudflare/next-on-pages` (deprecated).
**Current status**: Not blocking — static export works fine. Revisit if SSR is needed.

### Vinext (Cloudflare's Vite-based Next.js alternative)
4.4x faster builds, 57% smaller bundles. Has Traffic-aware Pre-Rendering (TPR). Experimental — watch but don't adopt yet.
**Source**: https://blog.cloudflare.com/vinext/

### ECharts for React (replace Recharts for heavy charts)
Recharts is SVG-based, currently rendering all daily data points (~800 points, no downsampling). If data grows significantly (e.g., tick-level data, 10+ years), ECharts' Canvas/WebGL rendering handles millions of points.
**Current bottleneck**: None visible. Recharts with `isAnimationActive={false}` + `memo()` is performant at ~800 points.
**When to switch**: If brush interaction becomes janky with larger datasets.

### `use()` hook for data loading
React 19's `use()` can replace the `useEffect` + `useState` fetch pattern in `use-bundle.ts` (line 328). Can be called conditionally and inside loops — breaks traditional hook rules. Cleaner code but requires Suspense boundaries.
**Current pattern**: Works fine, single fetch + local computation. Not urgent.

### `useOptimistic` / `useActionState` (React 19)
New hooks for async state management. Not relevant today (no forms or mutations in the dashboard), but useful if you add settings, manual transaction entry, or portfolio rebalancing features.

### Tailwind v4.2 features already available
You're on Tailwind v4 already. Recent v4.2 additions you could use:
- **`not-*` variant**: Style elements that *don't* match a condition (e.g., `not-last:border-b`)
- **`inert` utility**: Disable a section visually + functionally during loading
- **New color palettes**: `mauve`, `olive`, `mist`, `taupe` — could enhance the glass morphism theme
- **`field-sizing` utility**: Auto-sizing inputs if you add search/filter UI

### Nivo as Recharts alternative
If Recharts hits limits, [Nivo](https://nivo.rocks) supports SVG, Canvas, and HTML rendering modes — switch per chart based on data size. Canvas mode is 3-9x faster than SVG for large datasets. More built-in chart types (waffle, sunburst, chord) for richer portfolio visualization.
**When to switch**: If you want richer chart types or Canvas performance on specific charts.

---

## P3 — Watch list (don't adopt yet)

### Vinext (Cloudflare's Vite-based Next.js alternative)
4.4x faster builds, 57% smaller bundles. Traffic-aware Pre-Rendering (TPR) pre-renders only pages with actual traffic. Experimental — built by one Cloudflare engineer in one week using AI. Wait for stability.
**Source**: https://blog.cloudflare.com/vinext/

### Cloudflare Workers AI
Workers AI now has its own dashboard section, AI Gateway, and cost analytics. Could be used for: AI-powered transaction categorization, natural language portfolio queries, or automated financial summaries. Overkill for current scope but interesting if the dashboard evolves toward AI features.

### Turbopack (default in Next.js 16.2)
Now the default bundler for new Next.js projects. Dev server starts 87% faster than 16.1. You're on 16.2 — check if Turbopack is enabled for dev. Production builds with Turbopack are also stable now.
```bash
# Check if you're using Turbopack
npx next info
```

---

## Not recommended

| Technology | Why skip |
|---|---|
| CSS Subgrid | Current grid layouts are flat, no nested alignment issues |
| Scroll-driven animations | Dashboard is functional, not marketing — animations add visual noise |
| Observable Plot | Recharts covers all current chart types, no statistical viz needed |
| Server Components for charts | Static export, no server. Charts need client interactivity |
| OpenNext migration | Static export works. Only needed if you add SSR/ISR |
| SciChart | GPU-accelerated, handles millions of points — overkill for ~800 daily points |
