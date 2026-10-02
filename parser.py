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
        t = _html.unescape(self.text()).replace(" ", " ")
        return re.sub(r"\s+", " ", t).strip()

    def has_class(self, *cls):
        mine = set(self.attrs.get("class", "").split())
        return all(c in mine for c in cls)

    def classes(self):
        return set(self.attrs.get("class", "").split())


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


def first(node, pred):
    for n in walk(node):
        if n is not node and pred(n):
            return n
    return None


def clean(s):
    if s is None:
        return ""
    t = _html.unescape(str(s)).replace(" ", " ")
    return re.sub(r"\s+", " ", t).strip()


# ---------------------------------------------------------------- roman
def roman_to_int(r):
    vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
    total, prev = 0, 0
    for ch in reversed(r.strip()):
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
    ("VIII","18:40", "20:00"),
]


def _parse_time(text):
    """'0930 - 1050' (из sup-разметки) -> ('09:30','10:50')"""
    nums = re.findall(r"\d{1,2}", text.replace("\n", ""))
    nums = [x.zfill(2) for x in nums]
    if len(nums) >= 4:
        return (f"{nums[0]}:{nums[1]}", f"{nums[2]}:{nums[3]}")
    if len(nums) == 2:
        return (f"{nums[0]}:{nums[1]}", "")
    return ("", "")


