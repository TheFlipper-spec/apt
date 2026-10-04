# -*- coding: utf-8 -*-
"""
Синтетические страницы, повторяющие разметку almetpt.ru.

Структура восстановлена по реальным страницам сайта (классы .myCard,
.card-header > span.h3, .d-flex.flex-column для подгрупп, span.Staff,
.subGroup1/.subGroup2/.nosubGroup, .pl-3 для темы и Д/З, .changesPair
для замен). Нужны, чтобы тесты парсера работали без доступа к сайту.
"""

HEAD = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Расписание занятий групп</title></head><body>
<header>шапка сайта</header><main>"""
FOOT = """</main><footer>подвал</footer></body></html>"""


def _pair_card(numeral, t1, t2, variants, lines="", changed=False):
    vs = []
    for v in variants:
        sub = v.get("sub", "")
        sub_cls = {"1": "subGroup1", "2": "subGroup2"}.get(sub, "nosubGroup")
        sub_txt = {"1": "1 п/гр.", "2": "2 п/гр."}.get(sub, "")
        room = v.get("room", "")
        room_html = (f'<span class="text-nowrap">ауд.'
                     f'<a href="/fulltime/rooms?idAudience=41" title="{v.get("roomTitle", "")}">{room}</a>'
                     f'</span>') if room else ""
        tid_attr = f' data-id={v["teacherId"]}' if v.get("teacherId") else ""
        vs.append(f"""
      <div class="d-flex flex-column col-12 {'changesPair' if v.get('changed') else ''}">
        <div class="d-flex flex-row align-items-center">
          <span class="rounded px-1 {sub_cls}"><span class="{sub_cls}">{sub_txt}</span></span>
          {room_html}
          <span class="Staff d-md-none"{tid_attr} title="{v.get('teacherFull', '')}">{v.get('teacher', '')}</span>
          <span class="d-none d-md-block">преп.<span class="h5">{v.get('teacherFull', '')}</span></span>
        </div>
        <div class="d-md-none" title="{v.get('subjectFull', '')}">{v.get('subject', '')}</div>
        <div class="d-none d-md-block"><b>{v.get('subject', '')}</b> <small>{v.get('subjectFull', '')}</small></div>
        {v.get('lines', '')}
      </div>""")
    return f"""
  <div class="card myCard mb-2 {'changesPair' if changed else ''}">
    <div class="card-header d-flex">
      <span class="h3">{numeral}</span> пара
      <span class="text-nowrap ml-auto">{t1[:2]}<sup>{t1[3:]}</sup> - {t2[:2]}<sup>{t2[3:]}</sup></span>
    </div>
    <div class="card-body">{''.join(vs)}{lines}</div>
  </div>"""


def lines(topic=None, hw=None, note=None):
    out = []
    if topic:
        out.append(f'<div class="pl-3">Тема: {topic}</div>')
    if hw:
        out.append(f'<div class="pl-3">Д.з: {hw}</div>')
    if note:
        out.append(f'<div class="pl-3">{note}</div>')
    if not out:
        return ""
    return '<div class="d-none d-md-flex flex-column">' + "".join(out) + "</div>"


GRAF = """
<div class="header3">График учебного процесса на 2026 - 2027 учебный год</div>
<div class="card myCard">
  <div class="d-flex flex-row border-bottom">
    <div class="bg-light text-nowrap">01/09-19/10/2026</div>
    <div class="text-nowrap">7 недель</div>
    <div class="pl-2 text-wrap">Обучение по дисциплинам</div>
  </div>
  <div class="d-flex flex-row border-bottom">
    <div class="bg-light text-nowrap">20/10-02/11/2026, 20/10-02/11/2026</div>
    <div class="text-nowrap">4 дня (72 часов)</div>
    <div><b>УП 03</b> <small>Учебная практика ПМ 03</small></div>
    рук.<span class="Staff" title="Зинкина Алия Фанисовна">Зинкина А.Ф.</span>
  </div>
