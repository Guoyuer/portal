// ── Service Worker: offline-first static shell, stale-while-revalidate API ──

const CACHE_STATIC = "portal-static-v1";
const CACHE_API = "portal-api-v1";

// ── Install: precache app shell ─────────────────────────────────────────

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => k !== CACHE_STATIC && k !== CACHE_API)
          .map((k) => caches.delete(k)),
      ),
    ),
  );
  self.clients.claim();
});

// ── Fetch strategies ────────────────────────────────────────────────────

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // API: stale-while-revalidate
  if (url.pathname === "/timeline") {
    e.respondWith(
      caches.open(CACHE_API).then(async (cache) => {
        const cached = await cache.match(e.request);
        const fresh = fetch(e.request)
          .then((res) => {
            if (res.ok) cache.put(e.request, res.clone());
            return res;
          })
          .catch(() => cached);
        return cached || fresh;
      }),
    );
    return;
  }

  // Static assets: cache-first
  if (e.request.method === "GET" && url.origin === self.location.origin) {
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
