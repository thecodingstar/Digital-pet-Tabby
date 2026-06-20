#!/usr/bin/env python3
"""Slice the 6x5 cat-2 emotion sheet into transparent per-state PNGs.

The source has NO real alpha: its "transparent" look is a PAINTED light-gray
checkerboard (every pixel opaque). So we KEY OUT that gray checker (grayish +
bright) to alpha 0, while keeping pure-white eye-glints (>=248) and the colored
cat. Then drop the text-label strip and auto-crop by the new alpha.
Output -> cat2_states/."""
import os
from PyQt5.QtGui import QImage, qAlpha, qRed, qGreen, qBlue, qRgba

SRC = "Cat examples/cat-2 emotions (GG_2).png"
OUT = "cat2_states"
COLS, ROWS = 6, 5
INSET = 8
LABEL_FRAC = 0.20      # bottom fraction of each cell holds the text label
ALPHA_MIN = 30         # pixel counts as cat if alpha above this


def _bg_passable(r, g, b):
    """True if a pixel could be the painted checkerboard background: grayish &
    bright, or near-white. Flood-fill from the borders walks only these, so
    interior eye-glints/teeth (surrounded by the dark cat outline) are never
    reached and stay opaque."""
    spread = max(r, g, b) - min(r, g, b)
    avg = (r + g + b) // 3
    return (spread <= 26 and avg >= 198) or avg >= 236

NAMES = [
    "walk1", "walk2", "walk3", "walk4", "walk5", "walk6",
    "idle", "idle_blink", "sitting", "sleeping", "stretch", "alert",
    "happy", "love", "thinking", "curious", "playful", "done",
    "angry2", "hungry2", "sad2", "curious2", "playful2", "done2",
    "angry", "hungry", "sad", "scared", "surprised", "grumpy",
]


def cell_rect(img, col, row):
    W, H = img.width(), img.height()
    x0 = round(col * W / COLS) + INSET
    x1 = round((col + 1) * W / COLS) - INSET
    y0 = round(row * H / ROWS) + INSET
    y1 = round((row + 1) * H / ROWS)
    ch = y1 - y0
    y1 = y1 - int(ch * LABEL_FRAC)              # drop label strip
    return x0, y0, x1 - x0, y1 - y0


def slice_cell(img, col, row):
    x, y, w, h = cell_rect(img, col, row)
    cell = img.copy(x, y, w, h).convertToFormat(QImage.Format_ARGB32)

    # passable[y][x] = pixel looks like background (checker)
    passable = [[False] * w for _ in range(h)]
    for yy in range(h):
        for xx in range(w):
            px = cell.pixel(xx, yy)
            passable[yy][xx] = _bg_passable(qRed(px), qGreen(px), qBlue(px))

    # flood-fill from the border across passable pixels -> that's the bg
    bg = [[False] * w for _ in range(h)]
    stack = []
    for xx in range(w):
        if passable[0][xx]: stack.append((xx, 0))
        if passable[h - 1][xx]: stack.append((xx, h - 1))
    for yy in range(h):
        if passable[yy][0]: stack.append((0, yy))
        if passable[yy][w - 1]: stack.append((w - 1, yy))
    while stack:
        cx, cy = stack.pop()
        if cx < 0 or cy < 0 or cx >= w or cy >= h or bg[cy][cx] or not passable[cy][cx]:
            continue
        bg[cy][cx] = True
        stack.extend(((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)))

    minx, miny, maxx, maxy = w, h, -1, -1
    for yy in range(h):
        for xx in range(w):
            if bg[yy][xx]:
                cell.setPixel(xx, yy, qRgba(0, 0, 0, 0))   # erase background
            else:
                if xx < minx: minx = xx
                if xx > maxx: maxx = xx
                if yy < miny: miny = yy
                if yy > maxy: maxy = yy
    if maxx < 0:
        return cell
    pad = 3
    minx = max(0, minx - pad); miny = max(0, miny - pad)
    maxx = min(w - 1, maxx + pad); maxy = min(h - 1, maxy + pad)
    return cell.copy(minx, miny, maxx - minx + 1, maxy - miny + 1)


def main():
    os.makedirs(OUT, exist_ok=True)
    img = QImage(SRC).convertToFormat(QImage.Format_ARGB32)
    assert not img.isNull(), "source not found"
    i = 0
    for row in range(ROWS):
        for col in range(COLS):
            sub = slice_cell(img, col, row)
            path = os.path.join(OUT, NAMES[i] + ".png")
            sub.save(path)
            print(f"{NAMES[i]:11s} {sub.width()}x{sub.height()}")
            i += 1


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys
    QApplication(sys.argv)
    main()
