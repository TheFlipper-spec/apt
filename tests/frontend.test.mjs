/**
 * Тесты фронтенда в jsdom: страница реально загружается, fetch подменён
 * файлами из docs/, после чего проверяется разметка.
 *
 *   npm i jsdom          (в любой папке)
 *   node --test tests/frontend.test.mjs
 *
 * Путь к jsdom можно задать переменной JSDOM_PATH.
 */
import test, { after } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createRequire } from "node:module";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.join(__dirname, "..");
const WEB  = path.join(ROOT, "tests", "fixtures", "web");   // зеркало из синтетических страниц
const DOCS = path.join(ROOT, "docs");                        // настоящие данные бота
const HTML = fs.readFileSync(path.join(ROOT, "static", "index.html"), "utf8");

const require = createRequire(process.env.JSDOM_PATH || import.meta.url);
const { JSDOM } = require("jsdom");

// jsdom оставляет живые таймеры страницы — иначе раннер не завершается
after(() => setTimeout(() => process.exit(process.exitCode || 0), 50).unref());

/* ------------------------------------------------------------------ утилиты */
function fakeFetch(opts = {}) {
  const missing = new Set(opts.missing || []);
  const roots = opts.real ? [DOCS] : [WEB, DOCS];
  const override = opts.override || {};
  return async (url) => {
    const clean = String(url).split("?")[0].replace(/^\/+/, "");
    if (clean === "api/health") return new Resp(404, "not found");
    if (missing.has(clean)) return new Resp(404, "not found");
    if (clean in override) return new Resp(200, JSON.stringify(override[clean]));
    for (const root of roots) {
      const file = path.join(root, clean);
      if (file.startsWith(root) && fs.existsSync(file))
        return new Resp(200, fs.readFileSync(file, "utf8"));
    }
    return new Resp(404, "not found");
  };
}
class Resp {
  constructor(status, body) {
    this.status = status;
    this.ok = status >= 200 && status < 300;
    this._b = body;
    this.headers = { get: () => null };
  }
  async json() { return JSON.parse(this._b); }
  async text() { return this._b; }
}

async function boot({ hash = "", now = null, fetchOpts = {}, storage = {} } = {}) {
  const dom = new JSDOM(HTML, {
    url: "https://example.test/" + hash,
    runScripts: "dangerously",
    pretendToBeVisual: true,
    beforeParse(w) {
      w.fetch = fakeFetch(fetchOpts);
      w.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
      w.scrollTo = () => {};
      w.navigator.serviceWorker = undefined;
      for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
      if (now) {
        const Real = w.Date;
        const fixed = new Real(now).getTime();
        class FakeDate extends Real {
          constructor(...a) { super(...(a.length ? a : [fixed])); }
          static now() { return fixed; }
        }
        w.Date = FakeDate;
      }
    },
  });
  // ждём, пока отработает boot() и отрисуется день
  const w = dom.window;
  for (let i = 0; i < 200; i++) {
    await new Promise((r) => setTimeout(r, 10));
    if (w.document.querySelector("#view .view")) break;
  }
  await new Promise((r) => setTimeout(r, 40));
  return dom;
}

const txt = (dom, sel) => (dom.window.document.querySelector(sel)?.textContent || "").trim();
const all = (dom, sel) => Array.from(dom.window.document.querySelectorAll(sel));

/* ------------------------------------------------------------------ тесты */
test("страница грузится, список групп и день отрисованы", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const d = dom.window.document;
  assert.ok(d.querySelector("#view .view"), "вид дня отрисован");
  assert.ok(all(dom, "#groupsGrid .g-btn").length > 0, "группы отрисованы");
  assert.equal(txt(dom, "#selBadge"), "СА-231б");
  assert.equal(txt(dom, "#hWeekday"), "Понедельник");
  dom.window.close();
});

test("пары выводятся карточками и отсортированы по времени", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const cards = all(dom, ".pair-card");
  assert.equal(cards.length, 4);
  const starts = all(dom, ".rail .t1").map((x) => x.textContent.trim());
  assert.deepEqual(starts, [...starts].sort());
  assert.ok(txt(dom, ".pair-card .pc-subj").length > 1);
  dom.window.close();
});

