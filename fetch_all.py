#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот для GitHub Actions: собирает расписание almetpt.ru в статические JSON.

Результат (папка docs/ — её и раздаёт GitHub Pages):
  docs/index.html                     <- копия фронтенда (static/index.html)
  docs/.nojekyll
  docs/data/groups.json               <- список групп
  docs/data/bells.json                <- время пар
  docs/data/meta.json                 <- когда собрано, за какие даты есть данные
  docs/data/d/<groupId>/<date>.json   <- расписание группы на день

Запуск:
  python3 fetch_all.py                     # даты: вчера .. +3 дня (МСК)
  python3 fetch_all.py --days -2..3 --workers 4
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import parser as P

SITE = "https://almetpt.ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TZ = ZoneInfo("Europe/Moscow")


def log(*a):
    print(time.strftime("[%H:%M:%S]"), *a, flush=True)


def http_get(url, timeout=25, retries=3):
    err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru,en;q=0.8",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode("utf-8", "replace")
        except Exception as e:
            err = e
            time.sleep(1.5 * attempt)
    raise err


def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def rjson(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def parse_days_arg(s):
    m = re.fullmatch(r"(-?\d+)\.\.(\+?-?\d+)", s.strip())
    if not m:
        raise SystemExit(f"Неверный формат --days: {s!r} (пример: -1..3)")
    return int(m.group(1)), int(m.group(2))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="-1..3", help="диапазон дат от сегодня, напр. -1..3")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--out", default=os.path.join(BASE, "docs"))
    args = ap.parse_args()
    d0, d1 = parse_days_arg(args.days)

    today = datetime.now(TZ).date()
    dates = []
    for off in range(min(d0, d1), max(d0, d1) + 1):
        d = today + timedelta(days=off)
        if d.weekday() != 6:                     # воскресенья пропускаем
            dates.append(d.isoformat())
    log("Даты сборки:", ", ".join(dates))

    out = args.out
    os.makedirs(out, exist_ok=True)
    data_dir = os.path.join(out, "data")
    os.makedirs(data_dir, exist_ok=True)

    # ------------------------------------------------ 1. фронтенд -> docs/index.html
    with open(os.path.join(BASE, "static", "index.html"), encoding="utf-8") as f:
        html = f.read()
    with open(os.path.join(out, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    open(os.path.join(out, ".nojekyll"), "w").close()

    # ------------------------------------------------ 2. группы + звонки
    groups = None
    try:
        groups = P.parse_groups(http_get(SITE + "/2020/site/schedulegroups"))
        log(f"Групп получено: {len(groups)}")
    except Exception as e:
        log("!! не удалось получить список групп:", e)
    if not groups:
        saved = rjson(os.path.join(data_dir, "groups.json"))
        if not saved or not saved.get("groups"):
            saved = rjson(os.path.join(BASE, "data", "groups.json"))
        if not saved or not saved.get("groups"):
            raise SystemExit("Нет списка групп ни с сайта, ни из кэша — стоп.")
        groups = saved["groups"]
        log(f"Взяли группы из кэша: {len(groups)}")
    wjson(os.path.join(data_dir, "groups.json"),
          {"ok": True, "groups": groups, "fetchedAt": int(time.time()), "source": "bot"})

    try:
        bells = P.parse_bells(http_get(SITE + "/2020/site/schedulecalls"))
    except Exception as e:
        log("!! звонки не получены, fallback:", e)
        saved = rjson(os.path.join(data_dir, "bells.json")) or \
                rjson(os.path.join(BASE, "data", "bells.json")) or {}
        bells = saved.get("bells") or P.BELLS_FALLBACK
    bells = [tuple(map(str, b)) for b in bells]
    wjson(os.path.join(data_dir, "bells.json"),
          {"ok": True, "bells": bells, "fetchedAt": int(time.time()), "source": "bot"})
    bell_map = {r: (s, e) for r, s, e in bells}

    # ------------------------------------------------ 3. расписание по группам и датам
    tasks = [(g["id"], date) for date in dates for g in groups]
    log(f"Всего загрузок: {len(tasks)} (групп {len(groups)} × дней {len(dates)})")

    ok = fail = skipped = 0
    failed = []

    def load(task):
        nonlocal skipped
        gid, date = task
        path = os.path.join(data_dir, "d", str(gid), f"{date}.json")
        # если файл свежий (<25 мин) — пропускаем (экономим запросы при частых прогонах)
        try:
            if time.time() - os.path.getmtime(path) < 25 * 60:
                skipped += 1
                return True
        except OSError:
            pass
        url = f"{SITE}/2020/site/schedule/group/{gid}/{date}"
        html_txt = http_get(url)
        if "Страничка не найдена" in html_txt:
            raise ValueError("404")
        data = P.build_day(gid, date, html_txt, bell_map)
        data["fetchedAt"] = int(time.time())
        data["source"] = "bot"
        wjson(path, data)
        return True

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(load, t): t for t in tasks}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            t = futs[fut]
            try:
                fut.result()
                ok += 1
            except Exception as e:
                fail += 1
                failed.append((t, str(e)))
                log(f"!! {t}: {e}")
            if i % 50 == 0:
                log(f"прогресс: {i}/{len(tasks)} (ok {ok}, fail {fail})")
    log(f"Готово за {time.time()-t0:.0f}s: ok {ok}, пропущено свежих {skipped}, fail {fail}")

    # не падаем целиком из-за частичных ошибок — старые файлы остаются
    # ------------------------------------------------ 4. чистка устаревших файлов
    keep_min = (today + timedelta(days=-7)).isoformat()
    removed = 0
    droot = os.path.join(data_dir, "d")
    for gid_dir in list(os.listdir(droot)) if os.path.isdir(droot) else []:
        gdir = os.path.join(droot, gid_dir)
        for fn in list(os.listdir(gdir)):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", fn)
            if m and m.group(1) < keep_min:
                os.remove(os.path.join(gdir, fn))
                removed += 1
        if not os.listdir(gdir):
            os.rmdir(gdir)
    if removed:
        log(f"Удалено устаревших: {removed}")

    # ------------------------------------------------ 5. meta.json
    wjson(os.path.join(data_dir, "meta.json"), {
        "ok": True,
        "generatedAt": int(time.time()),
        "generatedAtHuman": datetime.now(TZ).strftime("%d.%m.%Y %H:%M МСК"),
        "dates": dates,
        "groups": len(groups),
        "failed": len(failed),
    })
    log("meta.json записан. Всё.")
    if failed:
        log("неудачные:", failed[:10])


if __name__ == "__main__":
    main()
