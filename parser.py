# -*- coding: utf-8 -*-
"""
Парсер страниц расписания сайта almetpt.ru.
Мини-DOM на html.parser + функции извлечения.
"""
import re
import html as _html
from html.parser import HTMLParser


# ---------------------------------------------------------------- mini DOM
class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag, attrs, parent=None):
        self.tag = tag
        self.attrs = dict(attrs)
        self.children = []          # Node | str  (str — куски текста в порядке документа)
        self.parent = parent

    def data(self):
        return "".join(c for c in self.children if isinstance(c, str))

    def text(self):
        if self.tag == "br":
            return " "
        parts = []
        for c in self.children:
            parts.append(c if isinstance(c, str) else c.text())
        return "".join(parts)

    def norm_text(self):
        t = _html.unescape(self.text()).replace("\u00a0", " ")
        return re.sub(r"\s+", " ", t).strip()

    def has_class(self, *cls):
        mine = set(self.attrs.get("class", "").split())
        return all(c in mine for c in cls)

    def classes(self):
        return set(self.attrs.get("class", "").split())

    def ancestors(self):
        n = self.parent
        while n is not None:
            yield n
            n = n.parent


VOID = {"br", "img", "input", "hr", "meta", "link", "col", "wbr", "area", "base", "embed", "source", "track"}


