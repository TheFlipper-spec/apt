# -*- coding: utf-8 -*-
"""Тесты бота: логика свежести, окно дат, индексы."""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import fetch_all as FA                 # noqa: E402

TODAY = date(2026, 10, 7)              # среда


def stored(fetched_ago, published=True, pairs=1, v=None):
    return {"fetchedAt": int(time.time()) - fetched_ago,
            "published": published,
            "v": FA.SCHEMA if v is None else v,
            "pairs": [{"n": "I"}] * pairs}


class TestTTL(unittest.TestCase):
    """
    Главный баг репозитория: свежесть считалась по mtime файла, а в CI
    actions/checkout выставляет mtime = «сейчас» всем файлам. Поэтому ни один
    уже существующий день никогда не перезапрашивался и зеркало навсегда
    застревало на «расписание не опубликовано».
    """

    def test_missing_file_is_always_fetched(self):
        self.assertEqual(FA.ttl_for(TODAY, TODAY, None), 0)
        self.assertEqual(FA.ttl_for(TODAY, TODAY, {"pairs": []}), 0)

    def test_today_refreshed_often(self):
        self.assertEqual(FA.ttl_for(TODAY, TODAY, stored(0)), FA.TTL_TODAY)
        self.assertLessEqual(FA.TTL_TODAY, 15 * 60)

    def test_near_future_refreshed(self):
        for off in (1, 2, 3):
            d = TODAY + timedelta(days=off)
            if d.weekday() == 6:
                continue
            self.assertEqual(FA.ttl_for(d, TODAY, stored(0)), FA.TTL_NEAR)

    def test_unpublished_future_day_is_rechecked(self):
        """Именно этот случай был сломан: день качался один раз за 3 суток."""
        d = TODAY + timedelta(days=3)
        st = stored(FA.TTL_NEAR + 60, published=False, pairs=0)
        ttl = FA.ttl_for(d, TODAY, st)
        self.assertLess(ttl, time.time() - st["fetchedAt"],
                        "неопубликованный будущий день обязан перезапрашиваться")

    def test_settled_past_day_is_not_refetched(self):
        d = TODAY - timedelta(days=2)
        self.assertEqual(FA.ttl_for(d, TODAY, stored(0)), FA.TTL_SETTLED)

    def test_past_day_without_schedule_is_rechecked(self):
        d = TODAY - timedelta(days=2)
        self.assertEqual(FA.ttl_for(d, TODAY, stored(0, published=False, pairs=0)), FA.TTL_PAST)
        self.assertLess(FA.TTL_PAST, FA.TTL_SETTLED)

    def test_sunday_is_cheap(self):
        sunday = TODAY + timedelta(days=(6 - TODAY.weekday()) % 7 or 7)
        self.assertEqual(sunday.weekday(), 6)
        self.assertEqual(FA.ttl_for(sunday, TODAY, stored(0)), FA.TTL_SUNDAY)

    def test_far_future_is_cheap_but_not_frozen(self):
        d = TODAY + timedelta(days=9)
        if d.weekday() == 6:
            d += timedelta(days=1)
        self.assertEqual(FA.ttl_for(d, TODAY, stored(0)), FA.TTL_FAR)
        self.assertLessEqual(FA.TTL_FAR, 6 * 3600)


class TestWindow(unittest.TestCase):
    def test_covers_requested_range(self):
        got = FA.window_dates(TODAY, -1, 3)
        for off in range(-1, 4):
            self.assertIn(TODAY + timedelta(days=off), got)

    def test_covers_full_current_and_next_week(self):
        got = set(FA.window_dates(TODAY, -1, 3, weeks=2))
        monday = TODAY - timedelta(days=TODAY.weekday())
        for i in range(14):
            self.assertIn(monday + timedelta(days=i), got)

    def test_sorted_unique(self):
        got = FA.window_dates(TODAY, -1, 3)
        self.assertEqual(got, sorted(set(got)))

    def test_includes_sunday(self):
        """Воскресенья нужны: иначе в обзоре недели дырка и дата «проваливается»."""
        got = FA.window_dates(TODAY, -1, 3)
        self.assertTrue(any(d.weekday() == 6 for d in got))

    def test_days_arg(self):
        self.assertEqual(FA.parse_days_arg("-1..3"), (-1, 3))
        self.assertEqual(FA.parse_days_arg(" -2 .. +5 "), (-2, 5))
        with self.assertRaises(SystemExit):
            FA.parse_days_arg("вчера")


