#!/usr/bin/env python3
"""Slice 'cat-2 emotions (GG_4).png' (6x5, magenta bg, 8-frame walk + emotions,
labels under each cat) into transparent per-state PNGs in cat3_states/.

Background is flat magenta in two shades (gutter ~(251,2,252), panel
~(251,91,253)). Magenta never appears on the cat, so a global chroma-key
(R&B high, G low) removes it cleanly and keeps the white eye-glints."""
import os
from PyQt5.QtGui import QImage, qAlpha, qRed, qGreen, qBlue, qRgba

SRC = "Cat examples/cat-2 emotions (GG_4).png"
OUT = "cat3_states"
COLS, ROWS = 6, 5
INSET = 8
LABEL_FRAC = 0.22      # bottom band holds the text label -> drop it
ALPHA_MIN = 30

NAMES = [
    "walk1", "walk2", "walk3", "walk4", "walk5", "walk6",
    "walk7", "walk8", "idle", "idle_blink", "sitting", "sleeping",
    "stretch", "alert", "happy", "love", "thinking", "curious",
    "playful", "done", "angry", "hungry", "sad", "scared",
    "surprised", "grumpy", None, None, None, None,
]


def _is_magenta(r, g, b):
    return r >= 170 and b >= 170 and g <= 150


def cell_rect(img, col, row):
    W, H = img.width(), img.height()
    x0 = round(col * W / COLS) + INSET
    x1 = round((col + 1) * W / COLS) - INSET
    y0 = round(row * H / ROWS) + INSET
    y1 = round((row + 1) * H / ROWS)
    y1 -= int((y1 - y0) * LABEL_FRAC)
    return x0, y0, x1 - x0, y1 - y0


def slice_cell(img, col, row):
    x, y, w, h = cell_rect(img, col, row)
    cell = img.copy(x, y, w, h).convertToFormat(QImage.Format_ARGB32)
    minx, miny, maxx, maxy = w, h, -1, -1
    for yy in range(h):
        for xx in range(w):
            px = cell.pixel(xx, yy)
            if _is_magenta(qRed(px), qGreen(px), qBlue(px)):
                cell.setPixel(xx, yy, qRgba(0, 0, 0, 0))
                continue
            if qAlpha(cell.pixel(xx, yy)) > ALPHA_MIN:
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
            name = NAMES[i] if i < len(NAMES) else None
            i += 1
            if not name:
                continue
            sub = slice_cell(img, col, row)
            sub.save(os.path.join(OUT, name + ".png"))
            print(f"{name:11s} {sub.width()}x{sub.height()}")


if __name__ == "__main__":
    from PyQt5.QtWidgets import QApplication
    import sys
    QApplication(sys.argv)
    main()
