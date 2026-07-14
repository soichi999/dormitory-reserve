const CACHE = "shokudo-v1";
const ASSETS = ["./", "./index.html", "./manifest.json", "./icon.svg"];

self.addEventListener("install", e =>
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(ASSETS)))
);

self.addEventListener("fetch", e => {
  if (e.request.url.includes("/reserve") || e.request.url.includes("/health") || e.request.url.includes("/subscribe")) return;
  e.respondWith(
    fetch(e.request).catch(() => caches.match(e.request))
  );
});

self.addEventListener("push", e => {
  const data = e.data ? e.data.json() : { title: "🍱 食堂予約", body: "来週の食事を予約しましょう！" };
  e.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      icon: "./icon.svg",
    })
  );
});
