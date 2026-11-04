const CACHE = "nbaq-v17";
const ASSETS = [
  "./",
  "static/styles.css",
  "static/app.js",
  "static/research.js",
  "static/live.js",
  "static/bets.js",
  "static/static.js",
  "static/icon.svg",
  "manifest.webmanifest",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(ASSETS))
      .catch(() => null)
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match(event.request).then(
          (cached) =>
            cached ||
            new Response(JSON.stringify({ detail: "offline" }), {
              status: 503,
              headers: { "Content-Type": "application/json" },
            })
        )
      )
    );
    return;
  }

  // 数据文件：network-first（Actions 会更新数据，缓存只作离线兜底）
  if (url.pathname.includes("/data/")) {
    event.respondWith(
      fetch(event.request)
        .then((resp) => {
          const copy = resp.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
          return resp;
        })
        .catch(() => caches.match(event.request).then((cached) => cached || Response.error()))
    );
    return;
  }

  // 其余静态资源（HTML/JS/CSS）：也走 network-first——每日部署后立刻拿到
  // 新 UI，不再依赖"记得改 CACHE 版本"；断网时回落到缓存。
  event.respondWith(
    fetch(event.request)
      .then((resp) => {
        if (resp.ok && resp.type === "basic") {
          const copy = resp.clone();
          caches.open(CACHE).then((cache) => cache.put(event.request, copy));
        }
        return resp;
      })
      .catch(() => caches.match(event.request).then((cached) => cached || Response.error()))
  );
});
