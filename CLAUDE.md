# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project: Pixel-buddy Mascot for Claude Code

Animated mascot that renders on the Windows taskbar, showing Claude Code activity state (idle, thinking, working, done) with emotions and animations.

**Current Status:** ✓ Working (v2: simple color-based emotions)  
**Sprint Goal:** Move mascot from terminal statusline to Windows taskbar overlay with emotions

## Tech Stack

- **Language:** Python 3, minimal deps (PyQt5 5.15+ for GUI)
- **Rendering:** 
  - Terminal ANSI half-block (statusline.py) — pixel-buddy cat in terminal
  - PyQt5 pixmap (taskbar_mascot.py / taskbar_mascot_v2.py) — taskbar overlay
- **Integration:** Claude Code hooks system → JSON state files (~/.claude/statusline/state/)
- **State machine:** Maps hook events (PreToolUse, PostToolUse, etc.) to mascot emotions

## File Structure

```
.
├── statusline/                          # Terminal statusline (WORKING)
│   ├── statusline.py                   # 3-line compact statusline + pixel-buddy cat sprite
│   ├── statusline_full.py              # 4-line extended statusline (no sprite)
│   ├── state_writer.py                 # Hook handler: Claude Code → state JSON
│   ├── mascot_pack.json                # Sprite def: 16×16 pixel-buddy cat, 12-color palette
│   └── state/                          # Per-session state (written by state_writer.py)
├── taskbar_mascot.py                   # FULL VERSION (has issues - see below)
├── taskbar_mascot_v2.py                # ✓ WORKING: simplified, color-based emotions
├── taskbar_mascot_simple.py            # Minimal test (blue rectangle with "MASCOT" text)
├── mascot_poc.py                       # Legacy: emoji bouncer (archived)
├── debug_mascot.py                     # Debug helper: logs PyQt5 init sequence
├── test_mascot_logic.py                # Unit tests for state decay, walk logic, etc.
├── test_integration.py                 # Integration test: verify mascot reads state files
├── CLAUDE.md                           # This file
└── README (planned)                    # User-facing guide
```

## Mascot Versions

### v2 (taskbar_mascot_v2.py) — CURRENT/RECOMMENDED ✓

**Status:** Stable, running, tested with Claude Code integration

**Features:**
- Colored rectangle on taskbar (bottom-right corner, 120×60 px)
- Text label showing current emotion (idle, thinking, tool_running, tool_success, tool_failure, done)
- Real-time state polling from ~/.claude/statusline/state/
- No external dependencies beyond PyQt5
- No sprite rendering (avoids QPixmap complexity)
- No system tray icon (avoids QSystemTrayIcon startup issues)

**Colors:**
- `idle` → blue
- `thinking` → purple
- `tool_running` → orange
- `tool_success` → green (1600ms hold)
- `tool_failure` → red (1800ms hold)
- `done` → light blue (2500ms hold)

**Run:** `python taskbar_mascot_v2.py`

**Integration verified:** ✓
- Reads state files written by state_writer.py ✓
- Responds to Claude Code events (tool_running, tool_success, etc.) ✓
- State decay after hold periods works ✓

---

### Full Version (taskbar_mascot.py) — HAS ISSUES ✗

**Status:** Crashes silently after initialization. Root cause: sprite rendering or tray icon setup

**Attempted features:**
- Full pixel-buddy cat sprite rendering (16×16 → 96×96 scaled)
- Heat palette (fur color shifts gold→red as context fills)
- Walk animation (ping-pong left/right during thinking/working)
- System tray icon with emotion tooltip
- Semi-transparent background

**Issues encountered:**
1. Process exits silently without exceptions in logs
2. No stderr/stdout capture (backgrounding in PowerShell silences output)
3. Error handlers in __init__, paintEvent, animate don't trigger
4. Crash likely in: SpriteRenderer.render_frame_to_pixmap() or QSystemTrayIcon.setIcon()
5. Basic PyQt5 event loop works (verified with debug_mascot.py)
6. Window creation and geometry setting work
7. Problem is specific to sprite rendering or tray setup

**Debugging done:**
- ✓ Verified taskbar rect detection works
- ✓ Confirmed PyQt5 imports and QApplication startup OK
- ✓ Tested basic window with window.show() and event loop — works
- ✓ Integrated test shows state loading/parsing works
- ✓ Unit tests pass: state decay, walk animation, palette heat-shift, downsampling
- ✗ Full mascot with sprites crashes before any error handler executes

**Next steps to fix:**
1. Try QPixmap creation outside paintEvent (cache during init)
2. Remove QSystemTrayIcon, replace with simple window-close button
3. Simplify sprite rendering: skip downsampling, render directly at small scale
4. Add try-except at app exec level to catch unhandled exceptions

---

## Core Concepts

### State Machine

States (mapped from Claude Code hook events):
| State | Trigger | Hold | Animation |
|-------|---------|------|-----------|
| `idle` | Default / decay | — | 1200ms period, 2 frames |
| `thinking` | UserPromptSubmit | — | 350ms period, 3 frames, walks |
| `tool_running` | PreToolUse (tool) | — | 250ms period, 2 frames, walks |
| `tool_success` | PostToolUse (ok) | 1600ms | 1 frame, then → idle |
| `tool_failure` | PostToolUse (error) | 1800ms | 1 frame, then → idle |
| `subagent_running` | PreToolUse (Task) | — | 250ms period, 2 frames, walks |
| `question` | PreToolUse (AskUserQuestion/ExitPlanMode) | — | → thinking after decay |
| `permission` | Notification | — | 1 frame |
| `done` | Stop | 2500ms | 2 frames, then → idle |
| `auth_success` | SessionStart (login/startup) | 2000ms | 1 frame, then → idle |

