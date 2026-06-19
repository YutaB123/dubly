// Service worker: caches the app shell, and shows push notifications so the
// assistant can reach you even when the app is closed.
const CACHE = 'dubly-v26';
const SHELL = ['/chat', '/manifest.webmanifest', '/static/icon-192.png', '/static/icon-badge.png', '/static/dubs.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (url.pathname.startsWith('/chat/') || url.pathname.startsWith('/file/')) return; // network only
  // App shell / navigations: network-first so updates show up right away,
  // falling back to cache only when offline.
  if (e.request.mode === 'navigate' || url.pathname === '/chat') {
    e.respondWith(
      fetch(e.request).then(r => {
        const copy = r.clone();
        caches.open(CACHE).then(c => c.put(e.request, copy)).catch(() => {});
        return r;
      }).catch(() => caches.match(e.request))
    );
    return;
  }
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request)));
});

// A push arrived from the server.
self.addEventListener('push', e => {
  let data = { title: 'Study Assistant', body: 'New message', url: '/chat' };
  try { data = Object.assign(data, e.data.json()); } catch (_) {}
  e.waitUntil((async () => {
    // Only skip the buzz if the chat is actually ON-SCREEN (like SMS — no
    // notification while you're looking at the thread). If you've left the
    // app (hidden/backgrounded/closed), always notify. `force` overrides.
    const clientsArr = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const onScreen = clientsArr.some(c => c.visibilityState === 'visible');
    if (onScreen && !data.force) return;
    await self.registration.showNotification(data.title, {
      body: data.body,
      icon: '/static/icon-192.png',
      badge: '/static/icon-badge.png',
      data: { url: data.url || '/chat' },
      tag: 'study-msg',
      renotify: true,
      requireInteraction: false,
      actions: [{ action: 'open', title: 'Reply' }],
    });
  })());
});

// Tapping the notification opens/focuses the app.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const target = (e.notification.data && e.notification.data.url) || '/chat';
  e.waitUntil((async () => {
    const all = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    for (const c of all) { if ('focus' in c) { c.navigate(target); return c.focus(); } }
    if (self.clients.openWindow) return self.clients.openWindow(target);
  })());
});