def parse_day(html_text):
    """
    Страница /2020/site/schedule/group/<id>/<date> -> dict:
    {group, dateText, pairs:[...], consultations:[...], graf:[...], notices:[...]}
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
        "group": "", "dateText": "", "published": True,
        "pairs": [], "consultations": [], "graf": [], "notices": [],
    }
    section = "head"

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
                elif "основное расписание отсутствует" not in low:
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
            else:
                section = "other:" + title[:60]
            continue

        # ----- card
        if section == "pairs":
            pair = _parse_pair_card(node)
            if pair:
                result["pairs"].append(pair)
        elif section == "consult":
            txt = node.norm_text()
            if txt:
                result["consultations"].append(txt)
        elif section == "graf":
            result["graf"].extend(_parse_graf_card(node))

    return result


def _parse_pair_card(card):
    header = first(card, lambda n: n.tag == "div" and n.has_class("card-header"))
    if not header:
        return None
    n_rom = first(header, lambda n: n.tag == "span" and n.has_class("h3"))
    numeral = clean(n_rom.text()) if n_rom else ""

    time_spans = find_all(header, lambda n: n.tag == "span")
    t_start = t_end = ""
    for sp in time_spans:
        raw = clean(sp.text()).replace(" ", "")
        if re.fullmatch(r"\d{3,4}-\d{3,4}", raw):
            t_start, t_end = _parse_time(raw)
            break

    body = first(card, lambda n: n.tag == "div" and n.has_class("card-body"))
    rows = []
    if body:
        # каждый "d-flex flex-column" — вариант (подгруппа)
        row_divs = find_all(body, lambda n: n.tag == "div" and n.has_class("d-flex", "flex-column"))
        if not row_divs:
            row_divs = [body]
        for rd in row_divs:
            row = {}
            # подгруппа
            sub_span = first(rd, lambda n: n.tag == "span" and n.has_class("rounded"))
            subgroup = ""
            if sub_span:
                for s in find_all(sub_span, lambda n: n.tag == "span"):
                    cl = s.classes()
                    lbl = s.norm_text()
                    if not lbl:
                        continue
                    if "subGroup1" in cl:
                        subgroup = "1 подгруппа"
                    elif "subGroup2" in cl:
                        subgroup = "2 подгруппа"
                    elif "nosubGroup" in cl:
                        subgroup = "без подгруппы"
                if not subgroup:
                    tt = sub_span.norm_text()
                    subgroup = tt
            # аудитория
            room, room_title = "", ""
            for sp in find_all(rd, lambda n: n.tag == "span"):
                t = sp.text()
                if "ауд." in t:
                    a = first(sp, lambda n: n.tag == "a")
                    if a is not None and clean(a.text()):
                        room = clean(a.text())
                        room_title = clean(a.attrs.get("title", ""))
                    else:
                        inner = first(sp, lambda n: n.tag == "span" and n.has_class("h5"))
                        room = clean(inner.text()) if inner else clean(t).replace("ауд.", "").strip()
                        room = room.replace("ауд.", "").strip()
                    break
            # преподаватель(и)
            teachers = []
            for st in find_all(rd, lambda n: n.tag == "span" and n.has_class("Staff")):
                teachers.append({"short": clean(st.text()), "full": clean(st.attrs.get("title", ""))})
            full_span = None
            for sp in find_all(rd, lambda n: n.tag == "span" and n.has_class("d-none", "d-md-block")):
                if "преп." in sp.text():
                    inner = first(sp, lambda n: n.tag == "span" and n.has_class("h5"))
                    full_span = clean(inner.text()) if inner else clean(sp.text()).replace("преп.", "").strip()
                    break
            # дисциплина
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
            if room or teachers or subj:
                rows.append({
                    "subgroup": subgroup,
                    "room": room,
                    "roomTitle": room_title,
                    "teachers": teachers,
                    "teacherFull": full_span or (teachers[0]["full"] if teachers else ""),
                    "subject": subj,
                    "subjectFull": subj_full,
                })

    # тема / домашка
    topics, hw, notes = [], [], []
    for d in find_all(card, lambda n: n.tag == "div" and n.has_class("d-none", "d-md-flex")):
        for ln in find_all(d, lambda n: n.tag == "div" and n.has_class("pl-3")):
            txt = ln.norm_text()
            if not txt:
                continue
            if txt.startswith("Тема"):
                topics.append(re.sub(r"^Тема:\s*", "", txt))
            elif re.match(r"^Д\.?\s*з", txt):
                hw.append(re.sub(r"^Д\.?\s*з:\s*", "", txt))
            else:
                notes.append(txt)

    changed = bool(find_all(card, lambda n: "changesPair" in n.classes()))
    if not rows:
        return None
    return {
        "numeral": numeral,
        "start": t_start,
        "end": t_end,
        "variant": rows[0] if len(rows) == 1 else None,
        "variants": rows,
        "topics": topics,
        "homework": hw,
        "notes": notes,
        "changed": changed,
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
        if len(variants) > 1:
            variant = {
                "room": " / ".join(sorted({v["room"] for v in variants if v["room"]})),
                "roomTitle": "",
                "teacherFull": " / ".join([v["teacherFull"] for v in variants if v["teacherFull"]]),
                "teachers": [t for v in variants for t in v["teachers"]],
                "subject": " / ".join(sorted({v["subject"] for v in variants if v["subject"]})),
                "subjectFull": "",
                "subgroup": "",
            }
        else:
            variant = variants[0]
        # если короткое название — просто обрезок полного ("Информатик" -> "Информатика"),
        # оставляем только полное
        subj, full = variant.get("subject", ""), variant.get("subjectFull", "")
        if subj and full:
            ns = re.sub(r"[\s.]+", "", subj.lower())
            nf = re.sub(r"[\s.]+", "", full.lower())
            if nf.startswith(ns) and 5 <= len(ns) and len(nf) <= len(ns) + 4:
                subj, full = full, ""
        pairs.append({
            "n": p["numeral"],
            "num": roman_to_int(p["numeral"]),
            "start": st, "end": en,
            "room": variant.get("room", ""),
            "roomTitle": variant.get("roomTitle", ""),
            "subject": subj,
            "subjectFull": full,
            "teacher": variant.get("teacherFull", "") or
                       " / ".join(t["short"] for t in variant.get("teachers", [])),
            "teachers": variant.get("teachers", []),
            "subgroup": variant.get("subgroup", ""),
            "topics": p["topics"],
            "homework": p["homework"],
            "notes": p["notes"],
            "changed": p["changed"],
            "variants": len(variants),
        })
    return {
        "ok": True,
        "groupId": int(group_id),
        "group": d["group"],
        "date": date,
        "dateText": d["dateText"],
        "published": d["published"],
        "pairs": pairs,
        "consultations": d["consultations"],
        "graf": d["graf"],
        "notices": d["notices"],
        "fetchedAt": 0,
        "stale": False,
    }


def json_dumps(x):
    import json
    return json.dumps(x, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- bells page
def parse_bells(html_text):
    dom = build_dom(html_text)
    out = []
    for row in find_all(dom, lambda n: n.tag == "div" and n.has_class("d-flex", "flex-row", "hoverable")):
        rn = first(row, lambda n: n.tag == "div" and n.has_class("pair"))
        ts = first(row, lambda n: n.tag == "div" and "text-nowrap" in n.classes() and "<sup>" not in str(n))
        raw = clean(row.text())
        m = re.match(r"([IVX]+)\s*пара\s*(\d{2})\s*(\d{2})\s*-\s*(\d{2})\s*(\d{2})", raw)
        if m:
            out.append((m.group(1), f"{m.group(2)}:{m.group(3)}", f"{m.group(4)}:{m.group(5)}"))
    return out or BELLS_FALLBACK
