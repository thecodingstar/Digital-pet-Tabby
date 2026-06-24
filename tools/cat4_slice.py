#!/usr/bin/env python3
# /// script
# requires-python = ">=3.9"
# dependencies = ["pillow", "numpy", "scipy"]
# ///
"""Slice a labeled cat sprite sheet (baked light-gray checkerboard bg) into
per-state transparent PNGs.

The sheets are RGBA but fully opaque; the "transparent" look is a baked
light-gray checkerboard. So we:
  1. Build a bg mask = low-saturation & bright (both checkerboard shades, plus
     the slightly blue-gray bg of the directional sheet).
  2. Flood-fill that mask from the sheet border (binary reconstruction) so only
     the *connected exterior* bg is removed -- interior white eye-glints survive.
  3. Clean a 2px soft-gray halo at cat edges so no fringe remains.
  4. For each grid cell, drop the bottom text label at the transparent gap under
     the cat, then tight-crop the cat by its alpha bbox.
"""
import os
import sys
import numpy as np
from pathlib import Path
from PIL import Image
from scipy import ndimage

# Source sheets + the sprite library live at the repo root; this slicer now
# lives in tools/, so resolve those paths relative to the repo root (parent of
# tools/) rather than the current working directory.
ROOT = Path(__file__).resolve().parent.parent

ALPHA_TH = 24
ROW_MIN_PIX = 6
PAD = 4


def remove_bg(arr):
    """Return a new alpha channel with connected exterior gray bg set to 0."""
    r, g, b = arr[:, :, 0].astype(int), arr[:, :, 1].astype(int), arr[:, :, 2].astype(int)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    spread = mx - mn
    bright = mn
    bg_like = (spread < 30) & (bright > 150)        # checkerboard grays
    # seed from the 1px border
    seed = np.zeros_like(bg_like)
    seed[0, :] = seed[-1, :] = seed[:, 0] = seed[:, -1] = True
    seed &= bg_like
    exterior = ndimage.binary_propagation(seed, mask=bg_like)
    alpha = np.full(arr.shape[:2], 255, np.uint8)
    alpha[exterior] = 0
    # halo cleanup: soft-gray pixels touching transparency -> clear (2 passes)
    soft = (spread < 45) & (bright > 165)
    for _ in range(2):
        trans = alpha == 0
        neigh = ndimage.binary_dilation(trans)
        kill = neigh & soft & (alpha > 0)
        alpha[kill] = 0
    return alpha


def find_cat_band(alpha, x0, x1, y0, y1, strip_top=False):
    """Return (cat_top, cat_bot) in absolute rows, excluding the bottom text
    label. The label is the lowest ink block; we cut at the transparent gap
    directly above it. Emotion marks above the cat (hearts/?/!) are kept.

    strip_top: also drop a short text block bleeding in at the very top (the
    previous grid row's label) -- used for the tightly-packed directional sheet."""
    sub = alpha[y0:y1, x0:x1]
    rowpix = (sub > ALPHA_TH).sum(axis=1)
    content = rowpix > ROW_MIN_PIX
    if not content.any():
        return None
    rows = np.where(content)[0]
    top, h = rows[0], len(content)
    if strip_top:
        # the previous grid row's label (possibly TWO text lines) bleeds in at
        # the top. Repeatedly drop short top blocks followed by a gap until we
        # reach the tall cat block.
        while True:
            bb = top
            while bb < h and content[bb]:
                bb += 1
            gg = bb
            while gg < h and not content[gg]:
                gg += 1
            if (bb - top) < 55 and (gg - bb) >= 4 and gg < h:
                top = gg
            else:
                break
    GAP = 4
    # walk up from the bottom: skip empty margin, consume the label block,
    # then the gap above it marks the cat's bottom.
    rr = h - 1
    while rr > top and not content[rr]:
        rr -= 1
    label_bottom = rr
    while rr > top and content[rr]:
        rr -= 1
    label_top = rr + 1
    # confirm a real gap separates this block (it's a label, not the cat body)
    gap = 0
    while rr > top and not content[rr]:
        rr -= 1
        gap += 1
    label_h = label_bottom - label_top + 1
    if gap >= GAP and label_h < 0.33 * h and rr > top:
        cat_bot = rr + 1          # last cat row (top of the gap)
    else:
        cat_bot = label_bottom    # no separable label; keep full content
    return y0 + top, y0 + cat_bot + 1


def slice_sheet(src, out_dir, cols, rows, names, inset=6, strip_top=False):
    img = Image.open(src).convert("RGBA")
    W, H = img.size
    arr = np.asarray(img).copy()
    arr[:, :, 3] = remove_bg(arr)
    img = Image.fromarray(arr, "RGBA")
    alpha = arr[:, :, 3]
    os.makedirs(out_dir, exist_ok=True)
    i = 0
    written = []
    for r in range(rows):
        for c in range(cols):
            name = names[i] if i < len(names) else None
            i += 1
            if not name:
                continue
            cx0 = round(c * W / cols) + inset
            cx1 = round((c + 1) * W / cols) - inset
            cy0 = round(r * H / rows) + inset
            cy1 = round((r + 1) * H / rows) - inset
            band = find_cat_band(alpha, cx0, cx1, cy0, cy1, strip_top)
            if band is None:
                print(f"  !! {name}: empty cell")
                continue
            ty0, ty1 = band
            sub_alpha = alpha[ty0:ty1, cx0:cx1]
            ys, xs = np.where(sub_alpha > ALPHA_TH)
            if len(xs) == 0:
                print(f"  !! {name}: empty band")
                continue
            ax0 = cx0 + max(0, xs.min() - PAD)
            ax1 = cx0 + min(cx1 - cx0, xs.max() + 1 + PAD)
            ay0 = ty0 + max(0, ys.min() - PAD)
            ay1 = ty0 + min(ty1 - ty0, ys.max() + 1 + PAD)
            crop = img.crop((ax0, ay0, ax1, ay1))
            crop.save(os.path.join(out_dir, name + ".png"))
            written.append((name, crop.width, crop.height))
            print(f"  {name:16s} {crop.width}x{crop.height}")
    return written


GG2_NAMES = [
    "walk-1", "walk-2", "walk-3", "walk-4", "walk-5", "walk-6",
    "idle", "idle_blink", "sitting", "sleeping", "stretch", "alert",
    "happy", "love", "thinking", "curious_a", "playful_a", "proud_a",
    "angry_a", "hungry_a", "sad_a", "curious_b", "playful_b", "proud_b",
    "angry_b", "hungry_b", "sad_b", "scared", "surprised", "grumpy",
]

# ---- eei3tw directional sheet: 10 cols x 10 rows ------------------------
# only the two contiguous run cycles (row2=run right, row3=run left, cols0-5)
EEI_NAMES = [None] * 100
for _c in range(6):
    EEI_NAMES[2 * 10 + _c] = f"run-{_c + 1}"        # row 2
    EEI_NAMES[3 * 10 + _c] = f"run-left-{_c + 1}"   # row 3

if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "gg2"
    if which == "gg2":
        slice_sheet(str(ROOT / "Cat examples/Tabby2/cat-2 emotions (GG_2).png"),
                    str(ROOT / "cat4_states"), 6, 5, GG2_NAMES)
    elif which == "eei":
        slice_sheet(str(ROOT / "Cat examples/Tabby2/Gemini_Generated_Image_eei3tweei3tweei3.png"),
                    str(ROOT / "cat4_eei_raw"), 10, 10, EEI_NAMES, inset=4, strip_top=True)
