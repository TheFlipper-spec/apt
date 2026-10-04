#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генерация PNG-иконок приложения без внешних зависимостей
(в образе нет rsvg/Pillow, поэтому рисуем сами).

    python3 tools/make_icons.py
"""
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "icons")

GRAD_A = (0x4f, 0x63, 0xf0)
GRAD_B = (0x8b, 0x5c, 0xf6)
WHITE = (255, 255, 255)
SS = 3                                   # суперсэмплинг


# ------------------------------------------------------------------ геометрия
def rrect_sd(x, y, cx, cy, hw, hh, r):
    """Знаковое расстояние до скруглённого прямоугольника."""
    qx = abs(x - cx) - (hw - r)
    qy = abs(y - cy) - (hh - r)
    return (math.hypot(max(qx, 0.0), max(qy, 0.0))
            + min(max(qx, qy), 0.0) - r)


def circle_sd(x, y, cx, cy, r):
    return math.hypot(x - cx, y - cy) - r


def seg_sd(x, y, ax, ay, bx, by):
    pax, pay = x - ax, y - ay
    bax, bay = bx - ax, by - ay
    denom = bax * bax + bay * bay
    h = 0.0 if denom == 0 else max(0.0, min(1.0, (pax * bax + pay * bay) / denom))
    return math.hypot(pax - bax * h, pay - bay * h)


def over(dst, src, a):
    if a <= 0:
        return dst
    if a >= 1:
        return src
    return tuple(int(round(d + (s - d) * a)) for d, s in zip(dst, src))


def cov(sd):
    """Покрытие пикселя по знаковому расстоянию (сглаживание)."""
    return max(0.0, min(1.0, 0.5 - sd))


def shade(x, y, n):
    """Диагональный градиент фона."""
    t = max(0.0, min(1.0, (x / n + y / n) / 2))
    return tuple(int(round(a + (b - a) * t)) for a, b in zip(GRAD_A, GRAD_B))


def render(n, pad_ratio=0.0, bg=None):
    """
    Рисует иконку n×n. pad_ratio — поля для maskable-варианта
    (safe zone: контент внутри 80 % окружности).
    """
    s = n * SS
    inner = s * (1 - 2 * pad_ratio)
    off = (s - inner) / 2
    k = inner / 512.0                      # исходный холст 512

    def X(v):
        return off + v * k

    rows = []
    for py in range(n):
        row = bytearray()
        for px in range(n):
            acc = [0.0, 0.0, 0.0, 0.0]
            for sy in range(SS):
                for sx in range(SS):
                    x = px * SS + sx + 0.5
                    y = py * SS + sy + 0.5
                    col, alpha = pixel(x, y, X, k, s, bg)
                    acc[0] += col[0] * alpha
                    acc[1] += col[1] * alpha
                    acc[2] += col[2] * alpha
                    acc[3] += alpha
            m = SS * SS
            a = acc[3] / m
            if a > 0:
                row += bytes((int(round(acc[0] / acc[3])),
                              int(round(acc[1] / acc[3])),
                              int(round(acc[2] / acc[3])),
                              int(round(a * 255))))
            else:
                row += bytes((0, 0, 0, 0))
        rows.append(bytes(row))
    return rows


def pixel(x, y, X, k, s, bg):
    # ---- подложка
    if bg is not None:
        base, alpha = bg, 1.0
    else:
        plate = cov(rrect_sd(x, y, s / 2, s / 2, s / 2, s / 2, 112 * (s / 512.0)))
        if plate <= 0:
            return (0, 0, 0), 0.0
        base, alpha = shade(x, y, s), plate
    col = base

    sw = 26 * k / 2                        # половина толщины обводки

    # ---- рамка «календаря»
    frame = abs(rrect_sd(x, y, X(256), X(258), X(140) - X(0), X(126) - X(0), 40 * k)) - sw
    col = over(col, WHITE, cov(frame))

    # ---- верхняя линия и «ушки»
    line = seg_sd(x, y, X(116), X(206), X(396), X(206)) - sw
    col = over(col, WHITE, cov(line))
    for ex in (186, 326):
        ear = seg_sd(x, y, X(ex), X(104), X(ex), X(160)) - sw
        col = over(col, WHITE, cov(ear))

    # ---- «пары» внутри
    for cx, cy, hw in ((202, 261, 42), (230, 319, 70)):
        bar = rrect_sd(x, y, X(cx), X(cy), hw * k, 13 * k, 13 * k)
        col = over(col, WHITE, cov(bar))
    for cx, cy in ((300, 261), (340, 319)):
        dot = circle_sd(x, y, X(cx), X(cy), 15 * k)
        col = over(col, WHITE, cov(dot))

    return col, alpha


# ------------------------------------------------------------------ PNG
def write_png(path, rows, n):
    raw = b"".join(b"\x00" + r for r in rows)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)
    return len(png)


def main():
    os.makedirs(OUT, exist_ok=True)
    jobs = [
        ("icon-192.png", 192, 0.0, None),
        ("icon-512.png", 512, 0.0, None),
        ("apple-touch-icon.png", 180, 0.0, None),
        ("maskable-512.png", 512, 0.12, GRAD_A),
        ("favicon-32.png", 32, 0.0, None),
    ]
    for name, n, pad, bg in jobs:
        size = write_png(os.path.join(OUT, name), render(n, pad, bg), n)
        print(f"{name}: {n}×{n}, {size} Б")


if __name__ == "__main__":
    main()
