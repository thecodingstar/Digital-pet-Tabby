# BRAIN.md — How Tabby's brain & memory work

Tabby has **two brains** and **persistence**. One brain decides what she *does*
(`brain.py`); the other decides what she *says*, remembers, and learns
(`chatter.py`). Both adapt over time. This doc is the faithful reference.

```
                ┌───────────────────────────── taskbar_mascot_cat.py ─────────────────────────────┐
                │  game loop (90ms): read Claude state, pick mode, animate, render, handle input    │
                └───────────┬───────────────────────────────────────────────┬─────────────────────┘
          reactive (Claude busy)                                   autonomous (idle)
                            │                                                 │
                            ▼                                                 ▼
                   REACTIVE map (state→clip)                          brain.py  (drives + behaviour)
                            │                                                 │
                            └──────────────► events (pet/feed/claude_*/musing/…) ──────────────┐
                                                                                                ▼
                                                                                  chatter.py  (voice + memory + learning)
                                                       ┌────────────────────────────────────────┼───────────────────────┐
                                                       ▼                                          ▼                       ▼
                                       structured recall (offline)                       LLM (OpenAI-compatible)   reflection
                                       cat_brain.json (reward-scored)                    Groq/NVIDIA/…             → confidence facts
                                                       └──────────────► cat_metrics.json (telemetry) ◄────────────┘
```

## What Tabby does (at a glance)

- **Reacts to Claude Code** — runs while tools run, follows subagents, cheers on
  success, gets startled by errors, perks up at questions, looks proud at end.
- **Lives on her own when idle** — wanders, sits, naps, zoomies, plays, grooms,
  curious/grumpy — chosen by her drives, not scripted.
- **Has needs you tend** — hungry (begs), lonely (seeks you), tired (sleeps),
  scared (cowers). **Left-click** = pet (consoles when scared); **right-click** =
  Feed/Pet/Sleep/Quit; **hover** = status panel (doing + drive bars + bond %).
- **Talks** — short cat-like lines in a bubble.
- **Learns you** — affection grows; she forms confidence-weighted impressions;
  behaviour adapts to what earns your attention; she leans on the API less over time.

---

## 1. Behaviour brain — `brain.py`

A **homeostatic drive model** with learning on top.

### Drives
| drive  | start | drift/s            | urgent ≥ | high → | reset by |
|--------|-------|--------------------|----------|--------|----------|
| energy | 70    | per-behaviour      | —        | sleep  | resting |
| hunger | 20    | +0.12 (+more on exertion) | 78 | beg | feed (−85) |
| social | 20    | +0.08              | 72       | seek   | pet (−30) |
| fear   | 0     | −0.6 (decays)      | 60       | cower  | console (−55) |

### Mood inertia (B1)
`valence` (pleasant↔unpleasant) and `arousal` (calm↔excited) are EWMA-smoothed
(rate 0.1) from the drives. Urgent drives name the mood directly
(scared/hungry/lonely); otherwise mood comes from smoothed valence/arousal
(sleepy / playful / content) so labels don't flicker on a threshold.

### Behaviour selection (per tick)
1. Update drives; **fear spike interrupts** → `cower`.
2. At a behaviour's end, choose next:
   - **Urgent drive wins** (fear > hunger > social). While scared, hunger/social
     urgency thresholds are **raised +20** so fear takes priority (B3).
   - Else weighted-random:
     `weight = base · _factor(energy, active_hour) · (0.7 + 0.6·affinity) · recent_penalty`
     - `_factor` biases by energy and by **user activity hour** (B5): naps cluster
       in your quiet hours, livelier when you're usually around.
     - `affinity` (B2): learned per-behaviour preference (below).
     - `recent_penalty` (B6): 0.15 just-used, decays to 1.0 over ~30s (history of last 4).
3. Scripted wake-up: `sleep → stretch → sit`. Finishing groom/sleep/stretch gives a
   small **contentment** valence bump (B3).

