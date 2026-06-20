# Tabby sprite library (cat4_states)

Normalized emotion + locomotion sprites for the cat **Tabby**.

- **Canvas:** 384×384 px, transparent PNG, every frame identical size.
- **Alignment:** horizontally centered, bottom-aligned (12 px floor margin) → common ground line for animation.
- **Background:** fully transparent (source checkerboard/gray was baked-in, removed via border flood-fill; interior eye-glints preserved).
- **Sources:** emotions + side-walk from `cat-2 emotions (GG_2).png`; run cycles from `Gemini_..._eei3tw...png` (scaled 2× nearest to match library scale). Pink sheets (GG_4/Untitled) and exact dup (vk5a6u) and typo sheet (GG_3) were dropped.

## Emotions (18)
idle, idle_blink, sitting, sleeping, stretch, alert, happy, love, thinking,
curious, playful, done (proud/satisfied), angry, hungry, sad, scared, surprised, grumpy

Deduped variants from GG_2: kept the stronger pose, dropped near-dups
(proud_a≈proud_b, curious_a≈curious_b, angry_b≈grumpy, hungry_b weaker, sad_a < sad_b).

## Walk cycle — side, 6 frames (loops)
`walk-1 → walk-2 → walk-3 → walk-4 → walk-5 → walk-6 → (loop)`

| frame | pose |
|-------|------|
| walk-1 | contact A (right lead foot plants) |
| walk-2 | recoil A (weight absorb / down) |
| walk-3 | passing A (legs cross under body) |
| walk-4 | contact B (left lead foot plants) |
| walk-5 | recoil B (down) |
| walk-6 | passing B (legs cross under) |

Contact → recoil → passing → opposite contact = smooth alternating-leg gait.
`walk-left-1..6` = horizontal mirror (faces left), same order.

## Run cycle — directional, 6 frames each (loops)
`run-1..6` (faces right), `run-left-1..6` (faces left). Sheet order 1→6 is the
gallop sequence; loops 6→1.

## Animated previews
`_cycles/walk.gif`, `_cycles/walk-left.gif`, `_cycles/run.gif`, `_cycles/run-left.gif`

## Not included
Jump frames (eei3tw) skipped: scattered across the sheet with ambiguous
duplicate labels (multiple "JUMP START" cells) — high mislabel risk. Available
as a follow-up if wanted.
