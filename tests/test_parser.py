# -*- coding: utf-8 -*-
"""Офлайн-тесты парсера almetpt.ru. Запуск: python3 -m unittest discover tests"""
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tests", "fixtures"))

import parser as P                     # noqa: E402
import make_fixtures as F              # noqa: E402

BELLS = {r: (s, e) for r, s, e in P.BELLS_FALLBACK}


class TestGroups(unittest.TestCase):
    def test_parse(self):
        g = P.parse_groups(F.groups_page())
        self.assertEqual(len(g), 4)
        self.assertEqual(g[0], {"id": 1035, "name": "СА-231б", "course": 1})
        self.assertEqual([x["course"] for x in g], [1, 2, 3, 4])

    def test_empty_page(self):
        self.assertEqual(P.parse_groups("<html></html>"), [])


class TestBells(unittest.TestCase):
    def test_parse(self):
        b = P.parse_bells(F.bells_page())
        self.assertEqual(b[0], ("I", "08:00", "09:20"))
        self.assertEqual(len(b), 3)

    def test_fallback(self):
        self.assertEqual(P.parse_bells("<html></html>"), P.BELLS_FALLBACK)


class TestDay(unittest.TestCase):
    def setUp(self):
        self.d = P.build_day(1042, "2026-10-05", F.day_with_pairs(), BELLS)

    def test_header(self):
        self.assertTrue(self.d["ok"])
        self.assertTrue(self.d["published"])
        self.assertEqual(self.d["group"], "СА-231б")
        self.assertEqual(self.d["dateText"], "понедельник, 5 октября 2026 года")
        self.assertEqual(len(self.d["pairs"]), 4)

    def test_times(self):
        p = self.d["pairs"][0]
        self.assertEqual((p["start"], p["end"]), ("08:00", "09:20"))
        self.assertEqual(p["n"], "I")
        self.assertEqual(p["num"], 1)

    def test_subgroups_are_not_mashed_together(self):
        """Пара по подгруппам должна отдаваться отдельными блоками, а не строкой «А / Б»."""
        p = self.d["pairs"][0]
        self.assertEqual(p["variants"], 2)
        self.assertEqual(len(p["parts"]), 2)
        self.assertEqual([x["subgroup"] for x in p["parts"]], ["1 п/гр.", "2 п/гр."])
        self.assertEqual([x["room"] for x in p["parts"]], ["233", "318"])
        self.assertEqual(p["parts"][0]["subject"], "03.03/09.0")
        self.assertEqual(p["parts"][1]["subject"], "ПрофИнЯз")

    def test_topic_belongs_to_its_subgroup(self):
        p = self.d["pairs"][0]
        self.assertEqual(p["parts"][0]["topics"], [])
        self.assertEqual(len(p["parts"][1]["topics"]), 1)
        self.assertIn("Проживание в гостинице", p["parts"][1]["topics"][0])
        self.assertEqual(p["parts"][1]["homework"], ["Выполнить задания в Moodle"])

    def test_single_variant_details_on_card(self):
        p = self.d["pairs"][1]
        self.assertEqual(p["variants"], 1)
        self.assertEqual(p["parts"], [])
        self.assertEqual(p["room"], "236")
        self.assertEqual(p["teacher"], "Зинкин Дмитрий Владимирович")
        self.assertEqual(p["topics"], ["Конфигурирование SMB (Практическая)"])
        self.assertEqual(p["homework"], ["Оформить отчёт"])

    def test_short_subject_replaced_by_full(self):
        p = self.d["pairs"][2]
        self.assertEqual(p["subject"], "Информатика")
        self.assertEqual(p["subjectFull"], "")

    def test_online_room(self):
        self.assertEqual(self.d["pairs"][2]["room"], "on-line")

    def test_changed_flag(self):
        self.assertTrue(self.d["pairs"][3]["changed"])
        self.assertFalse(self.d["pairs"][1]["changed"])

    def test_graf(self):
        g = self.d["graf"]
        self.assertGreaterEqual(len(g), 2)
        self.assertEqual(g[0]["range"], "01/09-19/10/2026")
        self.assertEqual(g[0]["label"], "Обучение по дисциплинам")
        self.assertEqual(g[1]["range"], "20/10-02/11/2026")          # дубль схлопнут
        self.assertEqual(g[1]["practice"]["short"], "УП 03")
        self.assertEqual(g[1]["hours"], "72")
        self.assertEqual(g[1]["heads"], ["Зинкина А.Ф."])

    def test_pairs_sorted(self):
        nums = [p["num"] for p in self.d["pairs"]]
        self.assertEqual(nums, sorted(nums))


