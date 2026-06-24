# Tabby Ultraplan v2 — offline hardening · bond decay · drag-to-move · 200+ tagged questions · fear · file org · monitoring

The roadmap for taking Tabby further. Each phase is independently testable;
behaviour phases are verified with `sim_harness.py`, the GUI by launching
`run_mascot.py`. Phase 6 (monitoring) is documented in `docs/MONITORING.md` and
built last.

## Goals
- **More self-sufficient offline** (no API key / network down).
- **More tactile** — drag her along the taskbar; she resumes roaming.
- **Deeper understanding of the human** — a large *tagged* question bank, and a
  bond that can cool when neglected (not monotonic).
- **More emotionally reactive** — fear responds to more than `tool_failure`.
- **Tidy repo + groundwork for longitudinal monitoring.**

## Status legend: ☐ todo · ◐ in progress · ☑ done

---

## Phase 0 — Telemetry-field contract  ☑
Lock the `bond`/`fear`/`net` event field names (see `docs/MONITORING.md`) so the
bond-decay and fear work emit them from day one. Producers route through a no-op
`Cat.log_event` sink until the Phase-6 logger lands.

## Phase 1 — Offline-first hardening
1. **`online` health flag** centralized at `chatter._post_chat` (the single network
   choke point): N consecutive failures (`NET_FAIL_MAX`) flip `online=False` + set a
   re-probe time; success resets. `_llm`/`_llm_question`/`reflect` gate on it but
   allow one re-probe after the cooldown. Keep per-caller try/except as backstop.
2. **Offline understanding** — the tagged library (Phase 3) applies traits+prefs+fact
   on every answer, so learning continues with no API.
3. **Telemetry** — `offline_sessions` + `local_question_rate` into `cat_metrics.json`;
   emit `net` events.

### 1b. Bond decay on neglect
Affection is currently monotonic. Add a **`last_attention`** timestamp set only on
genuine attention (pet/feed/console/quiz answer — never in `save()`). Wall-clock
decay on load + persist tick (mirrors `Brain.note_activity` decay): after `GRACE_H`,
lose `DECAY_PER_DAY` scaled by neglect, **loyalty-damped** (higher tiers cool
slower) and floored at `AFF_FLOOR` (≥ the quiz gate of 25 so neglect can't disable
the quiz). Surface in hover card + dashboard; emit `bond` events.

## Phase 2 — Drag to reposition (resume roaming)
Drag horizontally along the taskbar; release resumes free-wander from the new spot.
6px deadzone disambiguates drag from click (pet logic moves to release). Gated off
while a quiz card is up; clamps to `[left_bound, right_bound]`; re-asserts topmost.
Position is not persisted (she roams anyway). A fast drag feeds Phase 4 startle.

## Phase 3 — 200+ tagged question library + offline personality engine
- **3a schema:** each option carries `traits` (the 4 personality dims), `prefs`
  (structured facts about the human — extended set: chattiness, comfort_style,
  chronotype, pace, social_energy, humor, risk, structure, feedback_style,
  focus_style, reward, aesthetics), and optional `fact`.
- **3b:** a generated `questions_library.json` (200+ questions, validated).
- **3c wiring (`chatter.py`):** load the library (fallback to inline `QUESTIONS`);
  `_build_question` prefers an unused library question offline; `answer_question`
  persists `prefs`+`fact` (reusing `_merge_fact`); `behavior_hints` reads structured
  prefs first, keyword `_pref_extract` as fallback. API questions append to
  `learned_questions.json` for offline reuse. Schema bump + self-heal.

## Phase 4 — Expand fear triggers (personality-modulated)
Route all through `Brain.scare()` (already trust/jumpiness/shyness/comfort-damped)
via a trigger→amount dispatch at the mascot transition block. New triggers:
permission prompts, error storms, neglect, rough/fast drag, sudden activity spike.
Each small + trust-damped. Emit `fear` events. Document in `BRAIN.md`.

## Phase 5 — File organization  ☑ (repo was already clean)
- `tools/` holds the one-off slicers (`cat4_slice.py`, `cat5_slice.py`), paths fixed
  to resolve relative to repo root.
- `docs/` holds `ULTRAPLAN.md`, `MONITORING.md`, `IMPROVEMENT_PLAN.md`.
- `CLAUDE.md` + `cat4_states/MANIFEST.md` updated.

## Phase 6 — Continuous behaviour monitoring (build-later)
See `docs/MONITORING.md`. In-mascot `cat_telemetry.jsonl` logger (event +
log-on-change heartbeat, rotation) → `analyze_telemetry.py` roll-up + dashboard
section → on-demand `/analyze-cat` review into `docs/IMPROVEMENT_PLAN.md`. Built on
the Phase-0 fields that 1b/4 already emit.

---

## Verification
- **Behaviour/state:** `python sim_harness.py` — scenarios for bond decay
  (0/1/3/7 idle days, floor, loyalty damping, pet halts decay), offline question
  flow (library answers move traits+prefs, no repeat until exhausted), fear storm
  (rises then recovers, trust blunts it).
- **Offline:** stub `_post_chat` to raise → questions still flow, `online` flips +
  re-probes, no exceptions.
- **GUI:** `python run_mascot.py` — drag both directions, click-pet, right-click
  menu, quiz pin all intact.
- **Dashboard:** `python dashboard.py --open` shows offline-reliance + bond-trend.

## Risks
- Drag vs pet — 6px deadzone + pet-on-release.
- Bond floor below quiz gate would disable quizzing — floor ≥ 25.
- Schema migration — guarded by schema bump + existing self-heal.
- Slicer path breakage — fixed to resolve from repo root.
- Question quality/dupes — generated bank validated before integration.
- Telemetry observer-effect / growth — off-thread try/except + rotation/cap.