### Learning & lifelike state
- **Behaviour reinforcement (B2):** `affinity` per behaviour (EWMA 0.20, bounded
  [0.2, 1.0]). Pet/feed while doing X raises X's affinity; a scare lowers the
  current one. Bounds keep innate traits dominant — no behaviour monoculture.
- **Trust + sensitization (B4):** `trust` (init 0.30) dampens fear spikes
  `effective_scare = amount·(1−0.5·trust)·(1+jumpiness)`. Consoling raises trust;
  un-consoled scares erode it. `jumpiness` rises +0.25/scare (error storms make her
  progressively jumpy) and decays ~0.03/s.
- **User rhythm (B5):** a 24-bucket `active_hours` histogram, incremented on every
  interaction, drives the activity-hour bias above.

### Interactions (from the mascot)
`feed()` hunger−85 + reinforce + note-active; `receive_pet()` social−30, consoles
if fear>35 (fear−55, trust+0.1) else fear−8, + reinforce; `scare(amount)` applies
trust/jumpiness then cowers; `force_sleep()` caps energy at 25.

---

## 2. Memory / voice brain — `chatter.py`

Events: `pet, fed, consoled, claude_success/failure/question/done,
wants_attention, hungry, scared, musing, wake, sleep, greet`. Each is tagged with
context (`drives()` + `behavior`) and turned into a line.

### Self-improving loop (the core)
For each event:
```
ctx_coverage = neighbours_for_event(struct_sim ≥ 0.60) / COVER_CAP     # M3
local_prob   = clamp(0.10 + 0.55·ctx_coverage + 0.45·budget_pressure, 0, 0.95)
```
`random() < local_prob` → answer **from memory** (free, offline). Else → call the
LLM **and learn the reply** — but coverage is **context-aware (M3)**, so an API
call is spent to *fill the current gap*, not pile near-dups onto common contexts.
API use trends toward zero as the space fills.

### Structured recall (M1) — flagged `RECALL_MODE = "structured" | "vector"`
Context is a typed dict: `{event, mood, behavior, energy_b, hunger_b, fear_b,
affection_tier, daypart, claude_streak}`. Similarity = weighted field match
(`FIELD_WEIGHTS`; ordinal buckets score 1.0 equal / 0.5 adjacent / 0.0 opposite),
normalized 0..1. Interpretable, collision-free, zero-dep. Legacy hashed-cosine
("vector") retained as fallback. Event is always a hard filter.

### Reward-weighted recall (M2)
Each line carries `{reward, uses, last_used}`. The mascot reports outcomes within
the interaction (pet/feed/console → 1.0, scare → 0.0); reward is EWMA (α=0.25).
```
recall_weight = (sim + 0.05)³ · (0.5 + reward) · anti_repeat · cross_mult
```
- **anti_repeat (M5):** ×0.15 for lines in a session ring of the last 8 served.
- **cross_mult (M7):** when the context is thin (< MIN_KEEP neighbours), borrow
  lines from compatible events (`COMPAT_EVENTS`) at ×0.5.
Sample from the top half, weighted. So she serves lines that *land* and *fit*.

### Eviction (M4)
Over `LINE_CAP=24`, drop the worst by
`keep = 0.5·reward + 0.3·diversity + 0.2·recency` (diversity = mean text-dissimilarity),
never below `MIN_KEEP=6`. Keeps high-performing, distinct lines.

### Voice-coherent prompts (M6)
LLM calls include her top-reward lines for the event as few-shot "her voice",
current facts, the context, and the MAX_LINE/ASCII constraints — so new lines stay
on-voice and pass the quality gate.

### Reflection → confidence facts (R1/R2)
Every ~8 interactions one LLM call distils recent observations into a
`category|fact`. Facts are **records** `{text, category, evidence, confidence,
last_seen}` over categories `tools/schedule/temperament/style`. Re-observation
raises evidence + confidence (EWMA 0.30); confidence **decays with age** and facts
**drop below 0.20**; capped 3/category; near-dups merged. The prompt uses the
top-confidence facts. Reflection is skipped under budget pressure (R2).

### Quality guardrails
- **`_clean_line`** — printable-ASCII only (kills mojibake/emoji), single line,
  ≤ `MAX_LINE=46` at a word boundary, must contain letters.
