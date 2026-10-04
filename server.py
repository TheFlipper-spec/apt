#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Расписание АПТ — локальный сервер («живой» режим).
Только стандартная библиотека. Порт: 8000 (или env PORT).

API:
  GET /api/health                      -> проверка живости
  GET /api/groups                      -> список групп с курсами
  GET /api/bells                       -> время пар
  GET /api/schedule?group=ID&date=YYYY-MM-DD
  GET /api/lessons?date=YYYY-MM-DD     -> все занятия дня по всем группам
                                          (поиск преподавателя/аудитории)
Всё, что не /api/, отдаётся как статика из папки static/.
"""
import json
import mimetypes
import os
import re
import sys
import time
import threading
import urllib.error
import urllib.request
import concurrent.futures as cf
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs, unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import parser as P

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE, "static")
DATA_DIR = os.path.join(BASE, "data")
CACHE_DIR = os.path.join(DATA_DIR, "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
SITE = "https://almetpt.ru"

GROUPS_TTL = 6 * 3600          # список групп — раз в 6 часов
DAY_TTL = 10 * 60              # расписание дня — 10 минут
BELLS_TTL = 24 * 3600
LESSONS_TTL = 15 * 60

_lock = threading.Lock()
_mem = {"groups": None, "groups_ts": 0, "bells": None, "bells_ts": 0,
        "days": {}, "lessons": {}}


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
    saved = _read_json(os.path.join(DATA_DIR, "groups.json")) or {}
    groups = saved.get("groups") or []
    ts = saved.get("fetched_at", 0)
    if groups:
        with _lock:
            _mem["groups"] = groups
            _mem["groups_ts"] = max(_mem["groups_ts"], ts or now)
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
        if not data.get("group") and not data.get("pairs"):
            raise ValueError("ответ не похож на страницу расписания")
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
        return {"ok": False,
                "error": f"Не удалось получить расписание ({e.__class__.__name__}). "
                         "Проверьте соединение или попробуйте позже.",
                "groupId": int(group_id), "date": date}


def get_lessons(date):
    """Срез всех занятий дня по всем группам — для поиска преподавателя/аудитории."""
    now = time.time()
    with _lock:
        ent = _mem["lessons"].get(date)
        if ent and now - ent["ts"] < LESSONS_TTL:
            return ent["data"]
    groups, _ts, _src = get_groups()
    out = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        futs = {ex.submit(get_day, g["id"], date): g for g in groups}
        for fut in cf.as_completed(futs):
            g = futs[fut]
            try:
                d = fut.result()
            except Exception:
                continue
            for p in (d.get("pairs") or []):
                out.append({"g": g["id"], "gn": g["name"], "n": p.get("n", ""),
                            "s": p.get("start", ""), "e": p.get("end", ""),
                            "subj": p.get("subject", "") or p.get("subjectFull", ""),
                            "t": p.get("teacher", ""), "r": p.get("room", ""),
                            "sg": p.get("subgroup", ""),
                            "ch": 1 if p.get("changed") else 0})
    data = {"ok": True, "date": date, "lessons": out}
    with _lock:
        _mem["lessons"][date] = {"data": data, "ts": now}
    return data


# ------------------------------------------------------------------ http
class H(BaseHTTPRequestHandler):
    server_version = "APT-Schedule/2.0"
    protocol_version = "HTTP/1.1"

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
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------------------------------------------------- статика
    def _serve_static(self, path):
        rel = unquote(path.lstrip("/")) or "index.html"
        if rel.endswith("/"):
            rel += "index.html"
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self._send(404, {"ok": False, "error": "not found"})
            return
        ctype, _ = mimetypes.guess_type(full)
        if full.endswith(".webmanifest"):
            ctype = "application/manifest+json"
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, (ctype or "application/octet-stream") +
                   ("; charset=utf-8" if (ctype or "").startswith("text/") else ""))

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urlparse(self.path)
        path = u.path
        q = parse_qs(u.query)

        if not path.startswith("/api/"):
            self._serve_static(path)
            return

        if path == "/api/health":
            self._send(200, {"ok": True, "ts": int(time.time()), "mode": "live"})
            return

        if path == "/api/groups":
            groups, ts, src = get_groups()
            self._send(200, {"ok": True, "groups": groups, "fetchedAt": ts, "source": src})
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
            if not self._valid_date(date):
                return
            data = get_day(int(gid), date)
            self._send(200 if data.get("ok") else 502, data)
            return

        if path == "/api/lessons":
            date = (q.get("date") or [""])[0].strip()
            if not self._valid_date(date):
                return
            self._send(200, get_lessons(date))
            return

        self._send(404, {"ok": False, "error": "not found"})

    def _valid_date(self, date):
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date) or not (2020 <= int(date[:4]) <= 2100):
            self._send(400, {"ok": False, "error": "Некорректная дата"})
            return False
        return True


def main():
    port = int(os.environ.get("PORT", "8000"))
    srv = ThreadingHTTPServer(("0.0.0.0", port), H)
    print(f"APT-Schedule server: http://0.0.0.0:{port}")
    for warm in (get_groups, get_bells):
        try:
            warm()
        except Exception:
            pass
    srv.serve_forever()


if __name__ == "__main__":
    main()
