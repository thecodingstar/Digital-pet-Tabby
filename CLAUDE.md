# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repo.

## Project: Digital Pet Tabby — a living taskbar cat

Tabby is an animated pixel cat that lives on the Windows taskbar. She reacts to
Claude Code activity, runs an autonomous behaviour engine with drives when idle,
talks via an LLM, and grows a persistent personality + memory that increasingly
runs offline.

**Status:** ✓ Working — sprite mascot + autonomous brain + drives + LLM voice +
self-improving offline memory.

## Run

```bash
# auto-restarting launcher (recommended) — double-click or:
python run_mascot.py          # watches sources, restarts on edit / crash
# or just the mascot once:
python taskbar_mascot_cat.py
# or:
start_mascot.bat
```

**Interpreter note:** the machine's `python3.15.exe` is a broken uv shim. Use the
real CPython (`...\Programs\Python\Python314\python.exe`). `run_mascot.py`
auto-discovers a working PyQt5 interpreter, so prefer launching through it.

## Architecture

```
taskbar_mascot_cat.py   # PyQt5 window: renders sprites on the taskbar, owns the
                        #   game loop, speech bubble, hover info panel, right-
                        #   click menu, click-to-pet. Reactive vs brain modes.
brain.py                # Autonomous behaviour engine + drives (energy/hunger/
                        #   social/fear). Picks behaviours; drive overrides.
chatter.py              # Cat: voice (OpenAI-compatible LLM), persistent
                        #   personality + memory, and the self-improving
                        #   offline knowledge store with a daily API budget.
run_mascot.py           # Launcher: finds a real python, runs the mascot,
                        #   restarts on file change / crash, honours Quit.
cat4_slice.py           # One-off: slices source sheets -> cat4_states sprites.
cat4_states/            # 42 sprite PNGs (384x384, transparent) + _cycles GIFs
                        #   + MANIFEST.md. The art library Tabby is drawn from.
archive/                # Old experiments (statusline cat, v1/v2 mascots, etc.)
```

### Runtime state / secrets (all gitignored)

| File | What |
|------|------|
| `.env` | `GROQ_API_KEY` (+ optional `GROQ_MODEL`, `GROQ_BASE_URL`) |
| `cat_config.json` | alt config for any OpenAI-compatible provider |
| `cat_state.json` | personality: affection, traits, learned user_facts, mood |
| `cat_brain.json` | learned response memory + daily API call counts |
| `.mascot_stop` | sentinel written by the Quit menu so the watcher stops |

`cat_config.example.json` / `.env.example` are placeholders only — NEVER put a
real key in a tracked file.

## How Tabby works

### Two modes
- **react** — when Claude Code is busy, sprites/speech follow the real state
  (see `REACTIVE` map in `taskbar_mascot_cat.py`).
- **brain** — when idle, `brain.py` drives behaviour autonomously.

### State source
Reads `~/.claude/statusline/state/<session>.json` (written by
`statusline/state_writer.py`). 600s staleness cutoff. Emitted states: idle,
thinking, tool_running, subagent_running, question, tool_success, tool_failure,
permission, done, auth_success.

### Drives (brain.py)
`energy` (rest/active), `hunger` (rises; feed resets), `social` (rises when
ignored; petting lowers), `fear` (spikes on errors; consoling drops fast).
Urgent drives override behaviour: fear→cower, hunger→beg, social→seek. Mood is
derived from the drives. Behaviours map to `cat4_states` sprite names.

### Interactions
- **Left-click** = pet (consoles if scared).
- **Right-click** = menu: Feed / Pet / Sleep / Quit.
- **Hover** = info panel (what she's doing + drive bars + bond %).

### Voice + self-improving memory (chatter.py)
- LLM via any OpenAI-compatible endpoint (default Groq `llama-3.3-70b-versatile`).
  Cloudflare needs a browser `User-Agent` header (already set).
- Every API line is harvested into `cat_brain.json`, tagged with a context
  signature. Recall uses **offline hashed-bag-of-words cosine** (no deps) to pick
  the most context-similar learned line.
- `_local_prob = 0.15 + 0.6·coverage + 0.5·budget_pressure` → as memory grows the
  cat answers locally more, calling the API less.
- **Daily budget** `DAILY_BUDGET=600` (Groq free tier is 1000 RPD / 30 RPM).
- `reflect()` periodically distills observations into durable `user_facts`.
- All network runs on a background worker thread; UI never blocks.

## Conventions / gotchas
- Window stands ON the taskbar's top edge (`FOOT_OVERLAP`), forced topmost via
  `SetWindowPos(HWND_TOPMOST)`, re-asserted every ~2s.
- Sprites scaled with `Qt.FastTransformation` (nearest) to stay crisp.
- `cat4_states/*.png` are currently caught by the `*.png` gitignore rule — if a
  fresh clone needs the art, force-add them (`git add -f cat4_states/*.png`).
- Don't launch via the `python3.15.exe` uv shim; it can't spawn children.

## Source art
`cat4_states/MANIFEST.md` documents the 42-frame library (18 emotions + side
walk + mirrored walk-left + run + run-left) and the walk-cycle order. Built from
the transparent sheets in `Cat examples/Tabby2/` by `cat4_slice.py`.
