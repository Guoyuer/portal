import type { NextConfig } from "next";
import path from "path";
import bundleAnalyzer from "@next/bundle-analyzer";

const nextConfig: NextConfig = {
  output: "export",
  reactCompiler: true,
  experimental: {
    viewTransition: true,
  },
  turbopack: {
    root: path.resolve(__dirname),
  },
};

// ── Bundle analyzer (inert unless ANALYZE=true) ──────────────
// The `@next/bundle-analyzer` plugin only emits reports under webpack, not
// Turbopack (Next 16's default). In CI, `ANALYZE=true npx next build --webpack`
// will produce `.next/analyze/client.html` and friends.
const withBundleAnalyzer = bundleAnalyzer({
  enabled: process.env.ANALYZE === "true",
});

export default withBundleAnalyzer(nextConfig);
