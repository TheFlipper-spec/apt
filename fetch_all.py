#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Бот для GitHub Actions: собирает расписание almetpt.ru в статические JSON.

Результат (папка docs/ — её и раздаёт GitHub Pages):
  docs/<всё из static/>                 <- фронтенд, манифест, сервис-воркер, иконки
  docs/.nojekyll
  docs/data/groups.json                 <- список групп
  docs/data/bells.json                  <- время пар
  docs/data/meta.json                   <- когда собрано, за какие даты есть данные
  docs/data/summary.json                <- компактный индекс «группа × дата → сколько пар»
  docs/data/d/<groupId>/<date>.json     <- расписание группы на день
  docs/data/x/<date>.json               <- все занятия дня (поиск преподавателя/аудитории)

Запуск:
  python3 fetch_all.py                     # окно: вчера .. +3 дня плюс текущая и следующая недели
  python3 fetch_all.py --days -2..5 --workers 6
  python3 fetch_all.py --full              # игнорировать TTL, перекачать всё окно

ВАЖНО про свежесть: возраст данных определяется полем ``fetchedAt`` внутри
самого JSON-файла, а НЕ временем модификации файла. В CI ``actions/checkout``
выставляет всем файлам mtime «сейчас», поэтому проверка по mtime приводила к
тому, что ни один уже существующий день никогда не перезапрашивался.
"""
import argparse
import concurrent.futures as cf
import gzip
import io
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import date as Date, datetime, timedelta
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
import parser as P  # noqa: E402

SITE = "https://almetpt.ru"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TZ = ZoneInfo("Europe/Moscow")

# ----------------------------------------------------------------- TTL (сек)
TTL_TODAY = 10 * 60          # сегодня — меняется чаще всего (замены, темы, Д/З)
TTL_NEAR = 10 * 60           # завтра и ближайшие 2 дня: публикация часто в 12–14 МСК
TTL_FAR = 3 * 3600           # дальше по окну — расписание там обычно ещё не выложено
TTL_SUNDAY = 12 * 3600       # воскресенья почти всегда пустые
TTL_PAST = 12 * 3600         # прошлое без расписания — вдруг выложат задним числом
TTL_SETTLED = 7 * 24 * 3600  # прошедший опубликованный день уже не изменится

_print_lock = threading.Lock()


def log(*a):
    with _print_lock:
        print(time.strftime("[%H:%M:%S]"), *a, flush=True)


# ----------------------------------------------------------------- сеть
def http_get(url, timeout=25, retries=3):
    """GET с повторами, gzip и понятным текстом ошибки."""
    err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ru,en;q=0.8",
                "Accept-Encoding": "gzip",
                "Connection": "close",
            })
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if (r.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
            return raw.decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            # 404/410 повторять бессмысленно
            if e.code in (404, 410):
                raise
            err = e
        except Exception as e:                       # noqa: BLE001
            err = e
        if attempt < retries:
            time.sleep(min(1.5 * attempt, 5))
    raise err


# ----------------------------------------------------------------- файлы
def wjson(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    os.replace(tmp, path)


def write_if_changed(path, obj):
    """Перезаписывает файл только при реальном изменении — бережём историю git."""
    new = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == new:
                return False
    except OSError:
        pass
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    os.replace(tmp, path)
    return True


def rjson(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                # noqa: BLE001
        return None


def parse_days_arg(s):
    m = re.fullmatch(r"\s*(-?\d+)\s*\.\.\s*(\+?-?\d+)\s*", s)
    if not m:
        raise SystemExit(f"Неверный формат --days: {s!r} (пример: -1..3)")
    return int(m.group(1)), int(m.group(2))


# ----------------------------------------------------------------- даты
def window_dates(today, d0, d1, weeks=2):
    """
    Окно сборки: явный диапазон --days плюс текущая и следующая календарные
    недели целиком. Недели нужны, чтобы на зеркале работал обзор «Неделя»
    и точки с количеством пар в полоске дней.
    """
    days = set()
    for off in range(min(d0, d1), max(d0, d1) + 1):
        days.add(today + timedelta(days=off))
    monday = today - timedelta(days=today.weekday())
    for w in range(max(1, weeks)):
        for i in range(7):
            days.add(monday + timedelta(days=w * 7 + i))
    return sorted(days)


def payload_key(data):
    """Содержимое дня без служебных полей — чтобы не переписывать файл зря."""
    if not data:
        return None
    skip = {"fetchedAt", "checkedAt", "recheckedAt", "source", "stale", "v"}
    return json.dumps({k: v for k, v in data.items() if k not in skip},
                      ensure_ascii=False, sort_keys=True)


# Версия формата дня. Её увеличение заставляет бота один раз перекачать всё
# окно: иначе после доработки парсера старые файлы остались бы без новых полей
# (например, без id преподавателей) до истечения их собственного TTL.
SCHEMA = 3


def ttl_for(d: Date, today: Date, stored):
    """Сколько секунд сохранённый файл считается свежим. 0 — качать обязательно."""
    if not stored or not stored.get("fetchedAt"):
        return 0
    if int(stored.get("v") or 0) != SCHEMA:      # файл собран старым парсером
        return 0
    delta = (d - today).days
    if delta < 0:
        published = bool(stored.get("published")) and bool(stored.get("pairs"))
        return TTL_SETTLED if published else TTL_PAST
    if d.weekday() == 6 and delta != 0:              # воскресенье
        return TTL_SUNDAY
    if delta == 0:
        return TTL_TODAY
    if delta <= 3:
        return TTL_NEAR
    return TTL_FAR


# ----------------------------------------------------------------- главное
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="-1..3", help="диапазон дат от сегодня, напр. -1..3")
    ap.add_argument("--weeks", type=int, default=2,
                    help="сколько календарных недель держать целиком (с текущей)")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=os.path.join(BASE, "docs"))
    ap.add_argument("--full", action="store_true", help="игнорировать TTL")
    ap.add_argument("--offline", action="store_true",
                    help="без сети: только скопировать static/ и пересобрать индексы")
    ap.add_argument("--keep-days", type=int, default=14, help="через сколько дней удалять прошлое")
    args = ap.parse_args()
    d0, d1 = parse_days_arg(args.days)

    today = datetime.now(TZ).date()
    dates = [d.isoformat() for d in window_dates(today, d0, d1, args.weeks)]
    log("Окно сборки:", dates[0], "…", dates[-1], f"({len(dates)} дней)")

    out = args.out
    data_dir = os.path.join(out, "data")
    os.makedirs(data_dir, exist_ok=True)

    # ------------------------------------------------ 1. static/ -> docs/
    copy_static(os.path.join(BASE, "static"), out)
    open(os.path.join(out, ".nojekyll"), "w").close()

    if args.offline:
        groups = (rjson(os.path.join(data_dir, "groups.json")) or {}).get("groups") or []
        if not groups:
            raise SystemExit("Нет docs/data/groups.json — офлайн-сборка невозможна.")
        log(f"Офлайн-сборка: групп {len(groups)}, сеть не используется")
        summary, lessons_per_date = build_indexes(data_dir, groups, dates)
        write_if_changed(os.path.join(data_dir, "summary.json"), summary)
        for date_s, lessons in lessons_per_date.items():
            write_if_changed(os.path.join(data_dir, "x", f"{date_s}.json"),
                             {"ok": True, "date": date_s, "lessons": lessons})
        meta = rjson(os.path.join(data_dir, "meta.json")) or {}
        meta.update({"ok": True, "today": today.isoformat(), "dates": dates,
                     "groups": len(groups)})
        wjson(os.path.join(data_dir, "meta.json"), meta)
        log("Готово (офлайн).")
        return

    # ------------------------------------------------ 2. группы
    groups = load_groups(data_dir)
    write_if_changed(os.path.join(data_dir, "groups.json"),
                     {"ok": True, "groups": groups, "source": "bot"})

    # ------------------------------------------------ 3. звонки
    bells = load_bells(data_dir)
    write_if_changed(os.path.join(data_dir, "bells.json"),
                     {"ok": True, "bells": bells, "source": "bot"})
    bell_map = {r: (s, e) for r, s, e in bells}

    # ------------------------------------------------ 4. дни
    tasks = [(g["id"], d) for d in dates for g in groups]
    log(f"Кандидатов: {len(tasks)} (групп {len(groups)} × дней {len(dates)})")

    # Журнал проверок живёт отдельным файлом: если писать время проверки внутрь
    # каждого дня, то каждый прогон менял бы сотни файлов и репозиторий рос бы
    # на пустом месте. Сами дни переписываются только при реальном изменении.
    log_path = os.path.join(data_dir, "checked.json")
    checked = (rjson(log_path) or {}).get("at") or {}
    checked_new = {}

    counters = {"ok": 0, "same": 0, "skip": 0, "fail": 0}
    failed = []
    cnt_lock = threading.Lock()

    def load(task):
        gid, date_s = task
        key = f"{gid}/{date_s}"
        path = os.path.join(data_dir, "d", str(gid), f"{date_s}.json")
        stored = rjson(path)
        last_check = max(int(checked.get(key, 0) or 0),
                         int((stored or {}).get("fetchedAt", 0) or 0))

        if not args.full:
            ttl = ttl_for(Date.fromisoformat(date_s), today, stored)
            if ttl and time.time() - last_check < ttl:
                with cnt_lock:
                    counters["skip"] += 1
                    checked_new[key] = last_check
                return

        url = f"{SITE}/2020/site/schedule/group/{gid}/{date_s}"
        html_txt = http_get(url)
        if "Страничка не найдена" in html_txt:
            raise ValueError("страница не найдена (404)")

        data = P.build_day(gid, date_s, html_txt, bell_map)

        # страховка от «мусорного» ответа (заглушка провайдера, капча, обрезанный HTML):
        # на настоящей странице всегда есть либо название группы, либо пары.
        if not data.get("group") and not data.get("pairs"):
            raise ValueError("ответ не похож на страницу расписания")

        # прошедший день не должен «терять» уже сохранённое расписание
        if (stored and stored.get("pairs") and not data.get("pairs")
                and Date.fromisoformat(date_s) < today):
            with cnt_lock:
                counters["same"] += 1
                checked_new[key] = int(time.time())
            return

        data["source"] = "bot"
        data["groupName"] = next((g["name"] for g in groups if g["id"] == gid), "") \
            or data.get("group", "")
        now_ts = int(time.time())

        data["v"] = SCHEMA
        if (stored and int(stored.get("v") or 0) == SCHEMA
                and payload_key(stored) == payload_key(data)):
            with cnt_lock:
                counters["same"] += 1
                checked_new[key] = now_ts
            return

        data["fetchedAt"] = now_ts
        wjson(path, data)
        with cnt_lock:
            counters["ok"] += 1
            checked_new[key] = now_ts

    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=max(1, args.workers)) as ex:
        futs = {ex.submit(load, t): t for t in tasks}
        done = 0
        for fut in cf.as_completed(futs):
            t = futs[fut]
            done += 1
            try:
                fut.result()
            except Exception as e:                   # noqa: BLE001
                with cnt_lock:
                    counters["fail"] += 1
                failed.append((t, f"{e.__class__.__name__}: {e}"))
                if len(failed) <= 15:
                    log(f"!! {t[0]} {t[1]}: {e}")
            if done % 200 == 0:
                log(f"прогресс {done}/{len(tasks)} "
                    f"(обновлено {counters['ok']}, без изменений {counters['same']}, "
                    f"из кэша {counters['skip']}, ошибок {counters['fail']})")
    log(f"Дни готовы за {time.time() - t0:.0f}s: обновлено {counters['ok']}, "
        f"без изменений {counters['same']}, из кэша {counters['skip']}, "
        f"ошибок {counters['fail']}")
    wjson(log_path, {"at": checked_new})

    # ------------------------------------------------ 5. чистка устаревшего
    removed = cleanup(data_dir, today, args.keep_days, dates)
    if removed:
        log(f"Удалено устаревших файлов: {removed}")

    # ------------------------------------------------ 6. индексы
    summary, lessons_per_date = build_indexes(data_dir, groups, dates)
    write_if_changed(os.path.join(data_dir, "summary.json"), summary)
    for date_s, lessons in lessons_per_date.items():
        write_if_changed(os.path.join(data_dir, "x", f"{date_s}.json"),
                         {"ok": True, "date": date_s, "lessons": lessons})
    log(f"Индексы: summary.json + {len(lessons_per_date)} дневных срезов")

    # ------------------------------------------------ 6б. педсостав
    try:
        collect_teachers(data_dir, dates, workers=min(args.workers, 4))
    except Exception as e:
        log("Педсостав собрать не удалось:", e)

    # ------------------------------------------------ 7. meta
    published_days = sum(1 for d in dates if summary["counts"].get(d, {}).get("published"))
    wjson(os.path.join(data_dir, "meta.json"), {
        "ok": True,
        "generatedAt": int(time.time()),
        "generatedAtHuman": datetime.now(TZ).strftime("%d.%m.%Y %H:%M МСК"),
        "today": today.isoformat(),
        "dates": dates,
        "groups": len(groups),
        "fetched": counters["ok"],
        "cached": counters["skip"],
        "failed": counters["fail"],
        "publishedDays": published_days,
    })
    log("meta.json записан. Всё.")
    if failed:
        log("примеры неудачных:", failed[:5])


# ----------------------------------------------------------------- шаги
def copy_static(src, dst):
    if not os.path.isdir(src):
        return
    n = 0
    for root, _dirs, files in os.walk(src):
        rel = os.path.relpath(root, src)
        target = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(target, exist_ok=True)
        for fn in files:
            shutil.copy2(os.path.join(root, fn), os.path.join(target, fn))
            n += 1
    log(f"{os.path.basename(src)}/ -> {os.path.basename(dst)}/: {n} файл(ов)")


def load_groups(data_dir):
    """Список групп с сайта, объединённый с сохранённым.

    Сайт периодически отдаёт неполный список (в истории репозитория было
    82 → 91 → 74 группы). Если просто перезаписывать файл, у студентов
    пропадают группы, поэтому берём объединение.
    """
    fresh = []
    try:
        fresh = P.parse_groups(http_get(SITE + "/2020/site/schedulegroups"))
        log(f"Групп получено с сайта: {len(fresh)}")
    except Exception as e:                           # noqa: BLE001
        log("!! список групп не получен:", e)

    saved = (rjson(os.path.join(data_dir, "groups.json"))
             or rjson(os.path.join(BASE, "data", "groups.json")) or {})
    old = saved.get("groups") or []

    merged = {}
    for g in old:
        if g.get("id"):
            merged[int(g["id"])] = dict(g)
    for g in fresh:
        merged[int(g["id"])] = dict(g)                # свежие данные приоритетнее

    if not merged:
        raise SystemExit("Нет списка групп ни с сайта, ни из кэша — стоп.")
    if fresh and len(fresh) < len(merged):
        log(f"   (к {len(fresh)} с сайта добавлено {len(merged) - len(fresh)} из кэша)")

    return sorted(merged.values(), key=lambda g: (g.get("course") or 9, g.get("name") or ""))


def load_bells(data_dir):
    try:
        bells = P.parse_bells(http_get(SITE + "/2020/site/schedulecalls"))
    except Exception as e:                           # noqa: BLE001
        log("!! звонки не получены, берём сохранённые:", e)
        saved = (rjson(os.path.join(data_dir, "bells.json"))
                 or rjson(os.path.join(BASE, "data", "bells.json")) or {})
        bells = saved.get("bells") or P.BELLS_FALLBACK
    return [[str(x) for x in b] for b in bells]


def cleanup(data_dir, today, keep_days, dates):
    """Удаляет дни старше keep_days и дневные срезы вне окна."""
    removed = 0
    keep_min = (today - timedelta(days=max(1, keep_days))).isoformat()
    droot = os.path.join(data_dir, "d")
    if os.path.isdir(droot):
        for gid_dir in sorted(os.listdir(droot)):
            gdir = os.path.join(droot, gid_dir)
            if not os.path.isdir(gdir):
                continue
            for fn in sorted(os.listdir(gdir)):
                m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", fn)
                if m and m.group(1) < keep_min:
                    os.remove(os.path.join(gdir, fn))
                    removed += 1
            if not os.listdir(gdir):
                os.rmdir(gdir)
    xroot = os.path.join(data_dir, "x")
    if os.path.isdir(xroot):
        keep = set(dates)
        for fn in sorted(os.listdir(xroot)):
            m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.json", fn)
            if m and m.group(1) not in keep:
                os.remove(os.path.join(xroot, fn))
                removed += 1
    return removed



# ===================================================================== педсостав
PHOTO_MAX = 400 * 1024          # фото крупнее — не зеркалим, оставим ссылку
TEACHER_TTL = 30 * 24 * 3600    # карточку перечитываем раз в месяц


def http_get_bytes(url, timeout=25, tries=2):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read(), r.headers.get("Content-Type", "")
        except Exception as e:
            last = e
            time.sleep(0.4 * (i + 1))
    raise last


def collect_teachers(data_dir, dates, workers=4):
    """Собирает справочник преподавателей по id, встреченным в расписании.

    id берётся прямо из страниц расписания: <span class="Staff" data-id=1100>.
    Карточка /2020/site/html/teacherinfo/<id> даёт должность и фото,
    фото зеркалится в docs/data/teachers/<id>.jpg — чтобы сайт не зависел
    от доступности almetpt.ru и работал офлайн.
    """
    seen = {}
    d_root = os.path.join(data_dir, "d")
    if os.path.isdir(d_root):
        for gid in os.listdir(d_root):
            for fn in os.listdir(os.path.join(d_root, gid)):
                if fn[:-5] not in dates:
                    continue
                day = rjson(os.path.join(d_root, gid, fn)) or {}
                for pair in day.get("pairs", []):
                    srcs = list(pair.get("teachers") or [])
                    for part in pair.get("parts") or []:
                        srcs += part.get("teachers") or []
                    for t in srcs:
                        tid = t.get("id")
                        if tid:
                            seen.setdefault(int(tid), {}).update(
                                {k: v for k, v in t.items() if v and k != "id"})
    if not seen:
        log("Педсостав: id преподавателей в расписании не найдены")
        return

    path = os.path.join(data_dir, "teachers.json")
    store = rjson(path) or {}
    old = store.get("teachers") or {}
    now = int(time.time())
    photo_dir = os.path.join(data_dir, "teachers")

    todo = [tid for tid in seen
            if (now - int((old.get(str(tid)) or {}).get("checkedAt", 0)) > TEACHER_TTL
                or "facts" not in (old.get(str(tid)) or {}))]
    log(f"Педсостав: встречено {len(seen)}, обновить нужно {len(todo)}")

    out = dict(old)
    # имена из расписания знаем всегда, даже если карточка не открылась
    for tid, info in seen.items():
        rec = dict(out.get(str(tid)) or {})
        rec.setdefault("id", tid)
        if info.get("full"):
            rec["full"] = info["full"]
        if info.get("short"):
            rec["short"] = info["short"]
        out[str(tid)] = rec

    def one(tid):
        try:
            html = http_get(f"{SITE}/2020/site/html/teacherinfo/{tid}")
            card = P.parse_teacher_card(html)
        except Exception as e:
            return tid, None, f"карточка: {e}"
        photo_rel = None
        src = card.get("photo") or ""
        if src:
            url = src if src.startswith("http") else SITE + src
            try:
                blob, ctype = http_get_bytes(url)
                if blob[:3] == b"\xff\xd8\xff" or "image" in ctype:
                    if len(blob) <= PHOTO_MAX:
                        os.makedirs(photo_dir, exist_ok=True)
                        fp = os.path.join(photo_dir, f"{tid}.jpg")
                        if not (os.path.exists(fp) and open(fp, "rb").read() == blob):
                            with open(fp, "wb") as f:
                                f.write(blob)
                        photo_rel = f"teachers/{tid}.jpg"
            except Exception:
                pass
        return tid, (card, photo_rel, src), None

    done = fails = 0
    if todo:
        with cf.ThreadPoolExecutor(max_workers=workers) as ex:
            for tid, res, err in ex.map(one, todo):
                if err or not res:
                    fails += 1
                    continue
                card, photo_rel, src = res
                rec = dict(out.get(str(tid)) or {})
                rec["id"] = tid
                if card.get("full"):
                    rec["full"] = card["full"]
                for k in ("position", "category", "sourceUrl"):
                    if card.get(k):
                        rec[k] = card[k]
                if "facts" in card:
                    rec["facts"] = card.get("facts") or []
                # Даже если парсер не нашёл id в заголовке, URL строится по
                # id из расписания — он ведёт на официальную карточку.
                rec.setdefault("sourceUrl", f"{SITE}/2020/site/html/teacherinfo/{tid}")
                if photo_rel:
                    rec["photo"] = photo_rel
                elif src:
                    rec["photoUrl"] = src if src.startswith("http") else SITE + src
                rec["checkedAt"] = now
                out[str(tid)] = rec
                done += 1

    withphoto = sum(1 for r in out.values() if r.get("photo") or r.get("photoUrl"))
    log(f"Педсостав: всего {len(out)}, обновлено {done}, ошибок {fails}, с фото {withphoto}")
    write_if_changed(path, {"ok": True, "teachers": out})

    # подчистить фото тех, кого больше нет в расписании
    if os.path.isdir(photo_dir):
        keep = {f"{t}.jpg" for t in out}
        for fn in os.listdir(photo_dir):
            if fn not in keep:
                try:
                    os.remove(os.path.join(photo_dir, fn))
                except OSError:
                    pass


def build_indexes(data_dir, groups, dates):
    """
    summary.json  — {"g": {gid: {date: [пар, опубликовано, есть замены, начало, конец]}}}
    x/<date>.json — плоский список всех занятий дня (для поиска преподавателя/аудитории)
    """
    names = {int(g["id"]): g.get("name", "") for g in groups}
    g_index, counts = {}, {}
    lessons_per_date = {d: [] for d in dates}

    for gid in sorted(names):
        row = {}
        for date_s in dates:
            d = rjson(os.path.join(data_dir, "d", str(gid), f"{date_s}.json"))
            if not d:
                continue
            pairs = d.get("pairs") or []
            row[date_s] = [
                len(pairs),
                1 if d.get("published") else 0,
                1 if any(p.get("changed") for p in pairs) else 0,
                (pairs[0].get("start") if pairs else "") or "",
                (pairs[-1].get("end") if pairs else "") or "",
            ]
            c = counts.setdefault(date_s, {"groups": 0, "withPairs": 0, "published": 0})
            c["groups"] += 1
            c["withPairs"] += 1 if pairs else 0
            c["published"] += 1 if d.get("published") else 0
            for p in pairs:
                tlist = list(p.get("teachers") or [])
                for part in p.get("parts") or []:
                    tlist += part.get("teachers") or []
                tid = next((t.get("id") for t in tlist if t.get("id")), None)
                row_l = {
                    "g": gid, "gn": names.get(gid, ""),
                    "n": p.get("n", ""), "s": p.get("start", ""), "e": p.get("end", ""),
                    "subj": p.get("subject", "") or p.get("subjectFull", ""),
                    "t": p.get("teacher", ""),
                    "r": p.get("room", ""),
                    "sg": p.get("subgroup", ""),
                    "ch": 1 if p.get("changed") else 0,
                }
                if tid:
                    row_l["ti"] = tid           # id для аватара в поиске
                lessons_per_date[date_s].append(row_l)
        if row:
            g_index[str(gid)] = row

    summary = {
        "ok": True,
        "dates": dates,
        "names": {str(k): v for k, v in names.items()},
        "g": g_index,
        "counts": counts,
    }
    return summary, lessons_per_date


if __name__ == "__main__":
    main()
