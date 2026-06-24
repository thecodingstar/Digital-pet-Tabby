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
- **Status:** proposed.
