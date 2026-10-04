# -*- coding: utf-8 -*-
"""
Сквозной прогон бота с подменённой сетью: проверяем, что зеркало
действительно обновляется на следующем запуске (главный баг репозитория).
"""
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tests", "fixtures"))

import fetch_all as FA                 # noqa: E402
import make_fixtures as F              # noqa: E402


class FakeSite:
    """Отдаёт «не опубликовано» до тех пор, пока расписание не выложат."""

    def __init__(self):
        self.published = False
        self.hits = []

    def __call__(self, url, **kw):
        self.hits.append(url)
        if url.endswith("/schedulegroups"):
            return F.groups_page(2)
        if url.endswith("/schedulecalls"):
            return F.bells_page()
        if "/schedule/group/" in url:
            return F.day_with_pairs() if self.published else F.day_not_published()
        raise OSError("неизвестный url")


class TestMirrorRefresh(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.out = os.path.join(self.tmp, "docs")
        self.site = FakeSite()
        self._http = FA.http_get
        FA.http_get = self.site
        self._argv = sys.argv

    def tearDown(self):
        FA.http_get = self._http
        sys.argv = self._argv
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_bot(self, extra=()):
        sys.argv = ["fetch_all.py", "--out", self.out, "--days", "0..1",
                    "--weeks", "1", "--workers", "2", *extra]
        FA.main()

    def age_by(self, seconds):
        """Имитируем «прошло N секунд»: и в днях, и в журнале проверок.

        mtime файлов при этом остаётся свежим — ровно так ведёт себя
        actions/checkout, из-за чего старая проверка по mtime не работала.
        """
        data = os.path.join(self.out, "data")
        log = os.path.join(data, "checked.json")
        if os.path.exists(log):
            with open(log, encoding="utf-8") as f:
                j = json.load(f)
            j["at"] = {k: v - seconds for k, v in j["at"].items()}
            FA.wjson(log, j)
            os.utime(log, None)
        droot = os.path.join(data, "d")
        for gid in os.listdir(droot):
            gdir = os.path.join(droot, gid)
            for fn in os.listdir(gdir):
                p = os.path.join(gdir, fn)
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("fetchedAt"):
                    d["fetchedAt"] -= seconds
                    FA.wjson(p, d)
                os.utime(p, None)

    def day_file(self, gid, date_s):
        p = os.path.join(self.out, "data", "d", str(gid), f"{date_s}.json")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def test_unpublished_day_gets_refreshed_on_next_run(self):
        today = datetime.now(FA.TZ).date().isoformat()

        self.run_bot()
        self.assertFalse(self.day_file(1035, today)["published"])

        # имитируем «прошло 20 минут» и сайт выложил расписание
        self.site.published = True
        self.age_by(FA.TTL_TODAY + 60)

        self.site.hits.clear()
        self.run_bot()

        d = self.day_file(1035, today)
        self.assertTrue(d["published"], "день обязан перезапроситься и стать опубликованным")
        self.assertEqual(len(d["pairs"]), 4)
        self.assertTrue(any(f"/group/1035/{today}" in u for u in self.site.hits))

    def test_fresh_day_is_served_from_cache(self):
        self.run_bot()
        self.site.hits.clear()
        self.run_bot()
        self.assertFalse(any("/schedule/group/" in u for u in self.site.hits),
                         "только что скачанные дни не должны качаться повторно")

    def test_full_flag_ignores_ttl(self):
        self.run_bot()
        self.site.hits.clear()
        self.run_bot(["--full"])
        self.assertTrue(any("/schedule/group/" in u for u in self.site.hits))

    def test_garbage_response_does_not_destroy_good_data(self):
        self.site.published = True
        self.run_bot()
        today = datetime.now(FA.TZ).date().isoformat()
        good = self.day_file(1035, today)
        self.assertEqual(len(good["pairs"]), 4)

        # сайт начал отдавать заглушку провайдера
        FA.http_get = lambda url, **kw: (
            F.groups_page(2) if url.endswith("schedulegroups")
            else F.bells_page() if url.endswith("schedulecalls")
            else F.blocked_page())
        self.age_by(10 * 3600)
        self.run_bot()

        still = self.day_file(1035, today)
        self.assertEqual(len(still["pairs"]), 4, "мусорный ответ не должен затирать расписание")

    def test_outputs(self):
        self.site.published = True
        self.run_bot()
        data = os.path.join(self.out, "data")
        for name in ("groups.json", "bells.json", "meta.json", "summary.json"):
            self.assertTrue(os.path.exists(os.path.join(data, name)), name)
        self.assertTrue(os.path.exists(os.path.join(self.out, "index.html")),
                        "static/ должен копироваться в docs/")
        self.assertTrue(os.path.exists(os.path.join(self.out, ".nojekyll")))

        meta = json.load(open(os.path.join(data, "meta.json"), encoding="utf-8"))
        self.assertEqual(meta["failed"], 0)
        self.assertGreaterEqual(len(meta["dates"]), 7)

        summary = json.load(open(os.path.join(data, "summary.json"), encoding="utf-8"))
        today = datetime.now(FA.TZ).date().isoformat()
        self.assertEqual(summary["g"]["1035"][today][0], 4)

        x = json.load(open(os.path.join(data, "x", f"{today}.json"), encoding="utf-8"))
        self.assertTrue(x["lessons"])
        self.assertIn("gn", x["lessons"][0])

    def test_unchanged_day_is_not_rewritten(self):
        """Иначе каждый прогон бота менял бы сотни файлов и раздувал репозиторий."""
        self.site.published = True
        self.run_bot()
        today = datetime.now(FA.TZ).date().isoformat()
        path = os.path.join(self.out, "data", "d", "1035", f"{today}.json")
        self.age_by(FA.TTL_TODAY + 60)
        with open(path, encoding="utf-8") as f:
            before = f.read()
        os.utime(path, (1, 1))
        self.site.hits.clear()
        self.run_bot()
        self.assertTrue(any(f"/group/1035/{today}" in u for u in self.site.hits),
                        "день всё равно должен быть перепроверен по сети")
        self.assertEqual(os.path.getmtime(path), 1, "файл без изменений трогать нельзя")
        with open(path, encoding="utf-8") as f:
            self.assertEqual(f.read(), before)

    def test_old_files_are_cleaned(self):
        self.run_bot()
        old = os.path.join(self.out, "data", "d", "1035", "2020-01-01.json")
        FA.wjson(old, {"ok": True, "pairs": []})
        self.run_bot(["--keep-days", "3"])
        self.assertFalse(os.path.exists(old))


if __name__ == "__main__":
    unittest.main()
