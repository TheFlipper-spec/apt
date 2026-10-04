#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Проверки вёрстки под телефон, которые можно сделать без браузера.

Скриншот не снимешь, зато целый класс поломок виден прямо в CSS: жёсткая
ширина больше экрана, несогласованные отступы, мелкие зоны нажатия.
Эти правила и зафиксированы тестами, чтобы не возвращались.
"""
import os
import re
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HTML_PATH = os.path.join(BASE, "static", "index.html")

# самый узкий экран, который считаем обязательным к поддержке
NARROW = 320


def read_html():
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


def css_of(html):
    m = re.search(r"<style>(.*?)</style>", html, re.S)
    assert m, "в странице нет блока <style>"
    return m.group(1)


def strip_comments(css):
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def media_blocks(css):
    """[(условие или None, тело)] — верхний уровень и каждый @media отдельно."""
    css = strip_comments(css)
    out, base, i = [], [], 0
    while i < len(css):
        m = re.compile(r"@media([^{]+)\{").search(css, i)
        if not m:
            base.append(css[i:])
            break
        base.append(css[i:m.start()])
        depth, j = 1, m.end()
        while j < len(css) and depth:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        out.append((m.group(1).strip(), css[m.end():j - 1]))
        i = j
    out.append((None, "".join(base)))
    return out


def rules(body):
    """[(селектор, объявления)] внутри одного блока."""
    return [(m.group(1).strip(), m.group(2))
            for m in re.finditer(r"([^{}@]+)\{([^{}]*)\}", body)]


def decl(body, selector, prop):
    """Значение свойства у селектора (последнее объявление)."""
    val = None
    for sel, decls in rules(body):
        if selector not in [s.strip() for s in sel.split(",")]:
            continue
        for m in re.finditer(rf"(?:^|;)\s*{re.escape(prop)}\s*:\s*([^;]+)", decls):
            val = m.group(1).strip()
    return val


def px(value):
    if value is None:
        return None
    m = re.match(r"(-?[\d.]+)px", value.strip())
    return float(m.group(1)) if m else None


def joined(blocks, cond_match):
    """Тело всех @media с нужным условием — их может быть несколько."""
    return "\n".join(b for c, b in blocks
                     if c and cond_match in c.replace(" ", ""))


def is_desktop_only(condition):
    """True для @media (min-width: N) с N > NARROW — такие правила телефона не касаются."""
    if not condition:
        return False
    m = re.search(r"min-width\s*:\s*(\d+)px", condition)
    return bool(m) and int(m.group(1)) > NARROW


class MobileCssTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read_html()
        cls.css = css_of(cls.html)
        cls.blocks = media_blocks(cls.css)

    # ------------------------------------------------------------- базовое
    def test_viewport_meta(self):
        m = re.search(r'<meta\s+name="viewport"\s+content="([^"]+)"', self.html)
        self.assertIsNotNone(m, "нет meta viewport — телефон покажет десктопную ширину")
        content = m.group(1)
        self.assertIn("width=device-width", content)
        self.assertNotIn("user-scalable=no", content, "запрещать зум нельзя: это барьер доступности")
        mx = re.search(r"maximum-scale\s*=\s*([\d.]+)", content)
        if mx:
            self.assertGreaterEqual(float(mx.group(1)), 2.0, "зум должен оставаться не меньше 2x")

    def test_safe_area_respected(self):
        self.assertIn("env(safe-area-inset-bottom", self.css,
                      "нижняя панель iPhone перекроет контент без safe-area")

    # ------------------------------------------- ширины, вылезающие за экран
    def test_no_grid_column_wider_than_screen(self):
        """repeat(auto-fit, minmax(300px, 1fr)) на узком экране даёт горизонтальную прокрутку."""
        bad = []
        for cond, body in self.blocks:
            if is_desktop_only(cond):
                continue
            for m in re.finditer(r"minmax\(\s*(\d+)px", body):
                if int(m.group(1)) > NARROW - 40:      # запас на поля страницы
                    around = body[max(0, m.start() - 90):m.start()]
                    sel = (around.rsplit("}", 1)[-1] or "").strip()
                    bad.append(f"{sel[:60]} -> minmax({m.group(1)}px)")
        self.assertEqual(bad, [], "колонка шире экрана; оберните в minmax(min(Npx,100%),1fr): " + str(bad))

    def test_no_fixed_width_wider_than_screen(self):
        bad = []
        for cond, body in self.blocks:
            if is_desktop_only(cond):
                continue
            for sel, decls in rules(body):
                for prop in ("width", "min-width"):
                    for m in re.finditer(rf"(?:^|;)\s*{prop}\s*:\s*(\d+)px", decls):
                        if int(m.group(1)) > NARROW - 40:
                            bad.append(f"{sel.strip()[:50]} {prop}:{m.group(1)}px")
        self.assertEqual(bad, [], "жёсткая ширина больше экрана телефона: " + str(bad))

    def test_body_does_not_rely_on_overflow_hidden_only(self):
        """overflow-x:hidden прячет переполнение, но ломает sticky и прокрутку —
        он допустим только как страховка, а не как единственное решение."""
        self.assertIn("overflow-x:hidden", self.css.replace(" ", ""))

    # ----------------------------------------------- согласованность отступов
    def _rail_metrics(self, cond_match):
        """Ширина рельсы, зазор ряда и отступ «перемены» для нужного блока."""
        base = next(b for c, b in self.blocks if c is None)
        rail = px(decl(base, ".rail", "width"))
        gap = px(decl(base, ".pair-row", "gap"))
        brk = decl(base, ".brk", "padding")
        brk_left = px(brk.split()[-1]) if brk else px(decl(base, ".brk", "padding-left"))
        if cond_match:
            for cond, body in self.blocks:
                if cond and cond_match in cond.replace(" ", ""):
                    rail = px(decl(body, ".rail", "width")) or rail
                    gap = px(decl(body, ".pair-row", "gap")) or gap
                    brk_left = px(decl(body, ".brk", "padding-left")) or brk_left
        return rail, gap, brk_left

    def test_break_row_aligns_with_cards_on_desktop(self):
        rail, gap, brk = self._rail_metrics(None)
        self.assertIsNotNone(rail)
        self.assertEqual(brk, rail + gap,
                         f"«перемена» съедет: отступ {brk}, а карточка начинается с {rail + gap}")

    def test_break_row_aligns_with_cards_on_phone(self):
        rail, gap, brk = self._rail_metrics("max-width:560px")
        self.assertEqual(brk, rail + gap,
                         f"на телефоне «перемена» съедет: отступ {brk}, карточка с {rail + gap}")

    def test_time_rail_is_narrower_on_phone(self):
        wide, _, _ = self._rail_metrics(None)
        narrow, _, _ = self._rail_metrics("max-width:560px")
        self.assertLess(narrow, wide, "колонка времени должна ужиматься на телефоне")
        self.assertLessEqual(narrow / NARROW, 0.16,
                             "служебная колонка не должна съедать больше ~15% ширины экрана")

    # ------------------------------------------------------- зоны нажатия
    def test_tap_targets_are_reachable_by_finger(self):
        """Мелкие кнопки на телефоне промахиваются; на узком экране добираем размер."""
        phone = joined(self.blocks, "max-width:560px")
        checks = {".copy-btn": 32, ".seg button": 36}
        for sel, need in checks.items():
            h = px(decl(phone, sel, "height")) or px(decl(phone, sel, "min-height"))
            self.assertIsNotNone(h, f"{sel}: на телефоне размер не задан")
            self.assertGreaterEqual(h, need, f"{sel}: {h}px — мало для пальца")

    def test_header_buttons_stay_tappable(self):
        smallest = 999.0
        for cond, body in self.blocks:
            if is_desktop_only(cond):
                continue
            h = px(decl(body, ".icon-btn", "height"))
            if h:
                smallest = min(smallest, h)
        self.assertGreaterEqual(smallest, 34, f"кнопки шапки ужались до {smallest}px")

    # --------------------------------------------------------- читаемость
    def test_teacher_name_wraps_on_phone(self):
        phone = joined(self.blocks, "max-width:560px")
        self.assertEqual(decl(phone, ".tc-tx b", "white-space"), "normal",
                         "ФИО преподавателя не должно обрезаться многоточием на телефоне")

    def test_inputs_do_not_trigger_ios_zoom(self):
        """Safari на iOS масштабирует страницу при фокусе в поле со шрифтом < 16px."""
        phone = joined(self.blocks, "max-width:560px")
        for sel in ("input", ".search input"):
            size = px(decl(phone, sel, "font-size"))
            self.assertIsNotNone(size, f"{sel}: на телефоне размер шрифта не задан")
            self.assertGreaterEqual(size, 16, f"{sel}: {size}px — iOS зазумит страницу при фокусе")

    def test_group_list_does_not_stretch_page(self):
        base = next(b for c, b in self.blocks if c is None)
        self.assertIsNotNone(decl(base, ".groups", "max-height"),
                             "80+ групп растянут страницу — нужен свой скролл")
        self.assertEqual(decl(base, ".groups", "overflow-y"), "auto")

    def test_no_hover_only_affordances_for_touch(self):
        """:hover на телефоне не срабатывает — важные действия не должны от него зависеть."""
        hidden_by_default = re.findall(r"([^{}]+)\{[^{}]*opacity\s*:\s*0\s*[;}]", strip_comments(self.css))
        for sel in hidden_by_default:
            sel = sel.strip()
            if sel.startswith("."):
                self.assertNotIn(":hover", sel)


if __name__ == "__main__":
    unittest.main()
