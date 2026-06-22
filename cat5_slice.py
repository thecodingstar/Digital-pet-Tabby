#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow", "numpy", "scipy"]
# ///
"""Slice the NEW emotion sheet (Gemini_Generated_Image_7jkbv9...) into the few
states the cat4_states library was missing, normalised onto the same 384x384
transparent canvas (bottom-aligned, h-centred) as the existing sprites.

The sheet is an irregular labelled collage, so instead of a uniform grid we
segment by connected components (cats = big blobs; text/headers = small, dropped)
and pick the blob nearest each target centre. Only NEW states are taken — we skip
poses the library already has (idle/alert/curious/stretch/sit/sleep/groom/walk/run)
unless noted. Background is made transparent via cat4_slice.remove_bg.
"""
import os
import numpy as np
from PIL import Image
from scipy import ndimage
from cat4_slice import remove_bg, ALPHA_TH

SRC = "Gemini_Generated_Image_7jkbv97jkbv97jkb.png"
OUT = "cat4_states"
CANVAS = 384
BOTTOM_MARGIN = 13        # match the existing sprites' foot gap
PAD = 6
MIN_AREA, MIN_H, MIN_W = 3500, 70, 40

# NEW states -> approximate (cy, cx) centre on the 2752x1536 sheet.
TARGETS = {
    "hiss":           (504, 519),    # teeth bared, ears back (reactive snarl)
    "yawn":           (197, 2224),   # sitting wide-mouth yawn (sleepy/bored)
    "defensive_arch": (494, 180),    # spicy/aggressive stance (very scared)
    "loaf":           (1176, 1809),  # real loaf (was faked with `sitting`)
    "sleep_curled":   (1183, 2066),  # curled-up nap (alt to `sleeping`)
    "scratch":        (1155, 2586),  # leg-scratch grooming
}


def blobs(alpha):
    """Return (label_array, [(y0,y1,x0,x1,id), ...]) for cat-sized components."""
    lbl, n = ndimage.label(alpha > ALPHA_TH)
    out = []
    for i, sl in enumerate(ndimage.find_objects(lbl), 1):
        if sl is None:
            continue
        ys, xs = sl
        h, w = ys.stop - ys.start, xs.stop - xs.start
        if (lbl[sl] == i).sum() < MIN_AREA or h < MIN_H or w < MIN_W:
            continue
        out.append((ys.start, ys.stop, xs.start, xs.stop, i))
    return lbl, out


def main():
    img = Image.open(SRC).convert("RGBA")
    arr = np.asarray(img).copy()
    arr[:, :, 3] = remove_bg(arr)              # checkerboard -> transparent
    lbl, cells = blobs(arr[:, :, 3])

    for name, (ty, tx) in TARGETS.items():
        best = min(cells, key=lambda c: ((c[0] + c[1]) / 2 - ty) ** 2
                   + ((c[2] + c[3]) / 2 - tx) ** 2)
        y0, y1, x0, x1, cid = best
        dist = (((y0 + y1) / 2 - ty) ** 2 + ((x0 + x1) / 2 - tx) ** 2) ** 0.5
        if dist > 140:
            print(f"  !! {name}: no blob near target ({dist:.0f}px) — skipped")
            continue
        # isolate THIS cat: zero alpha for any other component (kills header/label
        # text or a neighbour cat that falls inside the crop rectangle).
        bx0, by0 = max(0, x0 - PAD), max(0, y0 - PAD)
        bx1, by1 = min(arr.shape[1], x1 + PAD), min(arr.shape[0], y1 + PAD)
        sub = arr[by0:by1, bx0:bx1].copy()
        sub[:, :, 3] = np.where(lbl[by0:by1, bx0:bx1] == cid, sub[:, :, 3], 0)
        ys, xs = np.where(sub[:, :, 3] > ALPHA_TH)      # tight-crop the isolated cat
        cat = Image.fromarray(sub[ys.min():ys.max() + 1, xs.min():xs.max() + 1], "RGBA")
        # fit within the canvas (keep nearest-pixel crispness like the renderer)
        maxw, maxh = CANVAS, CANVAS - BOTTOM_MARGIN
        if cat.width > maxw or cat.height > maxh:
            s = min(maxw / cat.width, maxh / cat.height)
            cat = cat.resize((round(cat.width * s), round(cat.height * s)), Image.NEAREST)
        canvas = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
        canvas.alpha_composite(cat, ((CANVAS - cat.width) // 2,
                                     CANVAS - BOTTOM_MARGIN - cat.height))
        os.makedirs(OUT, exist_ok=True)
        canvas.save(os.path.join(OUT, name + ".png"))
        print(f"  {name:16s} {cat.width}x{cat.height} -> {CANVAS}x{CANVAS}")


if __name__ == "__main__":
    main()
