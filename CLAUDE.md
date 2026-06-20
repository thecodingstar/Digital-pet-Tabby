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
chatter.py              # Cat: voice (OpenAI-compatible LLM), personality, and a
                        #   self-improving memory — structured reward-scored recall,
                        #   confidence facts, telemetry, daily API budget.
run_mascot.py           # Launcher: finds a real python, runs the mascot,
                        #   restarts on file change / crash, honours Quit.
sim_harness.py          # Offline sim (stubbed LLM) for measuring memory changes.
cat4_slice.py           # One-off: slices source sheets -> cat4_states sprites.
cat4_states/            # 42 sprite PNGs (384x384, transparent) + _cycles GIFs
                        #   + MANIFEST.md. The art library Tabby is drawn from.
archive/                # Old experiments (statusline cat, v1/v2 mascots, etc.)
```

**Full brain/memory design is documented in `BRAIN.md`** — read it before
changing `brain.py` / `chatter.py`. Verify memory changes with `sim_harness.py`.

### Runtime state / secrets (all gitignored)

| File | What |
|------|------|
| `.env` | `GROQ_API_KEY` (+ optional `GROQ_MODEL`, `GROQ_BASE_URL`) |
| `cat_config.json` | alt config for any OpenAI-compatible provider |
| `cat_state.json` | personality: affection, traits, confidence facts, mood, persisted drives + affinity/trust/active_hours, `schema_version` |
| `cat_brain.json` | learned lines (reward/uses/ctx) + daily API counts, `schema_version` |
| `cat_metrics.json` | per-day telemetry (local_hit_rate, served_sim, reward, …) |
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

### Drives + behaviour learning (brain.py)
`energy/hunger/social/fear` drift over time; urgent drives override behaviour
(fear→cower, hunger→beg, social→seek). On top of that: **mood inertia** (smoothed
valence/arousal), **behaviour affinity** (learns which behaviours earn your
attention), **trust + jumpiness** (consoling calms, error-storms sensitize), and a
**user-activity rhythm** (naps cluster in your quiet hours). All persist. See BRAIN.md.

### Interactions
- **Left-click** = pet (consoles if scared).
- **Right-click** = menu: Feed / Pet / Sleep / Quit.
- **Hover** = info panel (what she's doing + drive bars + bond %).

### Voice + self-improving memory (chatter.py)  — see BRAIN.md for full detail
- LLM via any OpenAI-compatible endpoint (default Groq `llama-3.3-70b-versatile`);
  needs a browser `User-Agent` header (set).
- **Structured context recall** (`RECALL_MODE`, default `structured`; `vector`
  legacy fallback): typed ctx + weighted field-match similarity, zero deps.
- **Reward-scored lines** — outcomes (pet/feed=good, scare=bad) EWMA each line;
  recall weights `(sim)³·(0.5+reward)·anti_repeat·cross_event`. Quality eviction.
- **Context-aware coverage** drives `local_prob` → API calls fill gaps, not pile dups.
- **Confidence facts** (categories, evidence, decay) from reflection.
- **Daily budget** 600; **telemetry** → `cat_metrics.json`. All network on a
  worker thread (RLock: prompt under lock, network outside, store under lock).
- Guardrails: ASCII-only `_clean_line`, dedup, UTF-8 + atomic writes, canned
  fallback, schema-versioned migration + self-heal on load.

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