Decay: hold-period states automatically fall back to idle (or subagent_running if agents active).

### Sprite Pack (mascot_pack.json)

- **Canvas:** 16×16 pixels
- **Palette:** 12 colors; palette[2] (fur #f5d08b) heat-shifts toward red (#ff4444) as context fills 60%→85%
- **Frames:** Named sprites for each state (idle_1/2, thinking_1-3, tool_1-2, ok_1, fail_1, question_1, permission_1, sub_1-2, done_1-2, auth_1)
- **Render modes:** half-block (terminal) or direct pixels (GUI)

### State Files

**Location:** `~/.claude/statusline/state/<session_id>.json` (written by state_writer.py hook)

**Schema:**
```json
{
  "currentState": "tool_running",
  "lastStateChangedAt": 1781938424.18,
  "lastUpdatedAt": 1781938424.18,
  "lastToolName": "Read",
  "toolCountInTurn": 19,
  "failedToolCountInTurn": 0,
  "activeSubagentCount": 0
}
```

**Staleness cutoff:** 600s — if lastUpdatedAt > 600s old, state resets to idle

## Development & Testing

### Running

**Terminal statusline (already integrated):**
```bash
# Registered in Claude Code settings, runs on every render
python statusline/statusline.py < session.json
```

**Taskbar mascot (v2):**
```bash
python taskbar_mascot_v2.py
```

### Integration Points

- state_writer.py is registered as hook for: PreToolUse, PostToolUse, UserPromptSubmit, Notification, Stop, SubagentStop, SessionStart
- state files written to ~/.claude/statusline/state/ (checked by taskbar mascot every 500ms)
- Mascot also checks project statusline/state/ as fallback

### Unit Tests

```bash
python test_mascot_logic.py         # State decay, walk animation, palette, downsampling
python test_integration.py          # Load state files, compute effective state
```

All pass. ✓

### Manual Testing State Transitions

```python
import json, time, os
os.makedirs('~/.claude/statusline/state', exist_ok=True)
now = time.time()

# tool_success → idle after 1600ms
state = {
    "currentState": "tool_success",
    "lastStateChangedAt": now,
    "lastUpdatedAt": now,
    "activeSubagentCount": 0
}
with open(f"~/.claude/statusline/state/manual-test.json", "w") as f:
    json.dump(state, f)
# Watch mascot show green, then decay to blue after 1.6s
```

## Issues Faced & Lessons

### ✓ Resolved

1. **State files in two locations** — state_writer.py writes to ~/.claude/statusline/state/, mascot initially looked in project dir
   - **Fix:** Updated _load_active_state() to check both locations

2. **Window positioning off-screen** — initially positioned above taskbar at (taskbar_top - mascot_height)
   - **Fix:** Moved to ON taskbar with y = taskbar_top + 5

3. **PyQt5 requires QApplication before QPixmap** — tried creating QPixmap in SpriteRenderer.__init__ before app existed
   - **Fix:** Deferred pixmap creation to paintEvent (where QApplication is active)

4. **PowerShell backgrounding silences stdout/stderr** — debug output never reached console
   - **Fix:** Write all debug info to files (mascot_init.log, etc.)

### ✗ Unresolved (Full Version)

1. **Silent crashes in full mascot** — process exits without error logs or exceptions
   - **Theory:** Crash in QPixmap.render_frame_to_pixmap() or QSystemTrayIcon.setIcon()
   - **Evidence:** Basic PyQt5 works; state loading works; sprite logic passes unit tests
   - **Status:** Diagnosed but not fixed (v2 workaround sufficient)

2. **Hard to debug PyQt5 crashes** — exceptions don't propagate to console when backgrounded
   - **Mitigation:** Run in foreground for debugging, add logging to all event handlers

## Recommendations

### Short term (current)
- Use v2 (taskbar_mascot_v2.py) in daily workflow — it's stable and provides state feedback
- It shows all essential emotions: idle, thinking, working, success/failure, done

### Medium term (next sprint)
- Fix sprite rendering or replace with emoji/symbol-based display
- Restore tray icon by wrapping QSystemTrayIcon creation in try-except, or use click-to-quit button
- Add autostart entry so mascot launches on Windows boot

### Long term
- Integrate v2 and statusline.py into a single unified render system
- Consider: web overlay (Electron app) instead of PyQt5 for better debugging
- Add sound effects or animations on state transitions

## Key Code Reuse

Functions shared across statusline.py, taskbar_mascot.py, taskbar_mascot_v2.py:

- `_hexrgb()` — parse "#rrggbb" to RGB tuple
- `_heat_palette()` — shift palette[2] based on context%
- `_downsample()` — shrink sprite by factor using mode-pixel
- `_effective_state()` — compute state + handle decay
- `get_taskbar_rect()` — Windows API call to detect taskbar position/size

All duplicated for now (no shared module) to keep mascots independent.