- **Dedup** (text cosine > 0.8) on store + load.
- **Self-heal on load** — re-clean + de-dup + migrate fields.
- **UTF-8 everywhere**, **atomic writes** (temp + `os.replace`).
- **Daily budget** `DAILY_BUDGET=600` (Groq free = 1000), reflection counts too.
- **Canned fallback** when no key / offline. **Background worker** for all network
  (RLock: build prompt under lock, network outside, store under lock).

### Telemetry (P2) — `cat_metrics.json`
Per day: served, api, reflection, local_hits, local_hit_rate, avg_served_sim,
mean_served_reward, repeat_rate. Atomic, worker-thread only. The sim harness reads
the same metrics.

---

## 3. Persistence (separate files, schema-versioned)

| file | holds | churn | gitignored |
|------|-------|-------|------------|
| `cat_state.json` | personality: affection, traits, **confidence facts**, mood, interactions, persisted **drives + valence/arousal + affinity + trust + jumpiness + active_hours**, `schema_version` | slow | yes |
| `cat_brain.json` | learned `lines` (line, ctx, **cstruct, reward, uses, last_used**), daily `calls`, `schema_version` | fast | yes |
| `cat_metrics.json` | daily telemetry | fast | yes |
| `.env` / `cat_config.json` | API key / base_url / model | rare | yes |

`schema_version=2`; `migrate` on load adds new fields with safe defaults and never
loses data. Drives + learning persist every 30s and on quit.

---

## 4. Verifying changes — `sim_harness.py` (M0)

```
python sim_harness.py [N]      # default 1000, offline, LLM stubbed, no budget
```
Streams synthetic events, prints a before/after table for `RECALL_MODE`
vector vs structured: local_hit_rate, early/late hit (learning curve),
avg_served_sim, reward, repeat_rate, api_calls, lines. Run it before/after any
memory change; **don't regress**.

Current baseline (1000 events): structured beats vector — `local_hit_rate`
~0.22 vs ~0.16, `avg_served_sim` ~0.64 vs ~0.57, `repeat_rate` ≈ 0, fewer API calls,
rising learning curve, no drive runaway over a 30-min behaviour sim.

---

## 5. Tunables

| constant | file | meaning |
|----------|------|---------|
| `RECALL_MODE` | chatter.py | structured (default) \| vector (legacy) |
| `DAILY_BUDGET` 600 | chatter.py | max API calls/day |
| `LINE_CAP` 24 / `MIN_KEEP` 6 | chatter.py | per-event cap / eviction floor |
| `COVER_CAP` 16 / `COVER_SIM_MIN` 0.60 | chatter.py | coverage neighbours / threshold |
| `REWARD_ALPHA` 0.25 | chatter.py | line reward EWMA |
| `ANTIREPEAT_K` 8 | chatter.py | session anti-repeat ring |
| `FIELD_WEIGHTS` | chatter.py | structured similarity weights |
| `FACT_*`, `FACT_CATS` | chatter.py | fact confidence/cap/categories |
| `REFLECT_BUDGET_PCT` 0.10 | chatter.py | reflection budget reserve |
| `CROSS_EVENT`, `COMPAT_EVENTS` | chatter.py | cross-event recall |
| `DRIFT`, `URGENT` | brain.py | drive drift / urgency thresholds |
| `AFFINITY_BOUNDS/ALPHA` | brain.py | behaviour-learning clamp / rate |
| `BEHAV_HISTORY_N` 4 | brain.py | recent-behaviour decay window |
| `TRUST_INIT` 0.30 | brain.py | starting trust |
| `BEHAVIORS` | brain.py | per-behaviour frames/period/speed/dur/energy |

---

## One-line summary
**Drives (smoothed into mood) + event form a typed context → context recalls the
best-fitting, best-performing learned line or spends a budgeted API call to fill a
gap → outcomes score lines, reflection distils confidence facts, behaviour affinity
and trust adapt to you → everything persists.** She behaves autonomously, speaks in
context, learns you, and needs the cloud less the longer she lives.