def build_dom(html_text):
    root = Node("root", [])

    class P(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.stack = [root]

        def handle_starttag(self, tag, attrs):
            n = Node(tag, attrs, self.stack[-1])
            self.stack[-1].children.append(n)
            if tag not in VOID:
                self.stack.append(n)

        def handle_startendtag(self, tag, attrs):
            n = Node(tag, attrs, self.stack[-1])
            self.stack[-1].children.append(n)

        def handle_endtag(self, tag):
            for i in range(len(self.stack) - 1, 0, -1):
                if self.stack[i].tag == tag:
                    del self.stack[i:]
                    return

        def handle_data(self, data):
            self.stack[-1].children.append(data)

    p = P()
    p.feed(html_text)
    return root


def walk(node):
    yield node
    for c in node.children:
        if isinstance(c, Node):
            yield from walk(c)


def find_all(node, pred):
    return [n for n in walk(node) if n is not node and pred(n)]


def find_outermost(node, pred):
    """Как find_all, но без вложенных друг в друга совпадений."""
    hits = find_all(node, pred)
    hit_set = set(id(h) for h in hits)
    return [h for h in hits if not any(id(a) in hit_set for a in h.ancestors())]


def first(node, pred):
    for n in walk(node):
        if n is not node and pred(n):
            return n
    return None


def clean(s):
    if s is None:
        return ""
    t = _html.unescape(str(s)).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", t).strip()


def looks_like_404(html_text):
    """Сайт отдаёт «мягкий» 404: код 200, но внутри заглушка.

    Проверять надо регистронезависимо и по нескольким формулировкам —
    по одной-единственной русской строке ловить ненадёжно.
    """
    low = (html_text or "").lower()
    return ("страничка не найдена" in low
            or "страница не найдена" in low
            or "страница отсутствует" in low)


# ---------------------------------------------------------------- roman
def roman_to_int(r):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed((r or "").strip()):
        v = vals.get(ch, 0)
        if v < prev:
            total -= v
        else:
            total += v
        prev = max(prev, v)
    return total


# ---------------------------------------------------------------- groups page
def parse_groups(html_text):
    """Список групп из /2020/site/schedulegroups -> [{id, name, course}]"""
    dom = build_dom(html_text)
    groups = []
    for n in find_all(dom, lambda x: x.tag == "div" and "data-id" in x.attrs
                      and "card-header" in x.classes()):
        gid = n.attrs["data-id"].strip()
        if not gid.isdigit():
            continue
        txt = n.norm_text()                      # "СА-261б, I курс"
        m = re.match(r"(.+?),\s*([IVX]+)\s*курс", txt)
        if m:
            name, course = clean(m.group(1)), roman_to_int(m.group(2))
        else:
            name, course = txt, None
        if name:
            groups.append({"id": int(gid), "name": name, "course": course})
    seen = set()
    uniq = []
    for g in groups:
        if g["id"] not in seen:
            seen.add(g["id"])
            uniq.append(g)
    return sorted(uniq, key=lambda g: (g["course"] or 9, g["name"]))


# ---------------------------------------------------------------- day page
BELLS_FALLBACK = [
    ("I",   "08:00", "09:20"),
    ("II",  "09:30", "10:50"),
    ("III", "11:20", "12:40"),
    ("IV",  "12:50", "14:10"),
    ("V",   "14:20", "15:40"),
    ("VI",  "15:50", "17:10"),
    ("VII", "17:15", "18:35"),
    ("VIII", "18:40", "20:00"),
]


def _parse_time(text):
    """Разобрать фактическое время из заголовка пары.

    На almetpt.ru встречаются и ``08<sup>00</sup> - 09<sup>20</sup>``,
    и обычные ``08:00 - 09:20``. Важно брать это значение с карточки пары,
    а не вычислять его из номера: длительность и перемены у разных дней
    действительно отличаются (1:30, 1:10 и т. п.).
    """
    raw = clean(text).replace("\u2013", "-").replace("\u2014", "-")

    def pair(hour, minute):
        h, m = int(hour), int(minute)
        if h > 23 or m > 59:
            return None
        return f"{h:02d}:{m:02d}"

    # Форматы 08:00, 8.00 и 08 00 (последний получается из sup-разметки).
    split = re.findall(r"(?<!\d)(\d{1,2})\s*(?::|\.)\s*(\d{2})(?!\d)", raw)
    if len(split) >= 2:
        a, b = pair(*split[0]), pair(*split[1])
        return (a or "", b or "")
    split = re.findall(r"(?<!\d)(\d{1,2})\s+(\d{2})(?!\d)", raw)
    if len(split) >= 2:
        a, b = pair(*split[0]), pair(*split[1])
        return (a or "", b or "")

    # Старый компактный вид 0800 - 0920.
    compact = re.findall(r"(?<!\d)(\d{3,4})(?!\d)", raw)
    if len(compact) >= 2:
        def compact_time(value):
            value = value.zfill(4)
            return pair(value[:2], value[2:])
        return (compact_time(compact[0]) or "", compact_time(compact[1]) or "")
    return ("", "")


def _norm_subgroup(txt):
    """Приводим «1 п/гр.», «1 подгруппа», «I подгр.» к единому виду."""
    t = clean(txt).lower()
    if not t:
        return ""
    if re.search(r"\b1\b|перв", t):
        return "1 п/гр."
    if re.search(r"\b2\b|втор", t):
        return "2 п/гр."
    if "без" in t:
        return ""
    return clean(txt)


def parse_day(html_text):
    """
    Страница /2020/site/schedule/group/<id>/<date> -> dict:
    {group, dateText, published, baseMissing,
     pairs:[...], practice:[...], consultations:[...], graf:[...],
     defenses:[...], notices:[...]}
    """
    dom = build_dom(html_text)

    # ----- ограничимся областью <main>
    main = first(dom, lambda n: n.tag == "main") or dom

    # ----- блочные маркеры в порядке следования
    blocks = []
    for n in walk(main):
        if n.tag == "div" and n.has_class("alert"):
            blocks.append(("alert", n))
        elif n.tag == "div" and n.has_class("header3"):
            blocks.append(("h3", n))
        elif n.tag == "div" and n.has_class("card", "myCard"):
            blocks.append(("card", n))
    # walk() уже обходит в порядке документа

    result = {
        "group": "", "dateText": "", "published": True, "baseMissing": False,
        "pairs": [], "practice": [], "consultations": [], "graf": [],
        "defenses": [], "notices": [],
    }
    section = "head"
    seen_practice = set()

    for kind, node in blocks:
        if kind == "alert":
            msg = node.norm_text()
            if msg:
                low = msg.lower()
                if "не опублик" in low:
                    result["published"] = False
                    # вытащим группу/дату из сообщения
                    m = re.search(r"группы\s+(.+?)\s*на\s+(.+?)\s*не опублик", msg)
                    if m:
                        result["group"] = result["group"] or clean(m.group(1))
                        result["dateText"] = result["dateText"] or clean(m.group(2))
                elif "основное расписание отсутствует" in low:
                    result["baseMissing"] = True
                else:
                    result["notices"].append(msg)
            continue

        if kind == "h3":
            title = node.norm_text()
            if "Расписание занятий группы" in title:
                section = "pairs"
                m = re.search(r"группы\s+(.+?)\s+на\s+(.+)$", title)
                if m:
                    result["group"] = clean(m.group(1))
                    result["dateText"] = clean(m.group(2))
            elif "Консультац" in title:
                section = "consult"
            elif "График учебного процесса" in title:
                section = "graf"
            elif "защит" in title.lower() or "курсов" in title.lower():
                # «График защиты курсовых проектов» и подобные редкие блоки:
                # структура там нестабильная, сохраняем текст как есть
                section = "defense"
            else:
                section = "other:" + title[:60]
            continue

        # ----- card
        if section == "pairs":
            pair = _parse_pair_card(node)
            if pair:
                result["pairs"].append(pair)
            else:
                # Карточка без номера пары в секции занятий — это практика
                # (место, руководитель, часы). Раньше она либо исчезала
                # вовсе, либо превращалась в «пару» без дисциплины.
                pr = _parse_practice_card(node)
                if pr:
                    k = json_dumps({key: pr[key] for key in ("short", "room", "period")})
                    if k not in seen_practice:
                        seen_practice.add(k)
                        result["practice"].append(pr)
        elif section == "consult":
            txt = node.norm_text()
            if txt:
                result["consultations"].append(txt)
        elif section == "graf":
            result["graf"].extend(_parse_graf_card(node))
        elif section == "defense":
            txt = node.norm_text()
            if txt:
                result["defenses"].append(txt)

    return result


def _collect_lines(scope):
    """Тема / Д.з / примечание внутри области -> (node, kind, text)."""
    out = []
    for d in find_all(scope, lambda n: n.tag == "div" and n.has_class("d-none", "d-md-flex")):
        for ln in find_all(d, lambda n: n.tag == "div" and n.has_class("pl-3")):
            txt = ln.norm_text()
            if not txt:
                continue
            if txt.startswith("Тема"):
                out.append((ln, "topic", re.sub(r"^Тема:\s*", "", txt)))
            elif re.match(r"^Д\.?\s*з", txt):
                out.append((ln, "hw", re.sub(r"^Д\.?\s*з:?\s*", "", txt)))
            else:
                out.append((ln, "note", txt))
    return out


def _parse_pair_card(card):
    header = first(card, lambda n: n.tag == "div" and n.has_class("card-header"))
    if not header:
        return None
    n_rom = first(header, lambda n: n.tag == "span" and n.has_class("h3"))
    numeral = clean(n_rom.text()) if n_rom else ""
    # На странице встречаются дополнительные карточки практики/аудиторий
    # без номера пары. Это не занятие и не должно попадать в расписание.
    if not numeral or roman_to_int(numeral) <= 0:
        return None

    t_start, t_end = _card_header_times(header)

    body = first(card, lambda n: n.tag == "div" and n.has_class("card-body"))
    rows = []
    row_divs = []
    if body:
        # каждый внешний "d-flex flex-column" — вариант (подгруппа);
        # вложенные такие же контейнеры не считаем отдельными вариантами
        row_divs = find_outermost(body, lambda n: n.tag == "div" and n.has_class("d-flex", "flex-column"))
        if not row_divs:
            row_divs = [body]
        for rd in row_divs:
            row = _parse_variant(rd)
            if row:
                rows.append((rd, row))

    # тема / домашка: что внутри варианта — к варианту, остальное — ко всей паре
    topics, hw, notes = [], [], []
    owners = {id(rd): row for rd, row in rows}
    for node, kind, txt in _collect_lines(card):
        target = None
        for anc in node.ancestors():
            if id(anc) in owners:
                target = owners[id(anc)]
                break
        bucket = target if target is not None else None
        if bucket is None:
            (topics if kind == "topic" else hw if kind == "hw" else notes).append(txt)
        else:
            key = "topics" if kind == "topic" else "homework" if kind == "hw" else "notes"
            bucket[key].append(txt)

    changed = bool(find_all(card, lambda n: "changesPair" in n.classes()))
    variants = [row for _rd, row in rows]
    if not variants:
        return None
    return {
        "numeral": numeral,
        "start": t_start,
        "end": t_end,
        "variants": variants,
        "topics": topics,
        "homework": hw,
        "notes": notes,
        "changed": changed,
    }


def _detect_subgroup(rd):
    """Подгруппа варианта.

    На сайте маркер subGroup1/subGroup2 стоит на самом контейнере варианта
    (div.d-flex.flex-column.subGroup1), поэтому сначала смотрим его классы.
    Запасной путь — span.rounded внутри и его текст («1 п/гр.»).
    """
    cl = rd.classes()
    if "subGroup1" in cl:
        return "1 п/гр."
    if "subGroup2" in cl:
        return "2 п/гр."
    if "nosubGroup" in cl:
        return ""
    sub_span = first(rd, lambda n: n.tag == "span" and n.has_class("rounded"))
    if sub_span:
        for s in find_all(sub_span, lambda n: n.tag == "span"):
            c2 = s.classes()
            if not s.norm_text():
                continue
            if "subGroup1" in c2:
                return "1 п/гр."
            if "subGroup2" in c2:
                return "2 п/гр."
            if "nosubGroup" in c2:
                return ""
        return _norm_subgroup(sub_span.norm_text())
    return ""


def _extract_room(scope):
    # аудитория
    for sp in find_all(scope, lambda n: n.tag == "span"):
        t = sp.text()
        if "ауд." not in t:
            continue
        a = first(sp, lambda n: n.tag == "a")
        if a is not None and clean(a.text()):
            return clean(a.text()), clean(a.attrs.get("title", ""))
        inner = first(sp, lambda n: n.tag == "span" and n.has_class("h5"))
        room = clean(inner.text()) if inner else clean(t)
        return room.replace("ауд.", "").strip(), ""
    return "", ""


def _extract_staff(scope):
    """Преподаватели/руководители: span.Staff с коротким и полным именем."""
    teachers = []
    for st in find_all(scope, lambda n: n.tag == "span" and n.has_class("Staff")):
        short = clean(st.text())
        full = clean(st.attrs.get("title", ""))
        # id ведёт на карточку преподавателя: /2020/site/html/teacherinfo/<id>
        tid = (st.attrs.get("data-id") or "").strip().strip('"\'')
        if short or full:
            t = {"short": short, "full": full}
            if tid.isdigit():
                t["id"] = int(tid)
            teachers.append(t)
    return teachers


def _full_teacher_line(rd):
    for sp in find_all(rd, lambda n: n.tag == "span" and n.has_class("d-none", "d-md-block")):
        if "преп." in sp.text():
            inner = first(sp, lambda n: n.tag == "span" and n.has_class("h5"))
            return clean(inner.text()) if inner else clean(sp.text()).replace("преп.", "").strip()
    return None


def _variant_subject(rd):
    subj, subj_full = "", ""
    desk = first(rd, lambda n: n.tag == "div" and n.has_class("d-none", "d-md-block"))
    if desk is not None:
        b = first(desk, lambda n: n.tag == "b")
        sm = first(desk, lambda n: n.tag == "small")
        subj = clean(b.text()) if b else ""
        subj_full = clean(sm.text()) if sm else ""
    if not subj:
        mob = first(rd, lambda n: n.tag == "div" and n.has_class("d-md-none") and "title" in n.attrs)
        if mob is not None:
            subj = clean(mob.text())
            subj_full = clean(mob.attrs.get("title", ""))
    return subj, subj_full


def _parse_variant(rd):
    subgroup = _detect_subgroup(rd)
    room, room_title = _extract_room(rd)
    teachers = _extract_staff(rd)
    full_span = _full_teacher_line(rd)
    subj, subj_full = _variant_subject(rd)
    # Вариант без дисциплины/аудитории/преподавателя, но с подписью
    # подгруппы — это «у этой подгруппы занятия нет». Его нельзя выбрасывать:
    # иначе предмет соседней подгруппы показывается как предмет всей группы.
    if not (room or teachers or subj or subgroup):
        return None
    return {
        "subgroup": subgroup,
        "room": room,
        "roomTitle": room_title,
        "teachers": teachers,
        "teacherFull": full_span or (teachers[0]["full"] if teachers else ""),
        "subject": subj,
        "subjectFull": subj_full,
        "topics": [],
        "homework": [],
        "notes": [],
    }


def _card_header_times(header):
    """Время из заголовка карточки («I пара 08:00 - 09:20» / sup-разметка)."""
    for sp in find_all(header, lambda n: n.tag == "span"):
        raw = clean(sp.text()).replace(" ", "")
        if re.fullmatch(r"\d{3,4}-\d{3,4}", raw):
            return _parse_time(raw)
    return "", ""


# Практика всегда занимает рамку с 1-й по 6-ю пару включительно:
# каждая подгруппа занимается 3 пары подряд, стандартное разбиение —
# 1 п/гр: пары I–III, 2 п/гр: пары IV–VI. Заголовочный интервал карточки
# вроде «08:00–16:00» лишь приблизителен, поэтому честное начало/конец
# каждой пары считаем по звонкам, а не по этой записи.
PRACTICE_FRAME = ("I", "II", "III", "IV", "V", "VI")


def practice_slots(bells):
    """Пары рамки практики (I–VI) с их временем по звонкам."""
    out = []
    for numeral in PRACTICE_FRAME:
        t = bells.get(numeral)
        if t is not None:
            out.append({"n": numeral, "start": t[0], "end": t[1]})
    return out


def _parse_practice_card(card):
    """Карточка практики в секции занятий: та же структура, что у пары,
    но без номера пары. Отсюда берём название, место, руководителей,
    период и часы — раньше такие карточки просто пропадали."""
    header = first(card, lambda n: n.tag == "div" and n.has_class("card-header"))
    body = first(card, lambda n: n.tag == "div" and n.has_class("card-body")) or card

    # если в заголовке есть номер пары — это всё-таки пара, не практика
    if header is not None:
        n_rom = first(header, lambda n: n.tag == "span" and n.has_class("h3"))
        if n_rom is not None and roman_to_int(clean(n_rom.text())) > 0:
            return None

    t_start = t_end = ""
    if header is not None:
        t_start, t_end = _card_header_times(header)

    short, full = _variant_subject(body)
    if not short and header is not None:
        # заголовок без номера: «Практика группы СА-231б на пятницу,
        # 30 октября 2026 года» → просто «Практика»; группу и дату
        # не повторяем — они и так видны на странице дня.
        txt = header.norm_text()
        txt = re.sub(r"\d{1,2}\s*\d{2}\s*-\s*\d{1,2}\s*\d{2}", " ", txt)
        txt = re.sub(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", " ", txt)
        cut = re.search(r"\s+группы\s+", txt)
        if cut is not None:
            txt = txt[:cut.start()]
        short = clean(txt)

    teachers = _extract_staff(body)
    room, room_title = _extract_room(body)
    hours_m = re.search(r"\((\d+)\s*час", body.text())
    period = ""
    bg = first(body, lambda n: n.tag == "div" and "bg-light" in n.classes())
    if bg is not None:
        parts = [p.strip() for p in clean(bg.text()).split(",") if p.strip()]
        period = ", ".join(dict.fromkeys(parts))

    if not (short or full or teachers or room or period):
        return None
    return {
        "short": short,
        "full": full,
        "start": t_start,
        "end": t_end,
        "room": room,
        "roomTitle": room_title,
        "heads": teachers,
        "hours": hours_m.group(1) if hours_m else "",
        "period": period,
    }


def _parse_graf_card(card):
    """График учебного процесса -> список периодов"""
    out = []
    last_range = ""
    for n in walk(card):
        if not isinstance(n, Node) or n is card:
            continue
        if n.tag == "div" and "bg-light" in n.classes():
            # "20/10-02/11/2026, 20/10-02/11/2026" -> без дублей
            parts = [p.strip() for p in clean(n.text()).split(",") if p.strip()]
            uniq = list(dict.fromkeys(parts))
            last_range = ", ".join(uniq)
        if n.tag == "div" and "border-bottom" in n.classes():
            r = n
            rng = first(r, lambda x: x.tag == "div" and "bg-light" in x.classes())
            rng_txt = clean(rng.text()) if rng else last_range
            if rng_txt:
                parts = [p.strip() for p in rng_txt.split(",") if p.strip()]
                rng_txt = ", ".join(dict.fromkeys(parts))
            m = re.search(r"(\d{1,2})\s*(недел\w*|дн\w*)", clean(r.text()))
            span = f"{m.group(1)} {m.group(2)}" if m else ""
            label_div = first(r, lambda x: x.tag == "div" and x.has_class("pl-2", "text-wrap"))
            label = clean(label_div.text()) if label_div else ""
            pract = None
            if not label:
                pb = first(r, lambda x: x.tag == "b")
                ps = first(r, lambda x: x.tag == "small")
                if pb:
                    pract = {"short": clean(pb.text()), "full": clean(ps.text()) if ps else ""}
            staffs = [clean(s.text()) for s in find_all(r, lambda x: x.tag == "span" and x.has_class("Staff"))]
            hours_m = re.search(r"\((\d+)\s*час", r.text())
            out.append({
                "range": rng_txt,
                "span": span,
                "hours": hours_m.group(1) if hours_m else "",
                "label": label,
                "practice": pract,
                "heads": staffs,
            })
    ded, seen = [], set()
    for it in out:
        if not any([it["range"], it["label"], it["practice"]]):
            continue
        k = (it["range"], it["span"], it["label"], json_dumps(it["practice"]))
        if k not in seen:
            seen.add(k)
            ded.append(it)
    return ded


# ---------------------------------------------------------------- day payload builder
def _dedup(seq):
    return list(dict.fromkeys(x for x in seq if x))


def _tidy_subject(subj, full):
    """
    Если короткое название — просто обрезок полного («Информатик» -> «Информатика»),
    оставляем только полное. Коды вида «05.03/09.0» не трогаем.
    """
    if subj and full:
        ns = re.sub(r"[\s.]+", "", subj.lower())
        nf = re.sub(r"[\s.]+", "", full.lower())
        if nf.startswith(ns) and 5 <= len(ns) and len(nf) <= len(ns) + 4:
            return full, ""
    return subj, full


def build_day(group_id, date, html_text, bells):
    """
    html страницы дня -> готовый JSON-словарь для фронтенда.
    bells: dict {roman: (start,end)}
    """
    d = parse_day(html_text)
    pairs = []
    for p in d["pairs"]:
        st, en = p["start"], p["end"]
        if (not st or not en) and p["numeral"] in bells:
            st, en = bells[p["numeral"]]
        variants = p["variants"]

        parts = []
        for v in variants:
            subj, full = _tidy_subject(v.get("subject", ""), v.get("subjectFull", ""))
            parts.append({
                "subgroup": v.get("subgroup", ""),
                "room": v.get("room", ""),
                "roomTitle": v.get("roomTitle", ""),
                "subject": subj,
                "subjectFull": full,
                "teacher": v.get("teacherFull", "") or
                           " / ".join(t["short"] for t in v.get("teachers", [])),
                "teachers": v.get("teachers", []),
                "topics": v.get("topics", []),
                "homework": v.get("homework", []),
                "notes": v.get("notes", []),
            })

        # плоские поля — для одной подгруппы и для обратной совместимости
        if len(parts) == 1:
            flat = parts[0]
            subject, subject_full = flat["subject"], flat["subjectFull"]
            room, room_title = flat["room"], flat["roomTitle"]
            teacher, teachers = flat["teacher"], flat["teachers"]
            subgroup = flat["subgroup"]
        else:
            subject = " · ".join(dict.fromkeys(x["subject"] for x in parts if x["subject"]))
            subject_full = ""
            room = " · ".join(dict.fromkeys(x["room"] for x in parts if x["room"]))
            room_title = ""
            teacher = " · ".join(dict.fromkeys(x["teacher"] for x in parts if x["teacher"]))
            teachers = [t for x in parts for t in x["teachers"]]
            subgroup = ""

        # Карточка с номером пары, но совсем без содержимого — мусор
        # (обрезанная вёрстка, пустая оболочка). Пару не создаём.
        if (not any(x["subject"] or x["room"] or x["teachers"] for x in parts)
                and not (p["topics"] or p["homework"] or p["notes"])):
            continue

        # у одиночной пары детали показываем прямо на карточке,
        # у пары «по подгруппам» — внутри блока своей подгруппы
        if len(parts) == 1:
            topics = p["topics"] + parts[0]["topics"]
            homework = p["homework"] + parts[0]["homework"]
            notes = p["notes"] + parts[0]["notes"]
        else:
            topics, homework, notes = p["topics"], p["homework"], p["notes"]

        pairs.append({
            "n": p["numeral"],
            "num": roman_to_int(p["numeral"]),
            "start": st, "end": en,
            "room": room,
            "roomTitle": room_title,
            "subject": subject,
            "subjectFull": subject_full,
            "teacher": teacher,
            "teachers": teachers,
            "subgroup": subgroup,
            "topics": _dedup(topics),
            "homework": _dedup(homework),
            "notes": _dedup(notes),
            "changed": p["changed"],
            "variants": len(parts),
            "parts": parts if len(parts) > 1 else [],
        })
    pairs.sort(key=lambda x: (x["num"] or 99, x["start"] or "99:99"))
    # Практике добавляем раскладку по парам: практика занимает пары I–VI
    # (подгруппы — по 3 пары), поэтому показываем честное начало и конец
    # каждой пары по звонкам, а не приблизительный интервал карточки.
    day_slots = practice_slots(bells)
    practice = []
    for pr in d["practice"]:
        pr = dict(pr)
        pr["slots"] = list(day_slots)
        practice.append(pr)
    return {
        "ok": True,
        "groupId": int(group_id),
        "group": d["group"],
        "date": date,
        "dateText": d["dateText"],
        "published": d["published"],
        "baseMissing": d["baseMissing"],
        "pairs": pairs,
        "practice": practice,
        "consultations": d["consultations"],
        "graf": d["graf"],
        "defenses": d["defenses"],
        "notices": d["notices"],
        "fetchedAt": 0,
        "stale": False,
    }


def json_dumps(x):
    import json
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- bells page
def parse_teacher_card(html_text):
    """Карточка преподавателя: /2020/site/html/teacherinfo/<id>

    Возвращает {"full": ФИО, "position": должность, "photo": путь к фото}.
    Фото на сайте лежит по предсказуемому адресу /img/staffs/<id>.jpeg,
    но берём его из разметки — вдруг расширение другое.
    """
    out = {"full": "", "position": "", "photo": "", "facts": []}

    m = re.search(r"""<img[^>]*src=['"]([^'"]*?/img/staffs/[^'"]+)['"]""", html_text)
    if m:
        out["photo"] = m.group(1)

    root = build_dom(html_text)
    teacher_card = first(root, lambda x: x.attrs.get("id") == "teacherCard") or root

    # ФИО — заголовок карточки рядом с «(<id>)». Ищем внутри teacherCard,
    # чтобы случайно не принять за ФИО имя директора в шапке сайта.
    for n in find_all(teacher_card, lambda x: x.tag in ("h1", "h2", "h3", "h4", "h5")):
        t = clean(n.text())
        # «Юрасов Данила Дмитриевич» — три слова с заглавной
        if re.fullmatch(r"[А-ЯЁ][а-яё-]+(?:\s+[А-ЯЁ][а-яё-]+){1,2}", t):
            out["full"] = t
            break

    # Карточка содержит не только должность: образование, дату начала
    # работы и общий стаж. Сохраняем каждую пару «подпись — значение», чтобы
    # фронтенд мог показать настоящую карточку, а не только ФИО и должность.
    txt = teacher_card.norm_text()
    labels = ["Должность", "Категория", "Образование", "Работает", "Общий стаж",
              "Педагогический стаж"]

    def fact_value(label):
        others = [x for x in labels if x != label]
        stop = "|".join(re.escape(x) + r"\s*:" for x in others)
        # Значение идёт до следующей подписи либо до конца карточки.
        pat = rf"(?<!\w){re.escape(label)}\s*:\s*(.+?)(?=\s+(?:{stop})|$)"
        hit = re.search(pat, txt, re.I)
        return clean(hit.group(1)).rstrip(",;") if hit else ""

    position = fact_value("Должность")
    if position:
        out["position"] = position
    for label in ("Категория", "Образование", "Работает", "Общий стаж",
                  "Педагогический стаж"):
        value = fact_value(label)
        if value and not any(x["label"] == label for x in out["facts"]):
            out["facts"].append({"label": label, "value": value})
    if out.get("position") and not any(x["label"] == "Должность" for x in out["facts"]):
        out["facts"].insert(0, {"label": "Должность", "value": out["position"]})
    if out["facts"]:
        category = next((x["value"] for x in out["facts"] if x["label"] == "Категория"), "")
        if category:
            out["category"] = category

    # Ссылка нужна даже в статическом режиме: на официальной карточке могут
    # появиться новые разделы (повышение квалификации и достижения), которых
    # нет в расписании и которые сайт подгружает отдельно.
    mid = re.search(r"\((\d+)\)", teacher_card.text())
    if mid:
        out["id"] = int(mid.group(1))
        out["sourceUrl"] = f"https://almetpt.ru/2020/site/html/teacherinfo/{mid.group(1)}"
    return out


def parse_bells(html_text):
    dom = build_dom(html_text)
    out = []
    for row in find_all(dom, lambda n: n.tag == "div" and n.has_class("d-flex", "flex-row", "hoverable")):
        raw = clean(row.text())
        m = re.match(r"([IVX]+)\s*пара\s*(\d{2})\s*(\d{2})\s*-\s*(\d{2})\s*(\d{2})", raw)
        if m:
            out.append((m.group(1), f"{m.group(2)}:{m.group(3)}", f"{m.group(4)}:{m.group(5)}"))
    return out or BELLS_FALLBACK
