#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Снимок страниц педсостава almetpt.ru — для разработки парсера.

Запускается автоматически из fetch_all.py, когда в репозитории ещё нет
docs/data/teachers.json и нет снимка. Кладёт сырой HTML в docs/_raw/,
чтобы по нему можно было написать парсер, не имея доступа к сайту.

После того как парсер написан, снимок больше не нужен и удаляется.
"""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)


def capture(out_dir, http_get, log=print):
    os.makedirs(out_dir, exist_ok=True)
    saved = []

    def put(name, text):
        path = os.path.join(out_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        saved.append(f"{name} ({len(text)} символов)")

    # 1. страница со списком преподавателей
    try:
        html = http_get("https://almetpt.ru/2020/site/html/teacherinfo")
        put("teacherinfo.html", html)
    except Exception as e:
        log("не удалось снять teacherinfo:", e)
        return saved

    # 2. карточки преподавателей: id берём прямо из расписания
    #    (<span class="Staff" data-id=1100 title="Юрасов Данила Дмитриевич">)
    try:
        sched = http_get("https://almetpt.ru/2020/site/schedule/group/1042/2026-10-05")
        put("schedule_sample.html", sched)
    except Exception as e:
        log("не удалось снять расписание:", e)
        sched = ""

    ids = []
    for m in re.finditer(r'class="Staff"\s+data-id=["\']?(\d+)', sched):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    for extra in ("1100", "108"):
        if extra not in ids:
            ids.append(extra)
    log("id преподавателей из расписания:", ids[:10])

    for tid in ids[:3]:
        url = f"https://almetpt.ru/2020/site/html/teacherinfo/{tid}"
        try:
            put(f"teacher_{tid}.html", http_get(url))
            log("снято:", url)
        except Exception as e:
            log("нет ответа:", url, e)

    return saved
