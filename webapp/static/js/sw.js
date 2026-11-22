// Service worker: shows notifications pushed by the Mac running Featherstone, and opens the
// app when one is tapped. Served at /sw.js so it covers the whole app.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));

self.addEventListener("push", (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data && event.data.text() }; }
  const title = data.title || "Featherstone";
  event.waitUntil(self.registration.showNotification(title, {
    body: data.body || "Verses are due today.",
    icon: "/static/icons/icon-192.png",
    badge: "/static/icons/icon-192.png",
    tag: "scripture-daily",          // a newer notification replaces the older one
    data: { url: data.url || "/" },
  }));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/";
  event.waitUntil((async () => {
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if ("focus" in w) {
        if ("navigate" in w) { try { await w.navigate(url); } catch (e) {} }
        return w.focus();
      }
    }
    return self.clients.openWindow(url);
  })());
});
