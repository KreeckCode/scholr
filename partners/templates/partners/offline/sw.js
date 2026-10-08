'use strict';

const CONFIG = {{ worker_config|safe }};
const CACHE = CONFIG.cache;
const APP_URL = CONFIG.appUrl;
const ASSETS = CONFIG.assets;
const SYNC_URL = CONFIG.syncUrl;

importScripts({{ core_url|safe }});

self.addEventListener('install', (event) => {
    event.waitUntil(
        caches.open(CACHE)
            .then((cache) => cache.addAll(ASSETS).then(() => fetch(APP_URL, { credentials: 'same-origin' }).then((response) => {
                if (response.ok && !response.redirected) {
                    return cache.put(APP_URL, response);
                }
                return undefined;
            })))
            .catch(() => undefined)
            .then(() => self.skipWaiting())
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((keys) => Promise.all(keys.filter((key) => key.startsWith('metp-offline') && key !== CACHE).map((key) => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', (event) => {
    const request = event.request;
    if (request.method !== 'GET') {
        return;
    }
    const url = new URL(request.url);
    if (url.origin !== self.location.origin) {
        return;
    }

    if (request.mode === 'navigate' && url.pathname === APP_URL) {
        event.respondWith(
            fetch(request)
                .then((response) => {
                    if (response.ok && !response.redirected) {
                        const copy = response.clone();
                        caches.open(CACHE).then((cache) => cache.put(APP_URL, copy));
                    }
                    return response;
                })
                .catch(() => caches.match(APP_URL).then((cached) => cached || Response.error()))
        );
        return;
    }

    if (ASSETS.includes(url.pathname)) {
        event.respondWith(
            caches.match(url.pathname).then((cached) => cached || fetch(request).then((response) => {
                if (response.ok) {
                    const copy = response.clone();
                    caches.open(CACHE).then((cache) => cache.put(url.pathname, copy));
                }
                return response;
            }))
        );
    }
});

self.addEventListener('sync', (event) => {
    if (event.tag !== 'metp-outbox') {
        return;
    }
    event.waitUntil(
        self.MetpOffline.flush({ syncUrl: SYNC_URL })
            .then(() => self.clients.matchAll({ type: 'window' }))
            .then((clients) => clients.forEach((client) => client.postMessage({ type: 'metp-synced' })))
    );
});
