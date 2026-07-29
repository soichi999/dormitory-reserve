const CACHE = "shokudo-v4";
const ASSETS = ["./", "./index.html", "./manifest.json", "./icon.svg"];

self.addEventListener("install", e =>
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)))
);

self.addEventListener("fetch", e => {
  // API リクエストはキャッシュしない
  if (e.request.url.includes("onrender.com")) return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});
