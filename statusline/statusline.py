#!/usr/bin/env python3
"""Simplified single-line statusline. Model · dir · git · ctx% · cost."""
import sys, json, subprocess, io, re, time, os

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

RESET = "\033[0m"
DIM   = "\033[38;5;243m"
SEP   = f" {DIM}·{RESET} "

def rgb(r, g, b, text, bold=False):
    return f"\033[{'1;' if bold else ''}38;2;{r};{g};{b}m{text}{RESET}"

def get(obj, *keys, default=None):
    for k in keys:
        if not isinstance(obj, dict): return default
        obj = obj.get(k)
        if obj is None: return default
    return obj

def fmt_k(n):
    if n < 0: return "?"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1000: return f"{n/1000:.1f}k"
    return str(n)

GREEN  = (80, 250, 123)
YELLOW = (241, 250, 140)
RED    = (255, 85, 85)
CYAN   = (139, 233, 253)

def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

def grad_color(t):
    t = max(0.0, min(1.0, t))
    if t < 0.6:
        return lerp(GREEN, YELLOW, t / 0.6)
    return lerp(YELLOW, RED, (t - 0.6) / 0.4)

def bar(pct, width=10):
    pct = max(0, min(100, pct))
    fill = round(pct / 100.0 * width)
    c = grad_color(pct / 100.0)
    return rgb(*c, "█" * fill) + f"{DIM}{'░' * (width - fill)}{RESET}"

def fmt_dur(secs):
    secs = int(secs)
    if secs <= 0: return "now"
    h, m = divmod(secs // 60, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d{h}h"
    if h: return f"{h}h{m:02d}m"
    return f"{m}m"

try:
    raw = sys.stdin.read().strip()
    d = json.loads(raw) if raw else {}
except Exception:
    d = {}

model = get(d, 'model', 'display_name') or 'Claude'
cwd   = get(d, 'workspace', 'current_dir') or '.'
dir_name = cwd.replace('\\', '/').rstrip('/').split('/')[-1]
pct   = int(get(d, 'context_window', 'used_percentage') or 0)
tok_total = get(d, 'context_window', 'context_window_size')
tok_total = int(tok_total) if tok_total is not None else -1
cu = get(d, 'context_window', 'current_usage') or {}
if cu:
    tok_used = sum(int(cu.get(k) or 0) for k in
                   ('input_tokens', 'output_tokens',
                    'cache_creation_input_tokens', 'cache_read_input_tokens'))
elif tok_total > 0:
    tok_used = round(pct / 100.0 * tok_total)
else:
    tok_used = -1

# ---- Git: branch + dirty flag ----
git_str = ""
try:
    r = subprocess.run(['git', 'status', '--porcelain=v2', '--branch'],
                       capture_output=True, text=True, timeout=2, cwd=cwd)
    if r.returncode == 0:
        branch, dirty = "", False
        for line in r.stdout.splitlines():
            if line.startswith('# branch.head'):
                branch = line.split(' ', 2)[2]
            elif line.startswith(('1 ', '2 ', '? ', 'u ')):
                dirty = True
        if branch:
            mark = rgb(*YELLOW, "*") if dirty else rgb(*GREEN, "✓")
            git_str = SEP + rgb(*GREEN, f"⎇ {branch}", bold=True) + " " + mark
except Exception:
    pass

r_, g_, b_ = grad_color(pct / 100.0)
pct_str = rgb(r_, g_, b_, f"{pct}%", bold=True)

# tokens used / total of context window
tok_str = ""
if tok_used >= 0 and tok_total > 0:
    tok_str = f" {rgb(*CYAN, fmt_k(tok_used))}{DIM}/{RESET}{rgb(*CYAN, fmt_k(tok_total))}"

print(rgb(*CYAN, model, bold=True)
      + SEP + rgb(255, 255, 255, dir_name, bold=True)
      + git_str
      + SEP + f"{DIM}ctx{RESET} {pct_str}{tok_str}")

# ---- Line 2: rate-limit windows with progress bar + reset countdown ----
now = time.time()
rl = get(d, 'rate_limits') or {}
segs = []
for key, label in (('five_hour', '5h'), ('seven_day', '7d')):
    win = rl.get(key) if isinstance(rl, dict) else None
    if not isinstance(win, dict):
        continue
    used = int(win.get('used_percentage') or 0)
    resets_at = win.get('resets_at')
    c = grad_color(used / 100.0)
    seg = f"{DIM}{label}{RESET} {bar(used)} {rgb(*c, f'{used}%', bold=True)}"
    if resets_at:
        seg += f" {DIM}↻ {fmt_dur(resets_at - now)}{RESET}"
    segs.append(seg)
if segs:
    print(SEP.join(segs))

# ---- Mascot: pixel-buddy (cat) — port of TeXmeijin/claude-code-mascot-statusline ----
# Code MIT (c) meijin; sprite pack CC-BY-4.0. Idle animation + context-heat palette.
def _hexrgb(c):
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))

