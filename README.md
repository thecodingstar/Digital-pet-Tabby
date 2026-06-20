# Digital Pet Tabby

An animated pixel-cat mascot that perches on the **Windows taskbar** and reacts
to [Claude Code](https://claude.com/claude-code) activity — walking while tools
run, blinking/stretching/sleeping when idle, celebrating successes and sulking
at failures.

![states](preview_states.png) <!-- local only; not in repo -->

## Features

- **Sits on the taskbar.** Frameless, always-on-top, transparent window whose
  feet rest on the taskbar's top edge (Windows 11's taskbar repaints over
  anything placed *inside* it, so the cat sits just above).
- **Walks across the bar.** During work states it strolls left/right, turns at
  the edges, and faces its direction of travel.
- **Six switchable characters** (right-click the cat):
  - `black`, `tabby`, `ginger`, `cream` — hand-drawn 16×16 pixel cats (`cats.py`)
  - `tabby2`, `tabby3` — image-sprite cats sliced from generated sprite sheets
- **Full emotion set + personality:** idle / blink / stretch / sit / sleep,
  thinking, working (walk cycle), success (happy), failure (sad → **angry** on a
  fail-streak), **grumpy** when woken, **hungry** at high context use, heart-eyes
  on a big finish, random idle fidgets.

## Run

```bash
pip install PyQt5
python mascot.py                 # last-used character (saved)
python mascot.py --cat tabby3    # force a character
pythonw mascot.py                # no console window
```

Double-click the cat to quit, right-click for the character menu, left-drag to
move it.

## How it reads Claude Code state

`statusline/state_writer.py` is registered as a Claude Code hook; it writes
per-session JSON to `~/.claude/statusline/state/<session>.json`. The mascot
polls the newest state file and maps the activity to an emotion.

## Layout

| File | Role |
|------|------|
| `mascot.py` | The taskbar window: placement, walking, state polling, mood engine, character switcher |
| `cats.py` | Hand-drawn character data (palettes, bodies, faces, walk legs, state→face map) |
| `cat2_slice.py`, `cat3_slice.py` | Slice generated sprite sheets into per-state PNGs |
| `design_cat.py`, `preview_cats.py` | Sprite design / preview harnesses |
| `statusline/` | Terminal statusline + the `state_writer.py` hook + `mascot_pack.json` |
| `cat2_emotions_prompt_v*.txt` | Image-gen prompts used to produce the sprite sheets |

## Images are not in the repo

Reference images, generated sprite sheets, and the sliced sprite folders
(`cat2_states/`, `cat3_states/`) are git-ignored. The four hand-drawn pixel
cats work with no assets. To rebuild the image cats, drop a sprite sheet in the
project and run the matching slicer (`python cat3_slice.py`).