class TestDayVariants(unittest.TestCase):
    def test_not_published(self):
        d = P.build_day(1042, "2026-10-06", F.day_not_published(), BELLS)
        self.assertFalse(d["published"])
        self.assertEqual(d["pairs"], [])
        self.assertEqual(d["group"], "СА-231б")
        self.assertEqual(d["dateText"], "вторник, 6 октября 2026 года")
        self.assertTrue(d["graf"], "график учебного процесса должен читаться и без пар")

    def test_published_but_empty(self):
        d = P.build_day(1042, "2026-10-10", F.day_empty(), BELLS)
        self.assertTrue(d["published"])
        self.assertEqual(d["pairs"], [])
        self.assertEqual(d["group"], "СА-231б")

    def test_base_missing_flag(self):
        d = P.build_day(1042, "2026-10-07", F.day_base_missing(), BELLS)
        self.assertTrue(d["published"])
        self.assertTrue(d["baseMissing"])
        self.assertEqual(len(d["pairs"]), 1)
        self.assertEqual(d["notices"], [], "служебный alert не должен попадать в уведомления")

    def test_consultations(self):
        d = P.build_day(1042, "2026-10-08", F.day_consultations(), BELLS)
        self.assertEqual(len(d["pairs"]), 1)
        self.assertEqual(len(d["consultations"]), 1)
        self.assertIn("Зинкин", d["consultations"][0])

    def test_blocked_page_is_rejectable(self):
        """У заглушки нет ни названия группы, ни пар — бот обязан её отбросить."""
        d = P.build_day(1042, "2026-10-05", F.blocked_page(), BELLS)
        self.assertEqual(d["group"], "")
        self.assertEqual(d["pairs"], [])


class TestHelpers(unittest.TestCase):
    def test_roman(self):
        for r, n in [("I", 1), ("IV", 4), ("V", 5), ("VIII", 8), ("", 0)]:
            self.assertEqual(P.roman_to_int(r), n)

    def test_time(self):
        self.assertEqual(P._parse_time("0930-1050"), ("09:30", "10:50"))
        self.assertEqual(P._parse_time("0800 - 0920"), ("08:00", "09:20"))

    def test_nbsp_cleanup(self):
        self.assertEqual(P.clean("a\u00a0 b\n c"), "a b c")

    def test_subgroup_norm(self):
        self.assertEqual(P._norm_subgroup("1 п/гр."), "1 п/гр.")
        self.assertEqual(P._norm_subgroup("2 подгруппа"), "2 п/гр.")
        self.assertEqual(P._norm_subgroup("без подгруппы"), "")

    def test_nested_flex_columns_are_one_variant(self):
        html = (F.HEAD + '<div class="header3">Расписание занятий группы X на пятницу</div>'
                '<div class="card myCard"><div class="card-header"><span class="h3">I</span>'
                '<span>08<sup>00</sup> - 09<sup>20</sup></span></div><div class="card-body">'
                '<div class="d-flex flex-column"><div class="d-flex flex-column">'
                '<div class="d-none d-md-block"><b>Физ</b> <small>Физика</small></div>'
                '</div></div></div></div>' + F.FOOT)
        d = P.build_day(1, "2026-10-09", html, BELLS)
        self.assertEqual(len(d["pairs"]), 1)
        self.assertEqual(d["pairs"][0]["variants"], 1)


if __name__ == "__main__":
    unittest.main()
