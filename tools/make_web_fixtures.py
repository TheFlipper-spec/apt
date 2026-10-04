#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Собирает маленькое «зеркало» в tests/fixtures/web/ из синтетических страниц
(tests/fixtures/make_fixtures.py), прогнав их через настоящий парсер.

Нужно, чтобы тесты фронтенда не зависели от изменчивых данных в docs/
и проверяли именно связку «парсер -> JSON -> интерфейс».

    python3 tools/make_web_fixtures.py
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tests", "fixtures"))

import parser as P                       # noqa: E402
import fetch_all as FA                   # noqa: E402
import make_fixtures as F                # noqa: E402

OUT = os.path.join(BASE, "tests", "fixtures", "web")
DATA = os.path.join(OUT, "data")
BELLS = {r: (s, e) for r, s, e in P.BELLS_FALLBACK}

# понедельник-воскресенье фиксированной недели — тесты не должны зависеть от «сегодня»
WEEK = [f"2026-10-{d:02d}" for d in range(5, 12)]
GROUPS = [
    {"id": 9001, "name": "СА-231б", "course": 3},
    {"id": 9002, "name": "ТМ-241", "course": 2},
]
PAGES = {
    "2026-10-05": F.day_with_pairs,        # обычный день, есть подгруппы и замена
    "2026-10-06": F.day_not_published,     # не опубликовано
    "2026-10-07": F.day_base_missing,      # только замены
    "2026-10-08": F.day_consultations,     # с консультациями
    "2026-10-09": F.day_with_pairs,
    "2026-10-10": F.day_empty,             # суббота без пар
    # 2026-10-11 (вс) файла нет вовсе — проверяем «данных нет»
}


def main():
    os.makedirs(DATA, exist_ok=True)
    FA.wjson(os.path.join(DATA, "groups.json"), {"ok": True, "groups": GROUPS, "source": "fixture"})
    FA.wjson(os.path.join(DATA, "bells.json"),
             {"ok": True, "bells": [list(b) for b in P.BELLS_FALLBACK], "source": "fixture"})

    for g in GROUPS:
        for date_s, page in PAGES.items():
            d = P.build_day(g["id"], date_s, page(), BELLS)
            d["group"] = g["name"]
            d["groupName"] = g["name"]
            d["fetchedAt"] = 1791100000
            d["source"] = "fixture"
            FA.wjson(os.path.join(DATA, "d", str(g["id"]), f"{date_s}.json"), d)

    FA.wjson(os.path.join(DATA, "teachers.json"), {"ok": True, "teachers": {
        "1100": {"id": 1100, "full": "Юрасов Данила Дмитриевич", "short": "Юрасов Д.Д.",
                 "position": "Преподаватель информационных технологий",
                 "photo": "teachers/1100.jpg"},
        "108": {"id": 108, "full": "Усманова Лилия Мансуровна", "short": "Усманова Л.М.",
                "position": "Методист, преподаватель иностранного языка",
                "category": "Высшая"},
    }})

    summary, lessons = FA.build_indexes(DATA, GROUPS, WEEK)
    FA.wjson(os.path.join(DATA, "summary.json"), summary)
    for date_s, items in lessons.items():
        FA.wjson(os.path.join(DATA, "x", f"{date_s}.json"),
                 {"ok": True, "date": date_s, "lessons": items})
    FA.wjson(os.path.join(DATA, "meta.json"), {
        "ok": True, "generatedAt": 1791100000, "generatedAtHuman": "фикстура",
        "today": "2026-10-05", "dates": WEEK, "groups": len(GROUPS),
        "fetched": 0, "cached": 0, "failed": 0, "publishedDays": 5,
    })
    n = sum(len(files) for _r, _d, files in os.walk(OUT))
    print(f"tests/fixtures/web: {n} файл(ов)")


if __name__ == "__main__":
    main()
