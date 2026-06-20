#!/usr/bin/env python3
"""Render all 4 cats across key states -> preview_cats.png"""
from PyQt5.QtWidgets import QApplication
from PyQt5.QtGui import QImage, QPainter, QColor
import sys, cats

STATES = ["idle", "thinking", "tool_running", "tool_success",
          "tool_failure", "done", "permission", "question"]


def hexrgb(c):
    return QColor(int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


def render(path="preview_cats.png", scale=16):
    app = QApplication(sys.argv)
    cell = 16 * scale
    pad = 10
    cols, rows = len(STATES), len(cats.CHARACTERS)
    W = pad + cols * (cell + pad)
    H = pad + 24 + rows * (cell + pad)
    img = QImage(W, H, QImage.Format_ARGB32)
    img.fill(QColor("#202830"))
    p = QPainter(img)
    p.setPen(QColor("#cdd6e0"))
    for ci, st in enumerate(STATES):
        p.drawText(pad + ci * (cell + pad) + 4, 16, st)
    for ri, name in enumerate(cats.CHARACTERS):
        pal = cats.PALETTES[name]
        for ci, st in enumerate(STATES):
            ox = pad + ci * (cell + pad)
            oy = 24 + pad + ri * (cell + pad)
            grid = cats.compose(name, st, leg_phase=ci % 2)
            for y, row in enumerate(grid):
                for x, ch in enumerate(row):
                    hexc = pal.get(ch)
                    if hexc:
                        p.fillRect(ox + x * scale, oy + y * scale, scale, scale, hexrgb(hexc))
            for (bx, by, bc) in cats.BADGES.get(st, ()):
                hexc = cats.BADGE_COLORS.get(bc)
                if hexc:
                    p.fillRect(ox + bx * scale, oy + by * scale, scale, scale, hexrgb(hexc))
    p.end()
    img.save(path)
    print("wrote", path)


if __name__ == "__main__":
    render()
