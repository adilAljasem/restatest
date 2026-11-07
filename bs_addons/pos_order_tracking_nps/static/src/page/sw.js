/* Service worker for the order tracking page: shows web-push notifications
 * (sound + vibration on the lock screen) and tells open pages to refresh/ring. */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) =>
    event.waitUntil(
        // Nothing is cached on purpose: drop anything an older version may have stored.
        caches.keys()
            .then((keys) => Promise.all(keys.map((k) => caches.delete(k))))
            .catch(() => {})
            .then(() => self.clients.claim())
    )
);

self.addEventListener("push", (event) => {
    let payload = {};
    try {
        payload = event.data ? event.data.json() : {};
    } catch (e) {
        payload = { title: event.data ? event.data.text() : "" };
    }
    const title = payload.title || "";
    const options = Object.assign({ silent: false }, payload.options || {});
    const data = options.data || {};
    event.waitUntil(
        Promise.all([
            self.registration.showNotification(title, options),
            // Wake the page (if alive in the background) so it starts its own ringing.
            self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) =>
                clients.forEach((c) => c.postMessage({ type: "tracking-push", kind: data.kind }))
            ),
        ])
    );
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const data = event.notification.data || {};
    const url = data.url || "/track/";
    const ack = data.ack ? fetch(data.ack, { method: "POST", credentials: "same-origin" }).catch(() => {}) : null;
    event.waitUntil(
        Promise.all([
            ack,
            self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clients) => {
                for (const client of clients) {
                    if (client.url.includes(url) && "focus" in client) {
                        return client.focus();
                    }
                }
                return self.clients.openWindow(url);
            }),
        ])
    );
});