</div>"""


def day_with_pairs():
    """Обычный день: одиночные пары, пара по подгруппам, замена, on-line."""
    body = ['<div class="header3">Расписание занятий группы СА-231б'
            '<span> на понедельник, 5 октября 2026 года</span></div>']
    body.append(_pair_card("I", "08:00", "09:20", [
        {"sub": "1", "room": "233", "roomTitle": "Лаборатория ИТ",
         "teacher": "Юрасов Д.Д.", "teacherFull": "Юрасов Данила Дмитриевич", "teacherId": 1100,
         "subject": "03.03/09.0", "subjectFull": "Безопасность сетевой инфраструктуры"},
        {"sub": "2", "room": "318", "roomTitle": "Кабинет иностранного языка",
         "teacher": "Усманова Л.М.", "teacherFull": "Усманова Лилия Мансуровна", "teacherId": 108,
         "subject": "ПрофИнЯз", "subjectFull": "Иностранный язык в проф. деятельности",
         "lines": lines(topic='Лексический материал "Проживание в гостинице" (Практическая)',
                        hw="Выполнить задания в Moodle")},
    ]))
    body.append(_pair_card("II", "09:30", "10:50", [
        {"room": "236", "roomTitle": "Лаборатория моделирования",
         "teacher": "Зинкин Д.В.", "teacherFull": "Зинкин Дмитрий Владимирович", "teacherId": 874,
         "subject": "05.03/09.0", "subjectFull": "Технологии хранения и анализа данных"},
    ], lines=lines(topic="Конфигурирование SMB (Практическая)", hw="Оформить отчёт")))
    body.append(_pair_card("III", "11:20", "12:40", [
        {"room": "on-line", "teacher": "Иванов И.И.", "teacherFull": "Иванов Иван Иванович",
         "subject": "Информатик", "subjectFull": "Информатика"},
    ]))
    body.append(_pair_card("IV", "12:50", "14:10", [
        {"room": "101", "teacher": "Петров П.П.", "teacherFull": "Петров Пётр Петрович",
         "subject": "Матем", "subjectFull": "Математика", "changed": True},
    ], changed=True))
    body.append(GRAF)
    return HEAD + "".join(body) + FOOT


def day_not_published():
    return HEAD + """
  <div class="alert alert-warning">Расписание занятий группы СА-231б на вторник,
    6 октября 2026 года не опубликовано</div>""" + GRAF + FOOT


def day_base_missing():
    """День, где есть только замены — сайт дописывает отдельный alert."""
    return (HEAD
            + '<div class="header3">Расписание занятий группы СА-231б'
              '<span> на среду, 7 октября 2026 года</span></div>'
            + _pair_card("I", "08:00", "09:20", [
                {"room": "233", "teacher": "Юрасов Д.Д.", "teacherFull": "Юрасов Данила Дмитриевич", "teacherId": 1100,
                 "subject": "ОБЖ", "subjectFull": "Основы безопасности"}])
            + GRAF
            + '<div class="alert alert-info">Основное расписание отсутствует</div>'
            + FOOT)


def day_empty():
    """Страница есть, пар нет, уведомлений тоже."""
    return (HEAD
            + '<div class="header3">Расписание занятий группы СА-231б'
              '<span> на субботу, 10 октября 2026 года</span></div>'
            + GRAF + FOOT)


def day_consultations():
    return (HEAD
            + '<div class="header3">Расписание занятий группы СА-231б'
              '<span> на четверг, 8 октября 2026 года</span></div>'
            + _pair_card("I", "08:00", "09:20", [
                {"room": "233", "teacher": "Юрасов Д.Д.", "teacherFull": "Юрасов Данила Дмитриевич", "teacherId": 1100,
                 "subject": "ОБЖ", "subjectFull": "Основы безопасности"}])
            + '<div class="header3">Консультации преподавателей</div>'
              '<div class="card myCard">Зинкин Д.В. — вторник, 15:50, ауд. 236</div>'
            + GRAF + FOOT)


def groups_page(n=4):
    rows = []
    data = [(1035, "СА-231б", "I"), (1042, "СА-232б", "II"),
            (1058, "ТМ-241", "III"), (1094, "ЭК-251", "IV")]
    for gid, name, course in data[:n]:
        rows.append(f'<div class="card-header collapsed" data-id="{gid}">{name}, {course} курс</div>')
    return HEAD + "".join(rows) + FOOT


def bells_page():
    rows = []
    for roman, a, b in [("I", "0800", "0920"), ("II", "0930", "1050"), ("III", "1120", "1240")]:
        rows.append(f"""<div class="d-flex flex-row hoverable">
          <div class="pair">{roman} пара</div>
          <div class="text-nowrap">{a[:2]}<sup>{a[2:]}</sup> - {b[:2]}<sup>{b[2:]}</sup></div>
        </div>""")
    return HEAD + "".join(rows) + FOOT


def not_found_page():
    return HEAD + "<h1>Страничка не найдена</h1>" + FOOT


def blocked_page():
    """Заглушка провайдера/капча — парсер не должен принять её за расписание."""
    return ("<!doctype html><html><head><title>Доступ ограничен</title></head>"
            "<body><main><div class=\"alert\">Проверка безопасности соединения</div>"
            "</main></body></html>")