def _heat_palette(palette, used_pct):
    # palette[2] (fur) shifts toward red as context fills (60%..85%)
    if used_pct is None or used_pct <= 60:
        return palette
    t = min(1.0, (used_pct - 60) / 25.0)
    orig = palette[2]
    if not orig:
        return palette
    r, g, b = _hexrgb(orig)
    nr = round(r + (255 - r) * t); ng = round(g + (68 - g) * t); nb = round(b + (68 - b) * t)
    out = list(palette)
    out[2] = f"#{nr:02x}{ng:02x}{nb:02x}"
    return out

def _cell(top, bot, pal):
    ct = pal[top] if top is not None else None
    cb = pal[bot] if bot is not None else None
    if ct is None and cb is None:
        return " "
    if ct is not None and cb is not None:
        tr, tg, tb = _hexrgb(ct)
        if ct == cb:
            return f"\033[38;2;{tr};{tg};{tb}m█{RESET}"
        br, bg, bb = _hexrgb(cb)
        return f"\033[38;2;{tr};{tg};{tb}m\033[48;2;{br};{bg};{bb}m▀{RESET}"
    if ct is not None:
        r, g, b = _hexrgb(ct)
        return f"\033[38;2;{r};{g};{b}m▀{RESET}"
    r, g, b = _hexrgb(cb)
    return f"\033[38;2;{r};{g};{b}m▄{RESET}"

def _downsample(sprite, factor=2):
    # shrink by `factor` in both axes; per block pick most-common non-transparent index
    H = len(sprite)
    W = len(sprite[0]) if H else 0
    out = []
    for y in range(0, H, factor):
        row = []
        for x in range(0, W, factor):
            cands = []
            for dy in range(factor):
                for dx in range(factor):
                    yy, xx = y + dy, x + dx
                    if yy < H and xx < len(sprite[yy]):
                        v = sprite[yy][xx]
                        if v:
                            cands.append(v)
            if not cands:
                row.append(0)
            else:
                best, bc = 0, -1
                for v in set(cands):
                    n = cands.count(v)
                    if n > bc:
                        bc, best = n, v
                row.append(best)
        out.append(row)
    return out

# state hold (ms) before decaying, and per-state frame period (ms)
_HOLDS = {"tool_success": 1600, "tool_failure": 1800, "done": 2500, "auth_success": 2000}
_PERIODS = {"idle": 1200, "thinking": 350, "tool_running": 250, "subagent_running": 250}

def _effective_state(st, now_s):
    if not st:
        return "idle", 0
    if now_s - float(st.get("lastUpdatedAt", 0)) > 600:   # stale session
        return "idle", 0
    cs = st.get("currentState", "idle")
    sub = int(st.get("activeSubagentCount", 0) or 0)
    hold = _HOLDS.get(cs)
    changed = float(st.get("lastStateChangedAt", 0))
    if hold and (now_s - changed) * 1000 >= hold:
        if sub > 0:
            return "subagent_running", sub
        if cs in ("question", "permission"):
            return "thinking", sub
        return "idle", sub
    return cs, sub

try:
    _here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(_here, "mascot_pack.json"), encoding="utf-8") as _pf:
        _pack = json.load(_pf)
    _pal = _heat_palette(_pack["sprite"]["palette"], pct)

    _st = None
    _sid = get(d, "session_id") or ""
    if _sid:
        try:
            with open(os.path.join(_here, "state", f"{_sid}.json"), encoding="utf-8") as _sf:
                _st = json.load(_sf)
        except Exception:
            _st = None
    _state, _ = _effective_state(_st, now)

    _frames = _pack["states"].get(_state) or _pack["states"]["idle"]
    _period = _PERIODS.get(_state, 600)
    _fi = int(now * 1000 / _period) % len(_frames)
    _sprite = _downsample(_pack["sprites"][_frames[_fi]], 2)

    # While "working", pace left<->right and face the travel direction.
    _WALK = {"thinking", "tool_running", "subagent_running"}
    _pad = ""
    if _state in _WALK:
        _span = 10                       # cells of roaming room
        _ph = int(now * 6) % (2 * _span)  # ~6 steps/sec ping-pong
        _right = _ph < _span
        _off = _ph if _right else (2 * _span - _ph)
        _pad = " " * _off
        if not _right:                   # mirror columns to face left
            _sprite = [list(reversed(r)) for r in _sprite]

    for _ri in range(0, len(_sprite), 2):
        _top = _sprite[_ri]
        _bot = _sprite[_ri + 1] if _ri + 1 < len(_sprite) else [0] * len(_top)
        print(_pad + "".join(_cell(_top[c], _bot[c], _pal) for c in range(len(_top))))
except Exception:
    pass
