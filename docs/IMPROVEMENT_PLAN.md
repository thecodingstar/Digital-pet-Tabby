# Tabby Improvement Log

A running, tracked log of concrete tuning/hardening proposals for Tabby. The
Phase-6 monitoring review loop (`/analyze-cat`, see `docs/MONITORING.md`) appends
data-driven suggestions here from rolled-up telemetry; humans triage them into the
roadmap (`docs/ULTRAPLAN.md`).

Each entry: a date, the signal that prompted it, and a proposed change.

## Format

```
### YYYY-MM-DD — <short title>
- **Signal:** what the telemetry roll-up showed (e.g. "fear events 9/day, 70% from
  permission prompts; bond slope -2.1/day during a 5-day gap").
- **Proposal:** the concrete tuning/code change.
- **Status:** proposed | accepted | done | rejected.
```

## Entries

### 2026-06-24 — `sit` over-use, amplified by the beg-reprieve
- **Signal:** first live roll-up (`analyze_telemetry.py`, ~14 min, 205 events):
  `sit` 29% of behaviour transitions (over the 25% flag) and `beg` 20% — together
  half of all transitions. `sit` and `beg` counts rose in lockstep as hunger
  climbed (sit 12->26->52, beg 0->10->37), because the Phase-1b hunger reprieve
  returns `sit` as the pause between begs. So a hunger episode makes every other
  behaviour `sit`. Drives healthy (energy 49->82, hunger 19->50 unfed, social
  7->31); persisted mood varied (sleepy/content/hungry); the new `permission`
  fear trigger fired once (works live); no bond/net events (expected: no pets,
  10h decay grace, API online).
- **Proposal:** (a) use a dedicated short pause sprite (`loaf`/`idle`) instead of
  `sit` for the urgent reprieve in `brain._choose`, so begging doesn't inflate
  `sit`; (b) down-weight `sit` in `_choose` since it's also the catch-all
  fallback. Either should drop `sit` below the 25% flag and de-couple it from
  `beg`. Re-measure with a fresh roll-up after the change.
- **Status:** done (2026-06-24) — both parts applied: (a) `_choose` uses `idle`
  (not `sit`) for the hunger/social reprieve; (b) `sit` base weight lowered 4->3
  (to match `idle`) so it's no longer the single most-weighted idle pose, while
  staying a natural resting behaviour. Re-measure on the next multi-hour run to
  confirm `sit` drops below the 25% flag.

### 2026-06-25 — hunger too needy (begs ~5-6x/hr)
- **Signal:** live in-mascot sampling (drives + behaviour-count deltas, ~40 min
  across two `beg` cycles) after the v5 merge. Hunger drift was 0.12/s, so
  0->urgent(78) took ~11 min: she hit urgent `beg` ~5-6x/hour. Each deep starve
  (`hunger > 85`) floored energy to 0 via the v5 hunger->energy coupling, then
  snapped back on feed. v5 itself verified healthy: tiredness/sleep/energy loop
  cycles, urgent-beg idle<->beg alternation works, `sit` no longer runaway.
- **Proposal:** lower hunger drift `0.12 -> 0.04/s` (0->urgent ~32 min, ~2
  feeds/hr) and the play/zoomies exertion burn `0.2 -> 0.06/s` to keep the ratio.
- **Status:** done (2026-06-25, commit 46598e4) — applied; live slope re-measured
  at 0.048/s (base + light exertion), projecting ~27-32 min/cycle ≈ ~2x/hr.

### 2026-06-25 — social neglect had no easy relief prompt
- **Signal:** in the same run, after feeds resolved hunger she drifted to mood
  `lonely` with social 80->89 and `seek` dominant — social climbs unchecked just
  like hunger did, but the only relief paths (left-click pet / right-click menu)
  give no visible cue that she needs it.
- **Proposal:** add an always-available-but-need-gated quick-action bar above the
  cat: surface **Feed** when `hunger > 60`, **Pet** when `social > 60` or
  `fear > 30`; hide otherwise (and during quiz cards). Bigger icon+label pills so
  they're easy to hit. Keep the right-click menu + left-click pet as fallbacks.
- **Status:** done (2026-06-25, commits 46598e4 / aa60dd5) — `ActionBar` widget
  added, need-gated. Re-watch a multi-hour run to confirm the Pet prompt actually
  catches loneliness episodes before she reaches the `seek`/`lonely` state.

### 2026-06-25 — quiz answers barely train the brain (untagged API path)
- **Signal:** traced the quiz pipeline against live data (13 answered questions,
  all `api_` ids). `_llm_question` builds options as bare `{"label": text}` — no
  `traits`/`prefs`/`fact` — so answering an online question stored **empty prefs**,
  **never nudged a trait**, and only crystallized a generic "human likes X" fact.
  `behavior_hints()` keyword-recovered 3 prefs (comfort_style/chronotype/pace), but
  the brain consumes only **comfort_style**; chronotype/pace are surfaced and
  ignored. Net: of everything the quiz collected, exactly one signal reaches
  behaviour. The rich 211-q tagged library (the path designed to feed the brain)
  is bypassed whenever online. Quiz was decorating her *voice* (system prompt
  lists stated likes) far more than training her *brain*.
- **Proposal:** (a) **tag API answers** — on answer, run answer/question text
  through keyword inference (`_infer_tags`) to recover a pref bag + complementary
  trait nudges, so an online answer trains traits like a library answer does
  (traits derived from the ANSWER only, to avoid the question naming both poles);
  (b) follow-up: wire more of `PREF_KEYS` into the brain (chronotype -> active_hours
  seed, pace -> musing/zoomies cadence) so >1 pref actually matters.
- **Status:** (a) done (2026-06-25) — `_infer_tags` added + wired into
  `answer_question`; verified: "chatty"->playfulness, "fast sprints"->curiosity,
  "cat person"->sass all nudge +0.08 with no question-wording bleed. (b) proposed,
  tracked as an ULTRAPLAN goal.
