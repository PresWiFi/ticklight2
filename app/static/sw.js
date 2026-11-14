// Ticklight service worker: offline shell, push notifications, notification buttons.
const SHELL = "ticklight-shell-v1";
const FILES = ["/", "/static/app.js", "/static/style.css", "/manifest.webmanifest",
               "/static/icons/icon-192.png", "/static/icons/badge-96.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k.startsWith("ticklight-shell") && k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// Network first; fall back to the cached shell when offline. API calls are never cached.
self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")
      || url.pathname.startsWith("/auth/")) return;
  e.respondWith(fetch(e.request).then((r) => {
    if (r.ok && FILES.includes(url.pathname)) {
      const copy = r.clone();
      caches.open(SHELL).then((c) => c.put(url.pathname, copy));
    }
    return r;
  }).catch(() => caches.match(e.request.mode === "navigate" ? "/" : url.pathname)));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data.json(); } catch { d = { title: "Ticklight", body: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || "Ticklight", {
    body: d.body || "",
    tag: d.tag,
    renotify: true,
    requireInteraction: !!d.requireInteraction,
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/badge-96.png",
    actions: d.actions || [],
    data: { url: d.url || "/" },
  }));
});

// The page stores its device token here (if it has one) so buttons work without opening the app.
async function authHeaders() {
  const r = await (await caches.open("ticklight-auth")).match("/__token");
  const token = r ? await r.text() : "";
  const h = { "Content-Type": "application/json" };
  if (token) h.Authorization = "Bearer " + token;
  return h;
}

async function openApp(url) {
  const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
  for (const w of wins) {
    if ("focus" in w) {
      if (url && url !== "/") w.navigate(url);
      return w.focus();
    }
  }
  return self.clients.openWindow(url || "/");
}

self.addEventListener("notificationclick", (e) => {
  const n = e.notification;
  n.close();
  if (!e.action) return e.waitUntil(openApp(n.data && n.data.url));
  e.waitUntil((async () => {
    try {
      const r = await fetch("/api/action", { method: "POST", credentials: "include",
        headers: await authHeaders(), body: JSON.stringify({ action: e.action }) });
      if (!r.ok) throw new Error(r.status);
      const res = await r.json();
      for (const w of await self.clients.matchAll({ type: "window" })) w.postMessage({ type: "refresh" });
      // Brief confirmation, replaces itself
      await self.registration.showNotification(res.message || "Done", {
        tag: "ticklight-ack", silent: true, badge: "/static/icons/badge-96.png",
        icon: "/static/icons/icon-192.png" });
      setTimeout(async () => (await self.registration.getNotifications({ tag: "ticklight-ack" })).forEach((x) => x.close()), 4000);
    } catch {
      await openApp(n.data && n.data.url);
    }
  })());
});
