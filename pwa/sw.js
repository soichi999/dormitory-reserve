const CACHE = "shokudo-v6";
const ASSETS = ["./", "./index.html", "./manifest.json", "./icon.svg", "./bg.jpg"];

self.addEventListener("install", e => {
  self.skipWaiting();
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)));
});

self.addEventListener("activate", e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", e => {
  // API リクエストはキャッシュしない
  if (e.request.url.includes("onrender.com")) return;
  e.respondWith(
    fetch(e.request).catch(() =>
      // オフライン時のフォールバックは必ず現在のキャッシュからのみ取得する
      // (caches.match()は全世代のキャッシュを横断検索するため、
      //  古いバージョンのindex.htmlが返ってしまうバグがあった)
      caches.open(CACHE).then(c => c.match(e.request))
    )
  );
});