test("пара по подгруппам показывается отдельными блоками, а не строкой «А / Б»", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const multi = all(dom, ".pair-card").find((c) => c.querySelector(".subs"));
  assert.ok(multi, "найдена пара с подгруппами");
  const subs = multi.querySelectorAll(".sub-card");
  assert.equal(subs.length, 2);
  const tags = Array.from(subs).map((s) => s.querySelector(".sub-tag").textContent.trim());
  assert.ok(tags.some((t) => /1/.test(t)) && tags.some((t) => /2/.test(t)));
  assert.ok(!multi.textContent.includes(" / "), "названия не склеены через слеш");
  dom.window.close();
});

test("перемены и окна считаются между парами", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const brks = all(dom, ".brk").map((b) => b.textContent.replace(/\s+/g, " ").trim());
  assert.ok(brks.length >= 3);
  assert.ok(brks.some((b) => /перемена/.test(b)));
  assert.ok(brks.every((b) => !/NaN|undefined/.test(b)), "нет NaN в длительности");
  dom.window.close();
});

test("«не опубликовано» и «пар нет» — разные экраны", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-06" });
  assert.match(txt(dom, ".state h3"), /не опубликовано/i);
  assert.ok(dom.window.document.querySelector("[data-act=retry]"), "есть кнопка перепроверки");
  dom.window.close();
});

test("отсутствующая дата даёт понятное состояние, а не пустой экран", async () => {
  const dom = await boot({ hash: "#/9001/2026-12-25" });
  assert.match(txt(dom, ".state h3"), /данных нет/i);
  dom.window.close();
});

test("полоска недели показывает 7 дней и точки с количеством пар", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const days = all(dom, "#weekStrip .day-b");
  assert.equal(days.length, 7);
  assert.ok(days.some((d) => d.querySelector(".dots i")), "есть дни с парами");
  assert.ok(days.some((d) => d.classList.contains("sel")), "выбранный день подсвечен");
  dom.window.close();
});

test("режим «Неделя» рисует все семь дней и сводку", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05", storage: { "apt.mode": "week" } });
  const w = dom.window;
  for (let i = 0; i < 200; i++) {
    await new Promise((r) => setTimeout(r, 10));
    if (w.document.querySelector(".wk-list .wk-day")) break;
  }
  assert.equal(all(dom, ".wk-day").length, 7);
  assert.match(txt(dom, ".strip"), /пар(ы|)\s*за неделю|пар за неделю/);
  assert.ok(all(dom, ".wk-item").length > 0, "внутри дней есть пары");
  dom.window.close();
});

test("текущая пара определяется по московскому времени, а не по часам устройства", async () => {
  // 2026-10-01 10:00 по Москве = 07:00 UTC. Пара II идёт 09:30–10:50.
  const dom = await boot({ hash: "#/9001/2026-10-05", now: "2026-10-05T07:00:00Z" });
  const nowCards = all(dom, ".pair-card.now");
  assert.equal(nowCards.length, 1, "ровно одна пара помечена как текущая");
  assert.match(nowCards[0].querySelector(".pc-num").textContent, /^II$/);
  assert.match(txt(dom, ".nowbar"), /Идёт II пара/);
  assert.equal(all(dom, ".pair-card.done").length, 1, "первая пара уже прошла");
  dom.window.close();
});

test("тот же момент в другом часовом поясе даёт тот же результат", async () => {
  process.env.TZ = "America/New_York";
  const dom = await boot({ hash: "#/9001/2026-10-05", now: "2026-10-05T07:00:00Z" });
  assert.match(all(dom, ".pair-card.now")[0].querySelector(".pc-num").textContent, /^II$/);
  dom.window.close();
  process.env.TZ = "UTC";
});

test("график учебного процесса подсвечивает текущий период", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const head = all(dom, ".info-head").find((h) => /График/.test(h.textContent));
  assert.ok(head, "карточка графика есть");
  assert.match(head.textContent, /сейчас:/);
  assert.equal(all(dom, ".graf-row.now").length, 1);
  dom.window.close();
});

