// Minimal service worker. Registering it (see app/register-sw.tsx) is what lets
// the browser treat Ranger-X as an installable app. It caches NOTHING and
// intercepts NOTHING: the app is a real-time WebSocket relay, offline is
// useless, and a no-op SW can't serve stale assets.
//
// ponytail: the empty fetch handler is cross-engine install insurance — some
// browsers gate installability on the SW having a fetch handler. It never calls
// respondWith, so the network path is unchanged. Do NOT add respondWith/caching
// unless offline becomes a goal. To remove this SW later, do NOT just delete the
// file (registered clients keep running the old one) — ship a self-unregistering
// SW that calls self.registration.unregister().
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (event) => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
