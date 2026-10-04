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

    # 2. вытащить любые похожие на id значения и дёрнуть пару карточек
    ids = []
    for m in re.finditer(r'(?:value|data-id|data-key)\s*=\s*["\'](\d{1,6})["\']', html):
        if m.group(1) not in ids:
            ids.append(m.group(1))
    log("кандидатов в id преподавателей:", len(ids), ids[:10])

    # варианты адресов карточки — какой-нибудь да ответит
    patterns = [
        "https://almetpt.ru/2020/site/html/teacherinfo?id={id}",
        "https://almetpt.ru/2020/site/teacherinfo/{id}",
        "https://almetpt.ru/2020/site/html/teacherinfo/{id}",
        "https://almetpt.ru/2020/site/schedule/teacher/{id}",
    ]
    for tid in ids[:2]:
        for i, pat in enumerate(patterns):
            url = pat.format(id=tid)
            try:
                page = http_get(url)
            except Exception as e:
                log("нет ответа:", url, e)
                continue
            put(f"teacher_{tid}_v{i}.html", page)
            log("снято:", url, len(page))

    # 3. страница расписания — чтобы разобраться с классом changesPair
    try:
        put("schedule_sample.html",
            http_get("https://almetpt.ru/2020/site/schedule/group/1042/2026-10-05"))
    except Exception as e:
        log("не удалось снять расписание:", e)

    return saved
