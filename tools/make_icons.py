#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генерация PNG-иконок приложения без внешних зависимостей
(в образе нет rsvg/Pillow, поэтому рисуем сами по геометрии static/icons/icon.svg).

    python3 tools/make_icons.py
"""
import math
import os
import struct
import zlib

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "static", "icons")

BG = (0x1f, 0x4f, 0xd8)
AMBER = (0xf2, 0xa9, 0x3b)
WHITE = (255, 255, 255)
SS = 3                                   # суперсэмплинг


# ------------------------------------------------------------------ геометрия
def rrect_sd(x, y, cx, cy, hw, hh, r):
    """Знаковое расстояние до скруглённого прямоугольника."""
    qx = abs(x - cx) - (hw - r)
    qy = abs(y - cy) - (hh - r)
    return (math.hypot(max(qx, 0.0), max(qy, 0.0))
            + min(max(qx, qy), 0.0) - r)


def poly_sd(x, y, pts):
    """Знаковое расстояние до простого многоугольника."""
    d = 1e18
    s = 1.0
    n = len(pts)
    for i in range(n):
        ax, ay = pts[i]
        bx, by = pts[(i - 1) % n]
        ex, ey = bx - ax, by - ay
        wx, wy = x - ax, y - ay
        denom = ex * ex + ey * ey
        t = 0.0 if denom == 0 else max(0.0, min(1.0, (wx * ex + wy * ey) / denom))
        px, py = wx - ex * t, wy - ey * t
        d = min(d, px * px + py * py)
        if (y >= ay and y < by and ex * wy > ey * wx) or (y < ay and y >= by and ex * wy < ey * wx):
            s = -s
    return s * math.sqrt(d)


def over(dst, src, a):
    if a <= 0:
        return dst
    if a >= 1:
        return src
    return tuple(int(round(d + (s - d) * a)) for d, s in zip(dst, src))


def cov(sd):
    """Покрытие пикселя по знаковому расстоянию (сглаживание)."""
    return max(0.0, min(1.0, 0.5 - sd))


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
        base, alpha = BG, plate
    col = base

    # ---- монограмма «А» (совпадает с static/icons/icon.svg)
    dy = 4 if bg is not None else 0
    outer = [(X(v[0]), X(v[1] - dy)) for v in ((232, 100), (280, 100), (408, 404), (342, 404), (256, 200), (170, 404), (104, 404))]
    hull = [(X(v[0]), X(v[1] - dy)) for v in ((232, 100), (280, 100), (408, 404), (104, 404))]
    col = over(col, WHITE, cov(poly_sd(x, y, outer)))

    # ---- янтарная перекладина внутри контура «А»
    bar_sd = max(poly_sd(x, y, hull),
                 rrect_sd(x, y, X(256), X(326 - dy), 166 * k, 22 * k, 0))
    col = over(col, AMBER, cov(bar_sd))

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
        ("apple-touch-icon.png", 180, 0.0, BG),
        ("maskable-512.png", 512, 0.11, BG),
        ("favicon-32.png", 32, 0.0, None),
    ]
    for name, n, pad, bg in jobs:
        size = write_png(os.path.join(OUT, name), render(n, pad, bg), n)
        print(f"{name}: {n}×{n}, {size} Б")


if __name__ == "__main__":
    main()
