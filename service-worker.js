'use strict';

// Bump this version string whenever index.html/style.css/app.js change,
// so returning visitors get the new shell instead of a stale cached one.
const CACHE_VERSION = 'brizzi-daily-a53e0533c9';
const SHELL_CACHE = `${CACHE_VERSION}-shell`;
const DATA_CACHE = `${CACHE_VERSION}-data`;

const SHELL_FILES = [
  './',
  './index.html',
  './style.css',
  './app.js',
  './manifest.json',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/icon-512-maskable.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith('brizzi-daily-') && key !== SHELL_CACHE && key !== DATA_CACHE)
          .map((key) => caches.delete(key))
      )
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // posts.json: NETWORK-FIRST. Always try to get the freshest data when
  // online (so new daily posts show up without reinstalling the app);
  // fall back to the last cached copy only when offline.
  if (url.pathname.endsWith('posts.json')) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const copy = response.clone();
          caches.open(DATA_CACHE).then((cache) => cache.put(event.request, copy));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
    return;
  }

  // Everything else (app shell): CACHE-FIRST, since it rarely changes
  // and should load instantly, including offline.
  event.respondWith(
    caches.match(event.request).then((cached) => cached || fetch(event.request))
  );
});
