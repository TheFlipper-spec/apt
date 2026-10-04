#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Служебный скрипт (запускается только в GitHub Actions).

Скачивает с almetpt.ru набор «эталонных» страниц и кладёт их в
tests/fixtures/*.html.gz, чтобы парсер можно было проверять локально,
без доступа к сайту. Рядом пишет report.json — что из этих страниц
вытащил текущий парсер.

Запуск:  python3 tools/capture_fixtures.py
"""
import gzip
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import parser as P  # noqa: E402

SITE = "https://almetpt.ru"
OUT = os.path.join(BASE, "tests", "fixtures")
TZ = ZoneInfo("Europe/Moscow")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")


def get(url):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "ru,en;q=0.8",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
    return raw.decode("utf-8", "replace")


def save(name, text):
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, name + ".html.gz")
    with gzip.open(path, "wt", encoding="utf-8") as f:
        f.write(text)
    return path


def main():
    today = datetime.now(TZ).date()
    report = {"capturedAt": datetime.now(TZ).isoformat(timespec="seconds"), "pages": []}

    jobs = [("groups", f"{SITE}/2020/site/schedulegroups"),
            ("bells", f"{SITE}/2020/site/schedulecalls")]
    # несколько групп × несколько дат вокруг сегодня
    for gid in (1042, 1058, 1094, 1035):
        for off in (-3, -1, 0, 1, 2, 4):
            d = (today + timedelta(days=off)).isoformat()
            jobs.append((f"day_{gid}_{d}", f"{SITE}/2020/site/schedule/group/{gid}/{d}"))
    jobs.append(("day_404", f"{SITE}/2020/site/schedule/group/999999/{today.isoformat()}"))

    for name, url in jobs:
        rec = {"name": name, "url": url}
        try:
            html = get(url)
            save(name, html)
            rec["bytes"] = len(html)
            if name == "groups":
                g = P.parse_groups(html)
                rec["parsed"] = {"groups": len(g), "sample": g[:3]}
            elif name == "bells":
                rec["parsed"] = {"bells": P.parse_bells(html)}
            else:
                bells = {r: (s, e) for r, s, e in P.BELLS_FALLBACK}
                d = P.build_day(1, name.split("_")[-1], html, bells)
                rec["parsed"] = {
                    "group": d["group"], "dateText": d["dateText"],
                    "published": d["published"], "pairs": len(d["pairs"]),
                    "notices": d["notices"],
                    "first": d["pairs"][0] if d["pairs"] else None,
                }
        except Exception as e:
            rec["error"] = f"{e.__class__.__name__}: {e}"
        report["pages"].append(rec)
        print(json.dumps(rec, ensure_ascii=False)[:400], flush=True)
        time.sleep(0.3)

    with open(os.path.join(OUT, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    print("готово")


if __name__ == "__main__":
    main()
