// Clear Sky — service worker: push notifications + app shell (no caching of live data)
self.addEventListener('install', e => { self.skipWaiting(); });
// the page can ask the waiting worker to take over immediately (the "new version" bar does this)
self.addEventListener('message', e => { if (e.data === 'skipWaiting') self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(self.clients.claim()); });

self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (_) { d = { title: 'Clear Sky', body: e.data && e.data.text() }; }
  const title = d.title || 'Clear Sky';
  const opts = {
    body: d.body || '',
    icon: '/static/logo-cs-192.png',
    badge: '/static/logo-cs-badge.png',   // white on transparent: Android draws a badge from its alpha alone
    tag: d.tag || 'alert',
    renotify: true,
    requireInteraction: d.tag === 'threat' || /🔴|🚀|MiG/.test(title),
    vibrate: d.tag === 'threat' || /🔴|🚀/.test(title) ? [300, 120, 300, 120, 600] : [150],
    timestamp: d.ts ? Date.parse(d.ts) : Date.now(),
    data: { url: d.url || '/m' }
  };
  e.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/m';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    for (const c of list) { if ('focus' in c) { c.navigate && c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
