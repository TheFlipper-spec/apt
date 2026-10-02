#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Расписание АПТ — локальный сервер.
Только стандартная библиотека. Порт: 8000 (или env PORT).

API:
  GET /api/groups                     -> список групп с курсами
  GET /api/schedule?group=ID&date=YYYY-MM-DD
  GET /api/health
"""
import json
import os
import re
import sys
import time
import threading
import urllib.request
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parser as P

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SITE = "https://almetpt.ru"

GROUPS_TTL = 6 * 3600          # список групп — раз в 6 часов
DAY_TTL = 10 * 60              # расписание дня — 10 минут
BELLS_TTL = 24 * 3600

_lock = threading.Lock()
_mem = {"groups": None, "groups_ts": 0, "bells": None, "bells_ts": 0, "days": {}}


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru,en;q=0.8",
    })
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return raw.decode("utf-8", "replace")


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json(path, obj):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass


# ------------------------------------------------------------------ data
def get_groups():
    now = time.time()
    with _lock:
        if _mem["groups"] and now - _mem["groups_ts"] < GROUPS_TTL:
            return _mem["groups"], _mem["groups_ts"], "live"
    try:
        html = http_get(SITE + "/2020/site/schedulegroups")
        groups = P.parse_groups(html)
        if groups:
            with _lock:
                _mem["groups"], _mem["groups_ts"] = groups, now
            _write_json(os.path.join(DATA_DIR, "groups.json"),
                        {"groups": groups, "fetched_at": int(now)})
            return groups, now, "live"
    except Exception as e:
        print("groups fetch failed:", e, file=sys.stderr)
    # fallback: файл
    saved = _read_json(os.path.join(DATA_DIR, "groups.json")) or {}
    groups = saved.get("groups") or []
    ts = saved.get("fetched_at", 0)
    if groups:
        with _lock:
            _mem["groups"], _mem["groups_ts"] = groups, max(_mem["groups_ts"], ts or now)
    return groups, ts, "cache"


def get_bells():
    now = time.time()
    with _lock:
        if _mem["bells"] and now - _mem["bells_ts"] < BELLS_TTL:
            return _mem["bells"]
    try:
        html = http_get(SITE + "/2020/site/schedulecalls")
        bells = P.parse_bells(html)
        if bells:
            with _lock:
                _mem["bells"], _mem["bells_ts"] = bells, now
            _write_json(os.path.join(DATA_DIR, "bells.json"),
                        {"bells": bells, "fetched_at": int(now)})
            return bells
    except Exception as e:
        print("bells fetch failed:", e, file=sys.stderr)
    saved = _read_json(os.path.join(DATA_DIR, "bells.json")) or {}
    bells = saved.get("bells") or P.BELLS_FALLBACK
    with _lock:
        _mem["bells"] = [tuple(b) for b in bells]
        _mem["bells_ts"] = now
    return _mem["bells"]


def bell_map():
    return {r: (s, e) for r, s, e in get_bells()}


def get_day(group_id, date):
    """date: YYYY-MM-DD"""
    key = f"{group_id}_{date}"
    now = time.time()
    with _lock:
        ent = _mem["days"].get(key)
        if ent and now - ent["ts"] < DAY_TTL:
            return ent["data"]
    url = f"{SITE}/2020/site/schedule/group/{group_id}/{date}"
    disk_path = os.path.join(CACHE_DIR, f"day_{key}.json")
    try:
        html = http_get(url)
        if "Страничка не найдена" in html:
            raise ValueError("page 404")
        data = P.build_day(int(group_id), date, html, bell_map())
        data["fetchedAt"] = int(now)
        with _lock:
            _mem["days"][key] = {"data": data, "ts": now}
        _write_json(disk_path, data)
        return data
    except Exception as e:
        print("day fetch failed:", url, e, file=sys.stderr)
        saved = _read_json(disk_path)
        if saved and saved.get("ok"):
            saved["stale"] = True
            with _lock:
                _mem["days"][key] = {"data": saved, "ts": now}
            return saved
        return {"ok": False, "error": f"Не удалось получить расписание ({e.__class__.__name__}). "
                                      "Проверьте соединение или попробуйте позже.",
                "groupId": int(group_id), "date": date}


# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    server_version = "APT-Schedule/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code, body, ctype="application/json; charset=utf-8", extra=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, str):
            body = body.encode("utf-8")
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if path in ("/", "/index.html"):
            try:
                with open(os.path.join(BASE, "static", "index.html"), "rb") as f:
                    body = f.read()
                self._send(200, body, "text/html; charset=utf-8")
            except Exception:
                self._send(500, {"ok": False, "error": "index.html not found"})
            return

        if path == "/api/health":
            self._send(200, {"ok": True, "ts": int(time.time())})
            return

        if path == "/api/groups":
            groups, ts, src = get_groups()
            self._send(200, {"ok": True, "groups": groups,
                             "fetchedAt": ts, "source": src})
            return

        if path == "/api/bells":
            self._send(200, {"ok": True, "bells": get_bells()})
            return

        if path == "/api/schedule":
            gid = (q.get("group") or [""])[0].strip()
            date = (q.get("date") or [""])[0].strip()
            if not re.fullmatch(r"\d+", gid):
                self._send(400, {"ok": False, "error": "Некорректный id группы"})
                return
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
                self._send(400, {"ok": False, "error": "Некорректная дата"})
                return
            # не даём уйти слишком далеко
            try:
                y = int(date[:4])
                if not (2020 <= y <= 2100):
                    raise ValueError
            except ValueError:
                self._send(400, {"ok": False, "error": "Некорректная дата"})
                return
            data = get_day(int(gid), date)
            self._send(200 if data.get("ok") else 502, data)
            return

        self._send(404, {"ok": False, "error": "not found"})


def main():
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"APT-Schedule server: http://0.0.0.0:{port}")
    # прогрев кэша
    try:
        get_groups()
        get_bells()
    except Exception:
        pass
    srv.serve_forever()


if __name__ == "__main__":
    main()
