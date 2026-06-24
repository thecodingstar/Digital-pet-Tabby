# Tabby Continuous Behaviour Monitoring — design plan (build later)

**Verdict: worth doing, with caveats.** Real longitudinal data (days of actual
use) beats synthetic windows for tuning the things we can't eyeball: drive
equilibria, bond trajectory, behaviour distribution, fear frequency, offline vs
API reliance, repetition. It also catches slow regressions (the kind the 10-min
capture only hinted at). The value is real **if** the pipeline is cheap, passive,
and self-contained — otherwise it's just noise and token burn.

## Phase 0 — telemetry field contract (LOCKED — emit these now)

These event shapes are emitted by the bond-decay (Ultraplan 1b), fear (Ultraplan
4), and offline-flag (Ultraplan 1) work *today*, so the Phase-6 logger can consume
them later without a retrofit. Producers append `dict`s of this shape; the logger
(later) is the only consumer. `ts` is `int(time.time())`.

| kind     | fields                                              | emitted when |
|----------|-----------------------------------------------------|--------------|
| `bond`   | `{ts, kind:"bond", affection, delta, reason}`       | affection changes — `reason` in `pet/feed/console/quiz/decay/greet` |
| `fear`   | `{ts, kind:"fear", amount, trigger, effective}`     | `scare()` fires — `trigger` in `tool_failure/permission/error_storm/neglect/drag/activity_spike`; `effective` is post-modulation fear delta |
| `net`    | `{ts, kind:"net", online}`                          | the `online` health flag flips (true<->false) |

Until the Phase-6 logger lands, producers route these through a single no-op sink
`Cat.log_event(rec)` / `Brain` callback so wiring exists but writes nothing. That
keeps the call sites stable and the contract honest.

## Key architecture decision: log from INSIDE the mascot, not an external shell

The first monitor was an external PowerShell/python sampler in a Claude background
shell. That's the wrong shape for "run for days, every session":
- it **dies when the shell/Claude session ends** — can't span days;
- it polls a file the mascot already owns (redundant);
- it depends on a Claude session being open at all.

The cat runs on the taskbar **independently of Claude**. So the logger should live
**in the mascot process** (it already has a 30s persist cycle and a worker
thread). Then it logs whenever the cat is alive — across reboots, with or without
Claude — which is exactly the multi-day corpus we want. Claude sessions just
*read and analyze* that log, they don't produce it.

## What to log (event + heartbeat, not dense polling)

Dense 20s polling for days = huge, mostly-idle, low-signal. Instead, append to a
rotating **`cat_telemetry.jsonl`** (gitignored):
- **Events** (the signal): behaviour transitions, drive threshold crossings
  (urgent on/off), interactions (pet/feed/console/quiz answer), fear spikes +
  their trigger, mood changes, API call vs offline fallback, drag-to-move. Use the
  Phase-0 contract above for bond/fear/net.
- **Heartbeat** (the baseline): one compact drive/mood/bond snapshot every
  ~2–5 min, only if something changed since the last (log-on-change).
- Each record: `{ts, kind, ...fields}`. Bounded: rotate daily, keep ~14 days,
  cap file size. No new PII beyond what `cat_state.json` already holds.

This is a natural extension of the existing `cat_metrics.json` telemetry — same
spirit, but time-series + event-level instead of per-day aggregates.

## Analysis pipeline (cheap, occasional — NOT an LLM in the loop)

1. **`analyze_telemetry.py`** (stdlib, zero-dep, like `dashboard.py`): rolls the
   JSONL into trends — behaviour histogram + over-use (>25%) flags, drive
   equilibria + time-at-urgent, bond trajectory & decay slope, fear events/day +
   trigger breakdown, API:offline ratio, repetition. Emits a section into the
   existing `dashboard.html`.
2. **On-demand subagent review** (the only token spend): a `/analyze-cat` style
   trigger that feeds the rolled-up summary (NOT raw logs) to a cheap subagent →
   it proposes concrete tuning/hardening diffs, appended to
   `docs/IMPROVEMENT_PLAN.md`. Runs when *I* ask, ~once per session, not on a timer.
3. **Optional SessionStart hook**: on each Claude session start, run the cheap
   roll-up (no LLM) and surface a one-line "cat health" digest + any red flags, so
   regressions get noticed without me asking.

## Why this beats the naive version
- Survives for days (in-process), independent of Claude sessions.
- Cheap by default: logging is near-free; analysis is stdlib; LLM only on demand.
- Hypothesis-driven fields (tied to the exact knobs we tune: drives, bond, fear,
  offline) so the corpus answers questions instead of just growing.

## Risks / guards
- **Log growth/leak** → rotation + size cap + gitignore (it holds personal usage).
- **Idle bias** (cat mostly idle → logs mostly idle) → event-driven logging +
  log-on-change heartbeat keeps signal density up.
- **Observer effect** → logging must be try/except, off the UI thread, never able
  to crash or slow the cat (reuse the existing worker + atomic-write discipline).
- **Analysis-without-hypothesis** → the roll-up reports against named tuning
  targets, not raw dumps.

## Build order (later, after / alongside the ULTRAPLAN roadmap)
1. In-mascot telemetry logger (`cat_telemetry.jsonl`, event + heartbeat, rotation).
2. `analyze_telemetry.py` roll-up + dashboard section.
3. `/analyze-cat` on-demand cheap-subagent review → `docs/IMPROVEMENT_PLAN.md`.
4. (Optional) SessionStart health digest.

**Pairs with:** the ULTRAPLAN bond-decay + fear-trigger work — those add exactly
the signals (bond slope, fear triggers) this monitor is designed to measure, so
build the logger around the same fields. See `docs/ULTRAPLAN.md`.
