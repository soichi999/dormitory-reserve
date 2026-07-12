const CACHE = "shokudo-v1";
const ASSETS = ["./", "./index.html", "./manifest.json", "./icon.svg"];

self.addEventListener("install", e =>
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)))
);

self.addEventListener("fetch", e => {
  // API リクエストはキャッシュしない
  if (e.request.url.includes("/reserve") || e.request.url.includes("/health")) return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