class TestIndexes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        self.groups = [{"id": 10, "name": "АА-11", "course": 1},
                       {"id": 20, "name": "ББ-22", "course": 2}]
        self.dates = ["2026-10-05", "2026-10-06"]
        day = {
            "ok": True, "published": True,
            "pairs": [
                {"n": "I", "start": "08:00", "end": "09:20", "subject": "Физика",
                 "teacher": "Иванов И.И.", "room": "101", "subgroup": "", "changed": False},
                {"n": "II", "start": "09:30", "end": "10:50", "subject": "Химия",
                 "teacher": "Петров П.П.", "room": "102", "subgroup": "1 п/гр.", "changed": True},
            ],
        }
        FA.wjson(os.path.join(self.data, "d", "10", "2026-10-05.json"), day)
        FA.wjson(os.path.join(self.data, "d", "20", "2026-10-05.json"),
                 {"ok": True, "published": False, "pairs": []})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_summary(self):
        s, lessons = FA.build_indexes(self.data, self.groups, self.dates)
        self.assertEqual(s["g"]["10"]["2026-10-05"], [2, 1, 1, "08:00", "10:50"])
        self.assertEqual(s["g"]["20"]["2026-10-05"], [0, 0, 0, "", ""])
        self.assertNotIn("2026-10-06", s["g"]["10"], "отсутствующий файл не попадает в индекс")
        self.assertNotIn("generatedAt", s, "индекс не должен меняться от одного времени сборки")
        self.assertEqual(s["counts"]["2026-10-05"],
                         {"groups": 2, "withPairs": 1, "published": 1})
        self.assertEqual(s["names"]["10"], "АА-11")

    def test_lessons_index(self):
        _s, lessons = FA.build_indexes(self.data, self.groups, self.dates)
        day = lessons["2026-10-05"]
        self.assertEqual(len(day), 2)
        self.assertEqual(day[0]["gn"], "АА-11")
        self.assertEqual(day[0]["t"], "Иванов И.И.")
        self.assertEqual(day[1]["r"], "102")
        self.assertEqual(day[1]["ch"], 1)
        self.assertEqual(lessons["2026-10-06"], [])


class TestGroupMerge(unittest.TestCase):
    """Сайт периодически отдаёт неполный список (82 → 91 → 74 в истории репо)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.data = os.path.join(self.tmp, "data")
        FA.wjson(os.path.join(self.data, "groups.json"), {"groups": [
            {"id": 1, "name": "АА-11", "course": 1},
            {"id": 2, "name": "ББ-22", "course": 2},
            {"id": 3, "name": "ВВ-33", "course": 3},
        ]})
        self._orig = FA.http_get

    def tearDown(self):
        FA.http_get = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_merge_keeps_cached(self):
        FA.http_get = lambda url, **kw: (
            '<div class="card-header" data-id="1">АА-11, I курс</div>')
        got = FA.load_groups(self.data)
        ids = sorted(g["id"] for g in got)
        self.assertEqual(ids, [1, 2, 3], "пропавшие с сайта группы берутся из кэша")

    def test_network_failure_falls_back(self):
        def boom(url, **kw):
            raise OSError("нет сети")
        FA.http_get = boom
        got = FA.load_groups(self.data)
        self.assertEqual(len(got), 3)

    def test_fresh_data_wins(self):
        FA.http_get = lambda url, **kw: (
            '<div class="card-header" data-id="1">АА-11новая, II курс</div>')
        got = {g["id"]: g for g in FA.load_groups(self.data)}
        self.assertEqual(got[1]["name"], "АА-11новая")
        self.assertEqual(got[1]["course"], 2)


if __name__ == "__main__":
    unittest.main()


class SchemaVersionTest(unittest.TestCase):
    """Смена версии парсера должна один раз обновить всё окно."""

    def test_old_schema_forces_refetch(self):
        past = TODAY - timedelta(days=14)
        # свежий файл нужной версии — качать не надо
        self.assertGreater(FA.ttl_for(past, TODAY, stored(0)), 0)
        # тот же файл, но собранный старым парсером — качать обязательно
        self.assertEqual(FA.ttl_for(past, TODAY, stored(0, v=FA.SCHEMA - 1)), 0)
        # и файл вовсе без версии
        legacy = {k: val for k, val in stored(0).items() if k != "v"}
        self.assertEqual(FA.ttl_for(past, TODAY, legacy), 0)

    def test_version_is_not_a_content_change(self):
        """Служебное поле v не должно считаться изменением содержимого."""
        a = {"pairs": [], "v": 3, "fetchedAt": 1}
        b = {"pairs": [], "v": 4, "fetchedAt": 2}
        self.assertEqual(FA.payload_key(a), FA.payload_key(b))