test("экспорт в календарь формирует корректный .ics", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const w = dom.window;
  let captured = null;
  w.URL.createObjectURL = (blob) => { captured = blob; return "blob:x"; };
  w.URL.revokeObjectURL = () => {};
  w.HTMLAnchorElement.prototype.click = function () {};
  w.document.querySelector("[data-act=ics]").click();
  await new Promise((r) => setTimeout(r, 50));
  assert.ok(captured, "blob создан");
  const text = await captured.text();
  assert.match(text, /^BEGIN:VCALENDAR/);
  assert.match(text, /END:VCALENDAR\r\n$/);
  // 08:00 МСК -> 05:00 UTC
  assert.match(text, /DTSTART:20261005T050000Z/);
  assert.equal((text.match(/BEGIN:VEVENT/g) || []).length,
               (text.match(/END:VEVENT/g) || []).length);
  dom.window.close();
});

test("быстрое переключение дней не оставляет страницу в скелетоне", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const w = dom.window;
  w.document.querySelector("#btnNext").click();
  w.document.querySelector("#btnNext").click();
  w.document.querySelector("#btnPrev").click();
  for (let i = 0; i < 200; i++) {
    await new Promise((r) => setTimeout(r, 10));
    if (!w.document.querySelector(".skel")) break;
  }
  assert.equal(w.document.querySelectorAll(".skel").length, 0, "скелетон убран");
  assert.ok(w.document.querySelector("#view .view"), "вид отрисован");
  assert.match(w.location.hash, /2026-10-06$/);
  dom.window.close();
});

test("поиск находит преподавателя и аудиторию", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const w = dom.window;
  w.document.querySelector("#btnFind").click();
  await new Promise((r) => setTimeout(r, 120));
  const inp = w.document.querySelector("#findInput");
  inp.value = "Зинкин";
  inp.dispatchEvent(new w.Event("input"));
  await new Promise((r) => setTimeout(r, 60));
  const res = all(dom, "#findRes .res-item");
  assert.ok(res.length > 0, "преподаватель найден");
  assert.match(res[0].textContent, /Зинкин/);
  dom.window.close();
});

test("при пустом списке групп показывается внятная ошибка", async () => {
  const dom = await boot({ fetchOpts: { missing: ["data/groups.json"] } });
  await new Promise((r) => setTimeout(r, 200));
  assert.match(txt(dom, ".state h3"), /подключ|соединени|пуст/i);
  dom.window.close();
});

test("в разметке нет остатков вида undefined/NaN", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const html = dom.window.document.querySelector("#view").innerHTML;
  assert.ok(!/undefined|NaN|\[object Object\]/.test(html), "мусора в разметке нет");
  dom.window.close();
});

/* ------------------------------------------- совместимость и мелкая механика */
test("старые данные бота (без parts) рендерятся без ошибок", async () => {
  const dom = await boot({ hash: "#/1042/2026-10-01", fetchOpts: { real: true } });
  assert.ok(dom.window.document.querySelector("#view .view"));
  assert.ok(all(dom, ".pair-card").length > 0, "пары из старого формата видны");
  dom.window.close();
});

test("избранное сохраняется и выводится отдельным блоком", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const w = dom.window;
  const btn = w.document.querySelector("#groupsGrid .g-btn");
  const gid = btn.dataset.gid;
  btn.dispatchEvent(new w.MouseEvent("click", { shiftKey: true, bubbles: true }));
  await new Promise((r) => setTimeout(r, 30));
  assert.match(w.localStorage.getItem("apt.fav") || "", new RegExp(gid));
  assert.ok(w.document.querySelector("#favRow .fav-chip"), "блок избранного заполнен");
  assert.equal(w.document.querySelector("#favWrap").hidden, false);
  dom.window.close();
});

test("тема переключается и запоминается", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const w = dom.window;
  const before = w.document.documentElement.dataset.theme;
  w.document.querySelector("#btnTheme").click();
  assert.notEqual(w.document.documentElement.dataset.theme, before);
  assert.equal(w.localStorage.getItem("apt.theme"), w.document.documentElement.dataset.theme);
  dom.window.close();
});

