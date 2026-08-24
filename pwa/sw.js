const CACHE = "shokudo-v5";
const ASSETS = ["./", "./index.html", "./manifest.json", "./icon.svg", "./bg.jpg"];

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
