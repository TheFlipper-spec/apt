/* Расписание АПТ — сервис-воркер.
   Оболочка приложения кэшируется навсегда (до смены версии),
   данные расписания — «сеть вперёд, кэш как запасной аэродром». */
"use strict";

const VERSION = "apt-v3";
const SHELL = `${VERSION}-shell`;
const DATA = `${VERSION}-data`;

const SHELL_FILES = [
  "./",
  "./index.html",
  "./manifest.webmanifest",
  "./icons/icon-192.png",
  "./icons/icon-512.png",
  "./icons/maskable-512.png",
  "./icons/favicon-32.png",
];

self.addEventListener("install", (e) => {
  e.waitUntil((async () => {
    const c = await caches.open(SHELL);
    // не валим установку из-за одного недоступного файла
    await Promise.all(SHELL_FILES.map((u) => c.add(u).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", (e) => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter((k) => !k.startsWith(VERSION)).map((k) => caches.delete(k)));
    if (self.registration.navigationPreload) {
      try { await self.registration.navigationPreload.enable(); } catch (_) { /* ignore */ }
    }
    await self.clients.claim();
  })());
});

self.addEventListener("message", (e) => {
  if (e.data === "skipWaiting") self.skipWaiting();
  if (e.data === "clearData") caches.delete(DATA);
});

function isData(url) {
  return /\/data\/.*\.json$/.test(url.pathname) || /\/api\//.test(url.pathname);
}

async function networkFirst(req, cacheName, timeoutMs) {
  const cache = await caches.open(cacheName);
  try {
    const ctrl = new AbortController();
    const t = timeoutMs ? setTimeout(() => ctrl.abort(), timeoutMs) : null;
    const res = await fetch(req, { signal: ctrl.signal });
    if (t) clearTimeout(t);
    if (res && res.ok) cache.put(req, res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(req, { ignoreSearch: true });
    if (hit) {
      // помечаем ответ как «из офлайн-копии», фронтенд это показывает
      const body = await hit.blob();
      const headers = new Headers(hit.headers);
      headers.set("X-From-Cache", "1");
      return new Response(body, { status: 200, headers });
    }
    throw err;
  }
}

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;

  // навигация: свежий HTML, иначе оболочка из кэша
  if (req.mode === "navigate") {
    e.respondWith((async () => {
      try {
        const pre = await e.preloadResponse;
        if (pre) return pre;
        return await networkFirst(req, SHELL, 4000);
      } catch (_) {
        const c = await caches.open(SHELL);
        return (await c.match("./index.html")) || (await c.match("./")) ||
               new Response("Офлайн", { status: 503 });
      }
    })());
    return;
  }

  if (isData(url)) {
    e.respondWith(networkFirst(req, DATA, 8000).catch(() =>
      new Response(JSON.stringify({ ok: false, offline: true, error: "Нет сети и нет копии" }),
                   { status: 503, headers: { "Content-Type": "application/json" } })));
    return;
  }

  // статика: кэш вперёд
  e.respondWith((async () => {
    const c = await caches.open(SHELL);
    const hit = await c.match(req);
    if (hit) return hit;
    const res = await fetch(req);
    if (res && res.ok) c.put(req, res.clone());
    return res;
  })());
});