test("горячие клавиши листают дни и открывают поиск", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const w = dom.window;
  const key = (k, mod = {}) =>
    w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key: k, bubbles: true, ...mod }));
  key("ArrowRight");
  await new Promise((r) => setTimeout(r, 60));
  assert.match(w.location.hash, /2026-10-06$/);
  key("ArrowRight", { shiftKey: true });
  await new Promise((r) => setTimeout(r, 60));
  assert.match(w.location.hash, /2026-10-13$/);
  key("/");
  await new Promise((r) => setTimeout(r, 60));
  assert.ok(w.document.querySelector("#mdFind").classList.contains("open"), "поиск открыт");
  key("Escape");
  assert.ok(!w.document.querySelector("#mdFind").classList.contains("open"), "поиск закрыт");
  dom.window.close();
});

test("устаревшее зеркало показывает предупреждение", async () => {
  const old = { ok: true, generatedAt: Math.floor(Date.now() / 1000) - 9 * 3600,
                today: "2026-10-05", dates: [], groups: 2 };
  const dom = await boot({ hash: "#/9001/2026-10-05",
                           fetchOpts: { override: { "data/meta.json": old } } });
  await new Promise((r) => setTimeout(r, 200));
  const b = dom.window.document.querySelector("#banners");
  assert.match(b.textContent, /обнов|устар|давно|almetpt/i);
  assert.ok(b.querySelector("a[href*=almetpt]"), "есть ссылка на сайт техникума");
  dom.window.close();
});

test("свежее зеркало не показывает предупреждений", async () => {
  const fresh = { ok: true, generatedAt: Math.floor(Date.now() / 1000) - 300,
                  today: "2026-10-05", dates: [], groups: 2 };
  const dom = await boot({ hash: "#/9001/2026-10-05",
                           fetchOpts: { override: { "data/meta.json": fresh } } });
  await new Promise((r) => setTimeout(r, 200));
  assert.equal(dom.window.document.querySelector("#banners").textContent.trim(), "");
  dom.window.close();
});

test("домашние задания собираются кнопкой «скопировать»", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const w = dom.window;
  let copied = "";
  w.navigator.clipboard = { writeText: async (t) => { copied = t; } };
  const btn = w.document.querySelector("[data-act=copyhw]");
  assert.ok(btn, "кнопка домашки есть");
  btn.click();
  await new Promise((r) => setTimeout(r, 60));
  assert.match(copied, /Moodle/);
  assert.match(copied, /Домашние задания · СА-231б/);
  assert.match(copied, /Пн 5 окт · ПрофИнЯз \(2 п\/гр\.\): Выполнить задания в Moodle/,
    "домашка подгруппы подписана своим предметом, а не склейкой");
  dom.window.close();
});

test("клик по дню в обзоре недели открывает этот день", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05", storage: { "apt.mode": "week" } });
  const w = dom.window;
  for (let i = 0; i < 200; i++) {
    await new Promise((r) => setTimeout(r, 10));
    if (w.document.querySelector(".wk-day")) break;
  }
  w.document.querySelectorAll("[data-goday]")[2].click();
  for (let i = 0; i < 200; i++) {
    await new Promise((r) => setTimeout(r, 10));
    if (w.document.querySelector(".pair-card") || w.document.querySelector(".state")) break;
  }
  assert.match(w.location.hash, /2026-10-07$/);
  assert.equal(w.localStorage.getItem("apt.mode"), "day");
  dom.window.close();
});

/* ------------------------------------------------- новое: макет, замены, память */
test("преподаватель показывается с аватаром, должностью и полным ФИО", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const rows = all(dom, ".tc-row");
  assert.ok(rows.length >= 2, "строки преподавателей есть");
  const t = rows.map((r) => r.textContent).join(" ");
  assert.match(t, /Юрасов Данила Дмитриевич/, "полное ФИО из справочника");
  assert.match(t, /Преподаватель информационных технологий/, "должность");
  // у Юрасова есть фото, у Усмановой нет — инициалы обязаны быть всегда
  const avas = all(dom, ".tc-row .ava");
  assert.ok(avas.every((a) => /^[А-ЯЁ]{2}$/.test(a.textContent.trim())), "инициалы во всех аватарах");
  assert.ok(avas.some((a) => a.querySelector("img[src*='teachers/1100.jpg']")), "фото подставлено");
  dom.window.close();
});

test("без справочника преподаватель всё равно виден", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05",
                           fetchOpts: { missing: ["data/teachers.json"] } });
  await new Promise((r) => setTimeout(r, 150));
  assert.match(dom.window.document.querySelector("#view").textContent, /Юрасов/);
  dom.window.close();
});

