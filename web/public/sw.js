// Hearsay service worker: keeps the app shell available offline. The API and audio are
// never cached here (offline episodes are M7.1, design.md §13). Web Push arrives in M6.2.

const CACHE = 'hearsay-shell-v1'
const SHELL = ['/', '/manifest.webmanifest', '/icon.svg', '/apple-touch-icon.png']

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches
      .open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting()),
  )
})

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (event) => {
  const request = event.request
  const url = new URL(request.url)
  if (request.method !== 'GET' || url.origin !== self.location.origin) return
  // Live data, and audio with Range requests: straight to the network.
  if (url.pathname.startsWith('/api/')) return

  // Built assets (fonts included) have content hashes in their names, so a cached copy is
  // always right.
  if (url.pathname.startsWith('/assets/')) {
    event.respondWith(
      caches.match(request).then(
        (hit) =>
          hit ??
          fetch(request).then((response) => {
            if (response.ok) {
              const copy = response.clone()
              void caches.open(CACHE).then((cache) => cache.put(request, copy))
            }
            return response
          }),
      ),
    )
    return
  }

  // Everything else (the page itself, deep links, icons): the network first, so updates
  // arrive. Every navigation is the same app shell.
  const key = request.mode === 'navigate' ? '/' : request
  event.respondWith(
    fetch(request)
      .then((response) => {
        if (response.ok) {
          const copy = response.clone()
          void caches.open(CACHE).then((cache) => cache.put(key, copy))
        }
        return response
      })
      .catch(() => caches.match(key).then((hit) => hit ?? Response.error())),
  )
})
