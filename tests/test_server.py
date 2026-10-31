# -*- coding: utf-8 -*-
"""Тесты живого сервера: валидация дат, single-flight, негативный кэш (P1.1).

Сеть подменяется — реальный almetpt.ru не нужен.
"""
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
sys.path.insert(0, os.path.join(BASE, "tests", "fixtures"))

import server as S                     # noqa: E402
import make_fixtures as F              # noqa: E402


class _StubHandler:
    """ минимальный self для H._valid_date """

    def __init__(self):
        self.sent = []

    def _send(self, code, body, **kw):
        self.sent.append((code, body))


class TestDateValidation(unittest.TestCase):
    def valid(self, date):
        h = _StubHandler()
        return S.H._valid_date(h, date), h.sent

    def test_real_date_ok(self):
        ok, sent = self.valid("2026-11-02")
        self.assertTrue(ok)
        self.assertEqual(sent, [])

    def test_impossible_date_rejected(self):
        ok, sent = self.valid("2026-99-99")
        self.assertFalse(ok)
        self.assertEqual(sent[0][0], 400)

    def test_30_february_rejected(self):
        ok, _sent = self.valid("2026-02-30")
        self.assertFalse(ok)

    def test_garbage_rejected(self):
        for bad in ("", "2026-1-1", "2026/11/02", "today", "0000-01-01"):
            ok, _sent = self.valid(bad)
            self.assertFalse(ok, bad)


class TestDayFetching(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        # сервер пишет дисковый кэш — изолируем и затираем побочные эффекты
        self._cache_dir, self._mirror, self._bundled = S.CACHE_DIR, S.MIRROR_DATA_DIR, S.DATA_DIR
        S.CACHE_DIR = self.tmp
        S.MIRROR_DATA_DIR = os.path.join(self.tmp, "mirror-none")
        S.DATA_DIR = os.path.join(self.tmp, "data-none")
        self._http = S.http_get
        self._bells = S.get_bells
        S.get_bells = lambda: S._mem.get("bells") or P_BELLS
        S._mem["days"].clear()

    def tearDown(self):
        S.http_get = self._http
        S.get_bells = self._bells
        S.CACHE_DIR, S.MIRROR_DATA_DIR, S.DATA_DIR = self._cache_dir, self._mirror, self._bundled
        S._mem["days"].clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_single_flight(self):
        """10 одновременных запросов одной группы — один исходящий запрос."""
        hits = []

        def slow(url, **kw):
            hits.append(url)
            time.sleep(0.15)
            return F.day_with_pairs()

        S.http_get = slow
        out, errs = [], []

        def work():
            try:
                out.append(S.get_day(1042, "2026-10-05"))
            except Exception as e:                     # noqa: BLE001
                errs.append(e)

        threads = [threading.Thread(target=work) for _ in range(10)]
        t0 = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(5)
        self.assertFalse(errs, errs)
        self.assertEqual(len(out), 10)
        self.assertEqual(len(hits), 1, f"исходящих запросов: {len(hits)}")
        self.assertLess(time.time() - t0, 3, "ждущие потоки не должны гонять сайт")
        self.assertTrue(all(d.get("ok") for d in out))

    def test_negative_cache(self):
        """Провал (404/сеть) запоминается: повторный запрос не идёт на сайт."""
        hits = []

        def boom(url, **kw):
            hits.append(url)
            raise ValueError("page 404")

        S.http_get = boom
        first = S.get_day(999999, "2026-10-05")
        second = S.get_day(999999, "2026-10-05")
        self.assertFalse(first.get("ok"))
        self.assertFalse(second.get("ok"))
        self.assertEqual(len(hits), 1)

    def test_soft_404_detected(self):
        S.http_get = lambda url, **kw: F.not_found_page()
        d = S.get_day(999999, "2026-10-05")
        self.assertFalse(d.get("ok"))


# значение по умолчанию для звонков без обращения к сайту
import parser as P                     # noqa: E402

P_BELLS = list(P.BELLS_FALLBACK)


if __name__ == "__main__":
    unittest.main()