test("если весь день из таблицы замен — значков «замена» нет, есть пояснение", async () => {
  const day = JSON.parse(
    fs.readFileSync(path.join(WEB, "data/d/9001/2026-10-05.json"), "utf8"));
  day.pairs.forEach((p) => { p.changed = true; });        // так отдаёт настоящий сайт
  const dom = await boot({ hash: "#/9001/2026-10-05",
                           fetchOpts: { override: { "data/d/9001/2026-10-05.json": day } } });
  const w = dom.window;
  assert.ok(all(dom, ".pair-card").length > 1, "пары отрисованы");
  assert.equal(all(dom, ".pc-chip.changed").length, 0, "у пар нет значка «замена»");
  assert.equal(all(dom, ".pair-card.changed").length, 0, "нет оранжевых рамок");
  const why = w.document.querySelector("[data-act=whychg]");
  assert.ok(why, "есть спокойная пометка в сводке");
  why.click();
  await new Promise((r) => setTimeout(r, 60));
  assert.ok(w.document.querySelector("#mdChg").classList.contains("open"), "пояснение открылось");
  assert.match(w.document.querySelector("#mdChg").textContent, /таблиц[ауы] замен/i);
  dom.window.close();
});

test("если заменена часть пар — значок показывается", async () => {
  // фикстура как раз такая: помечена одна пара из четырёх
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  assert.equal(all(dom, ".pc-chip.changed").length, 1, "ровно один значок");
  assert.equal(all(dom, ".pair-card.changed").length, 1);
  assert.match(txt(dom, ".strip"), /1 замена/);
  assert.ok(!dom.window.document.querySelector("[data-act=whychg]"), "пояснение не нужно");
  dom.window.close();
});

test("сайт открывается на запомненной группе, без даты в ссылке", async () => {
  const dom = await boot({ storage: { "apt.group": "9002", "apt.gname": "ТМ-241" } });
  const w = dom.window;
  assert.equal(txt(dom, "#selBadge"), "ТМ-241");
  assert.match(w.location.hash, /^#\/9002$/, "в ссылке только группа — дата подставится сегодняшняя");
  dom.window.close();
});

test("ссылка с датой по-прежнему открывает именно её", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-07",
                           storage: { "apt.group": "9002", "apt.gname": "ТМ-241" } });
  assert.equal(txt(dom, "#selBadge"), "СА-231б");
  assert.match(dom.window.location.hash, /2026-10-07$/);
  dom.window.close();
});

test("боковая колонка и основной блок существуют в разметке", async () => {
  const dom = await boot({ hash: "#/9001/2026-10-05" });
  const d = dom.window.document;
  assert.ok(d.querySelector(".layout > .side"), "боковая колонка");
  assert.ok(d.querySelector(".side #weekStrip"), "полоска недели в колонке");
  assert.ok(d.querySelector(".side .sel-panel"), "выбор группы в колонке");
  assert.ok(d.querySelector(".layout > #view"), "контент — соседний блок");
  assert.ok(d.querySelector(".view.has-aside > .day-aside"), "справочные карточки вынесены вбок");
  dom.window.close();
});

test("на настоящих данных бота фото преподавателей подставляются", async () => {
  const dom = await boot({ hash: "#/1042/2026-09-28", fetchOpts: { real: true } });
  const imgs = all(dom, ".tc-row .ava img");
  assert.ok(imgs.length > 0, "есть фотографии");
  assert.ok(imgs.every((i) => /^data\/teachers\/\d+\.jpg$/.test(i.getAttribute("src"))),
    "пути ведут в зеркало, а не на almetpt.ru");
  assert.match(dom.window.document.querySelector("#view").textContent, /Преподаватель/);
  dom.window.close();
});

test("на настоящих данных значков «замена» нет: весь день из таблицы замен", async () => {
  const dom = await boot({ hash: "#/1042/2026-09-28", fetchOpts: { real: true } });
  assert.ok(all(dom, ".pair-card").length >= 3);
  assert.equal(all(dom, ".pc-chip.changed").length, 0);
  assert.ok(dom.window.document.querySelector("[data-act=whychg]"), "есть пояснение");
  dom.window.close();
});
