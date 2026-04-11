// ── Service Worker: cache-first for static assets ──

const CACHE_STATIC = "portal-static-v2";

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys.filter((k) => k !== CACHE_STATIC).map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // Only cache same-origin static assets under /_next/static/
  // Skip everything else (manifest.json, HTML, API) to avoid
  // Cloudflare Access redirect CORS errors
  if (
    e.request.method === "GET" &&
    url.origin === self.location.origin &&
    url.pathname.startsWith("/_next/static/")
  ) {
    e.respondWith(
      caches.match(e.request).then(
        (cached) =>
          cached ||
          fetch(e.request).then((res) => {
            if (res.ok) {
              const clone = res.clone();
              caches.open(CACHE_STATIC).then((c) => c.put(e.request, clone));
            }
            return res;
          }),
      ),
    );
  }
});
