#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Демо-зеркало для локального просмотра интерфейса без доступа к almetpt.ru.

Берёт уже сохранённые в docs/ дни с парами и раскладывает их по текущей
и следующей неделе, пересобирая индексы. Полезно, когда нужно посмотреть
живые состояния («идёт пара», «перемена», обзор недели), а сайт техникума
недоступен.

    python3 tools/make_demo.py --out /tmp/preview
"""
import argparse
import json
import os
import shutil
import sys
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import fetch_all as FA                   # noqa: E402

WD = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
MON = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля",
       "августа", "сентября", "октября", "ноября", "декабря"]


def date_text(d):
    return f"{WD[d.weekday()]}, {d.day} {MON[d.month - 1]} {d.year} года"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=os.path.join(BASE, "docs"))
    ap.add_argument("--out", default="/tmp/preview")
    args = ap.parse_args()

    src_data = os.path.join(args.src, "data")
    out = args.out
    if os.path.exists(out):
        shutil.rmtree(out)
    os.makedirs(out, exist_ok=True)
    FA.copy_static(os.path.join(BASE, "static"), out)
    open(os.path.join(out, ".nojekyll"), "w").close()
    out_data = os.path.join(out, "data")

    groups = (FA.rjson(os.path.join(src_data, "groups.json")) or {}).get("groups") or []
    if not groups:
        raise SystemExit("нет docs/data/groups.json")
    FA.wjson(os.path.join(out_data, "groups.json"),
             {"ok": True, "groups": groups, "source": "demo"})
    bells = FA.rjson(os.path.join(src_data, "bells.json"))
    if bells:
        FA.wjson(os.path.join(out_data, "bells.json"), bells)
    # справочник преподавателей и их фото — как есть
    teachers = FA.rjson(os.path.join(src_data, "teachers.json"))
    if teachers:
        FA.wjson(os.path.join(out_data, "teachers.json"), teachers)
    src_photos = os.path.join(src_data, "teachers")
    if os.path.isdir(src_photos):
        shutil.copytree(src_photos, os.path.join(out_data, "teachers"), dirs_exist_ok=True)

    today = datetime.now(FA.TZ).date()
    monday = today - timedelta(days=today.weekday())
    dates = [(monday + timedelta(days=i)) for i in range(14)]

    made = 0
    for g in groups:
        gid = g["id"]
        gdir = os.path.join(src_data, "d", str(gid))
        if not os.path.isdir(gdir):
            continue
        # доноры: сохранённые дни, где реально есть пары
        donors = []
        for fn in sorted(os.listdir(gdir)):
            d = FA.rjson(os.path.join(gdir, fn))
            if d and d.get("pairs"):
                donors.append(d)
        if not donors:
            continue
        for i, dt in enumerate(dates):
            payload = dict(donors[i % len(donors)])
            if dt.weekday() == 6:                       # воскресенье — выходной
                payload = dict(payload, pairs=[], published=True)
            payload["date"] = dt.isoformat()
            payload["dateText"] = date_text(dt)
            payload["groupId"] = gid
            payload["group"] = g["name"]
            payload["groupName"] = g["name"]
            payload["fetchedAt"] = int(datetime.now(FA.TZ).timestamp()) - 420
            payload["source"] = "demo"
            FA.wjson(os.path.join(out_data, "d", str(gid), f"{dt.isoformat()}.json"), payload)
            made += 1

    iso = [d.isoformat() for d in dates]
    summary, lessons = FA.build_indexes(out_data, groups, iso)
    FA.wjson(os.path.join(out_data, "summary.json"), summary)
    for date_s, items in lessons.items():
        FA.wjson(os.path.join(out_data, "x", f"{date_s}.json"),
                 {"ok": True, "date": date_s, "lessons": items})
    FA.wjson(os.path.join(out_data, "meta.json"), {
        "ok": True,
        "generatedAt": int(datetime.now(FA.TZ).timestamp()) - 300,
        "generatedAtHuman": datetime.now(FA.TZ).strftime("%d.%m.%Y %H:%M МСК"),
        "today": today.isoformat(), "dates": iso, "groups": len(groups),
        "fetched": made, "cached": 0, "failed": 0, "publishedDays": len(iso),
        "demo": True,
    })
    print(f"демо-зеркало: {out}, дней {made}, групп {len(groups)}")


if __name__ == "__main__":
    main()
