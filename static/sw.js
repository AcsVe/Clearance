// App-shell cache + offline page only. No business data is cached:
// clearance and custody records always need a live connection.
const CACHE_NAME = 'clearance-shell-v2';
const SHELL = ['/static/offline.html', '/static/css/app.css', '/static/js/app.js',
  '/static/logo.png', '/static/icon-192.png', '/static/icon-512.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE_NAME).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((ns) => Promise.all(ns.filter((n) => n !== CACHE_NAME).map((n) => caches.delete(n)))));
  self.clients.claim();
});
self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).catch(() => caches.match('/static/offline.html')));
    return;
  }
  const url = new URL(req.url);
  if (req.method === 'GET' && url.origin === location.origin && url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(req).then((hit) => hit || fetch(req)));
  }
});

// Web Push: show the notification even when the app is closed.
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { title: e.data && e.data.text() }; }
  e.waitUntil(self.registration.showNotification(d.title || 'إشعار جديد', {
    body: d.body || '', icon: '/static/icon-192.png', badge: '/static/icon-192.png',
    dir: 'rtl', lang: 'ar', data: { url: d.url || '/' },
  }));
});
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  const target = url.startsWith('/') && !url.startsWith('//') ? url : '/';
  e.waitUntil(clients.matchAll({ type: 'window', includeUncontrolled: true }).then((ws) => {
    for (const w of ws) { if ('focus' in w) { w.navigate(target); return w.focus(); } }
    return clients.openWindow(target);
  }));
});
