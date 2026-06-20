#!/usr/bin/env python3
import sys, json, subprocess, io, re
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

RESET = "\033[0m"
DIM   = "\033[38;5;243m"

def ansi(code, text):
    return f"\033[{code}m{text}{RESET}"

def rgb(r, g, b, text, bold=False):
    return f"\033[{'1;' if bold else ''}38;2;{r};{g};{b}m{text}{RESET}"

def badge(r, g, b, text):
    return f"\033[1;38;2;20;20;25;48;2;{r};{g};{b}m {text} {RESET}"

SEP = f" {DIM}·{RESET} "   # soft separator within a row
GAP = "   "                 # column gap between row segments

def label(t):
    """Fixed-width dim bold gutter heading: CTX / SES / LIM."""
    return f"\033[1;38;5;245m{t:<3}{RESET}  "

def fmt_k(n):
    if n < 0: return "?"
    if n >= 1_000_000_000: return f"{n/1_000_000_000:.2f}B"
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1000: return f"{n/1000:.1f}k"
    return str(n)

def get(obj, *keys, default=None):
    for k in keys:
        if not isinstance(obj, dict): return default
        obj = obj.get(k)
        if obj is None: return default
    return obj

def time_until(unix_sec):
    if not unix_sec or unix_sec <= 0: return ""
    diff = unix_sec - datetime.now(timezone.utc).timestamp()
    if diff <= 0: return "now"
    h, rem = divmod(int(diff), 3600)
    m = rem // 60
    if h >= 24:
        return f"{h // 24}d{h % 24}h"
    return f"{h}h{m:02d}m" if h > 0 else f"{m}m"

def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

GREEN  = (80, 250, 123)
YELLOW = (241, 250, 140)
RED    = (255, 85, 85)
CYAN   = (139, 233, 253)
PURPLE = (189, 147, 249)
ORANGE = (255, 184, 108)
PINK   = (255, 121, 198)

def grad_color(t):
    # 0.0 green -> 0.6 yellow -> 1.0 red
    if t < 0.6:
        return lerp(GREEN, YELLOW, t / 0.6)
    return lerp(YELLOW, RED, (t - 0.6) / 0.4)

EIGHTHS = " ▏▎▍▌▋▊▉█"

def grad_bar(pct, width=12):
    """Per-cell gradient bar with eighth-block precision."""
    frac = max(0.0, min(pct / 100.0, 1.0))
    cells = frac * width
    full = int(cells)
    rem = cells - full
    out = []
    for i in range(width):
        t = (i + 0.5) / width
        r, g, b = grad_color(t)
        if i < full:
            out.append(f"\033[38;2;{r};{g};{b}m█")
        elif i == full and rem > 0:
            out.append(f"\033[38;2;{r};{g};{b}m{EIGHTHS[max(1, round(rem * 8))]}")
        else:
            out.append(f"\033[38;5;237m░")
    return "".join(out) + RESET

def mini_bar(pct, width=5):
    frac = max(0.0, min(pct / 100.0, 1.0))
    full = round(frac * width)
    r, g, b = grad_color(frac)
    return (f"\033[38;2;{r};{g};{b}m" + "■" * full
            + f"\033[38;5;237m" + "□" * (width - full) + RESET)

try:
    raw = sys.stdin.read().strip()
    d = json.loads(raw) if raw else {}
except Exception:
    d = {}

model        = get(d, 'model', 'display_name')           or 'Claude'
cwd          = get(d, 'workspace', 'current_dir')        or '.'
dir_name     = cwd.replace('\\', '/').rstrip('/').split('/')[-1]
out_style    = get(d, 'output_style', 'name')            or ''
cost         = float(get(d, 'cost', 'total_cost_usd')    or 0)
pct          = int(get(d, 'context_window', 'used_percentage') or 0)
duration_ms  = int(get(d, 'cost', 'total_duration_ms')   or 0)
api_ms       = int(get(d, 'cost', 'total_api_duration_ms') or 0)
lines_add    = int(get(d, 'cost', 'total_lines_added')   or 0)
lines_del    = int(get(d, 'cost', 'total_lines_removed') or 0)
tok_total    = int(get(d, 'context_window', 'context_window_size') if get(d, 'context_window', 'context_window_size') is not None else -1)
_cu          = get(d, 'context_window', 'current_usage') or {}
tok_in       = int(_cu.get('input_tokens')  if _cu.get('input_tokens')  is not None else -1)
tok_out      = int(_cu.get('output_tokens') if _cu.get('output_tokens') is not None else -1)
cache_read   = int(_cu.get('cache_read_input_tokens')     or 0)
cache_write  = int(_cu.get('cache_creation_input_tokens') or 0)
if _cu:
    tok_used = max(tok_in, 0) + max(tok_out, 0) + cache_read + cache_write
elif tok_total > 0:
    tok_used = round(pct / 100.0 * tok_total)
else:
    tok_used = -1
exceeds_200k = bool(get(d, 'exceeds_200k_tokens') or get(d, 'context_window', 'exceeds_200k_tokens') or False)
turns_val    = get(d, 'session', 'turn_count') if get(d, 'session', 'turn_count') is not None else get(d, 'turn_count')
turns        = int(turns_val) if turns_val is not None else -1

five_h_pct   = get(d, 'rate_limits', 'five_hour', 'used_percentage')
five_h_reset = get(d, 'rate_limits', 'five_hour', 'resets_at') or 0
seven_d_pct  = get(d, 'rate_limits', 'seven_day', 'used_percentage')
seven_d_reset= get(d, 'rate_limits', 'seven_day', 'resets_at') or 0
if five_h_pct  is not None: five_h_pct  = int(five_h_pct)
if seven_d_pct is not None: seven_d_pct = int(seven_d_pct)

# ---- Git: branch + ahead/behind + dirty counts + stash (porcelain v2) ----
git_str = ""
try:
    r = subprocess.run(['git', 'status', '--porcelain=v2', '--branch'],
                       capture_output=True, text=True, timeout=2, cwd=cwd)
    if r.returncode == 0:
        branch, ahead, behind = "", 0, 0
        staged = modified = untracked = conflicts = 0
        for line in r.stdout.splitlines():
            if line.startswith('# branch.head'):
                branch = line.split(' ', 2)[2]
            elif line.startswith('# branch.ab'):
                m = re.match(r'# branch\.ab \+(\d+) -(\d+)', line)
                if m: ahead, behind = int(m.group(1)), int(m.group(2))
            elif line.startswith(('1 ', '2 ')):
                xy = line.split(' ')[1]
                if xy[0] != '.': staged += 1
                if xy[1] != '.': modified += 1
            elif line.startswith('? '):
                untracked += 1
            elif line.startswith('u '):
                conflicts += 1
        if branch:
            parts = [rgb(*GREEN, f"⎇ {branch}", bold=True)]
            ab = ""
            if ahead:  ab += f"↑{ahead}"
            if behind: ab += f"↓{behind}"
            if ab: parts.append(rgb(*CYAN, ab))
            dirty = ""
            if staged:    dirty += rgb(*GREEN,  f"+{staged}")
            if modified:  dirty += rgb(*YELLOW, f"~{modified}")
            if untracked: dirty += rgb(*ORANGE, f"?{untracked}")
            if conflicts: dirty += rgb(*RED,    f"!{conflicts}")
            if dirty: parts.append(dirty)
            if not dirty and not ab:
                parts.append(rgb(*GREEN, "✓"))
            # stash count
            try:
                s = subprocess.run(['git', 'stash', 'list', '--format=.'],
                                   capture_output=True, text=True, timeout=2, cwd=cwd)
                n_stash = len(s.stdout.splitlines()) if s.returncode == 0 else 0
                if n_stash: parts.append(rgb(*PURPLE, f"⚑{n_stash}"))
            except Exception:
                pass
            git_str = SEP + " ".join(parts)
except Exception:
    pass

# ---- Code-Review-Graph status ----
crg_str = ""
try:
    crg_r = subprocess.run(['python', '-m', 'code_review_graph', 'status'],
                           capture_output=True, text=True, timeout=5)
    if crg_r.returncode == 0:
        nodes_m = re.search(r'(\d+)\s+nodes?', crg_r.stdout, re.IGNORECASE)
        crg_str = SEP + rgb(*PURPLE, "◉ CRG" + (f" {nodes_m.group(1)}n" if nodes_m else ""))
    else:
        crg_str = SEP + ansi("33", "◌ CRG ?")
except Exception:
    pass

# ---- Derived ----
def fmt_dur(ms):
    m, s = ms // 60000, (ms % 60000) // 1000
    if m >= 60:
        return f"{m // 60}h{m % 60:02d}m"
    return f"{m}m{s:02d}s"

hours_wall = duration_ms / 3_600_000
burn = (cost / hours_wall) if hours_wall > 0.02 else 0  # need >~1min for meaningful rate

# ---- All-time token usage (sum of transcript usage, incremental cache) ----
def get_alltime_tokens():
    """Sum usage across all ~/.claude/projects transcripts.
    Per-file (mtime, size) cache so only changed files re-parse;
    full total cached 120s so renders stay cheap."""
    import os, time
    from pathlib import Path
    cache_path = Path.home() / '.claude' / 'statusline_usage_cache.json'
    now = time.time()
    cache = {}
    try:
        cache = json.loads(cache_path.read_text(encoding='utf-8'))
    except Exception:
        pass
    if now - cache.get('_refreshed', 0) < 120:
        return cache.get('_total', -1)

    files = cache.get('files', {})
    proj_dir = Path.home() / '.claude' / 'projects'
    seen = set()
    for p in proj_dir.glob('*/*.jsonl'):
        key = str(p)
        seen.add(key)
        try:
            st = p.stat()
            ent = files.get(key)
            if ent and ent[0] == st.st_mtime and ent[1] == st.st_size:
                continue
            tot = 0
            with open(p, encoding='utf-8', errors='replace') as f:
                for line in f:
                    if '"usage"' not in line:
                        continue
                    try:
                        u = json.loads(line).get('message', {}).get('usage') or {}
                        tot += (u.get('input_tokens', 0) + u.get('output_tokens', 0)
                                + u.get('cache_creation_input_tokens', 0)
                                + u.get('cache_read_input_tokens', 0))
                    except Exception:
                        continue
            files[key] = [st.st_mtime, st.st_size, tot]
        except Exception:
            continue
    files = {k: v for k, v in files.items() if k in seen}
    total = sum(v[2] for v in files.values())
    try:
        cache_path.write_text(json.dumps(
            {'_refreshed': now, '_total': total, 'files': files}), encoding='utf-8')
    except Exception:
        pass
    return total

try:
    alltime_tok = get_alltime_tokens()
except Exception:
    alltime_tok = -1

clock = datetime.now().strftime("%H:%M")
style_str = (SEP + rgb(*PINK, f"✎ {out_style}")) if out_style and out_style.lower() != 'default' else ""

# ================= Row 1: identity (no gutter label) =================
print(badge(*CYAN, model)
      + "  " + rgb(255, 255, 255, dir_name, bold=True)
      + git_str + crg_str + style_str
      + SEP + f"{DIM}◷ {clock}{RESET}")

# ================= Row 2: CTX — context window =================
warn = ""
if exceeds_200k:
    warn = GAP + ansi("1;38;2;255;85;85;48;2;60;0;0", " >200k! ")
elif pct >= 80:
    warn = GAP + rgb(*RED, "⚠ compact soon", bold=True)

r_, g_, b_ = grad_color(pct / 100.0)
pct_str = rgb(r_, g_, b_, f"{pct:>3}%", bold=True)
tok_str = (GAP + rgb(*CYAN, f"{fmt_k(tok_used)}/{fmt_k(tok_total)}")) if tok_used >= 0 and tok_total > 0 else ""
io_str  = (GAP + f"{DIM}in{RESET} {rgb(*CYAN, fmt_k(tok_in))} {DIM}out{RESET} {rgb(*PURPLE, fmt_k(tok_out))}") if tok_in >= 0 and tok_out >= 0 else ""

print(label("CTX") + f"{grad_bar(pct)} {pct_str}{tok_str}{io_str}{warn}")

# ================= Row 3: SES — session economics =================
ses = [rgb(*YELLOW, f"${cost:.2f}", bold=True)
       + (f" {DIM}at{RESET} " + rgb(*ORANGE, f"${burn:.2f}/h") if burn > 0 else "")]
time_part = f"{DIM}⏱{RESET} {fmt_dur(duration_ms)}"
if api_ms > 0:
    time_part += f"{SEP}{DIM}api {fmt_dur(api_ms)}{RESET}"
ses.append(time_part)
if lines_add or lines_del:
    ses.append(rgb(*GREEN, f"+{lines_add}") + " " + rgb(*RED, f"−{lines_del}"))
if cache_read > 0 or cache_write > 0:
    saved = (cache_read / 1e6) * 2.70
    ses.append(f"{DIM}⛁ cache{RESET} " + rgb(*GREEN, f"${saved:.2f} saved"))
if turns >= 0:
    ses.append(f"{DIM}turn{RESET} " + rgb(*PURPLE, str(turns)))
if alltime_tok > 0:
    ses.append(f"{DIM}Σ all-time{RESET} " + rgb(*CYAN, f"{fmt_k(alltime_tok)} tok"))
print(label("SES") + GAP.join(ses))

# ================= Row 4: LIM — rate limits =================
lim = []
if five_h_pct is not None:
    reset = time_until(five_h_reset)
    rp = f" {DIM}resets {reset}{RESET}" if reset else ""
    lim.append(f"{DIM}5h{RESET} {mini_bar(five_h_pct)} {five_h_pct:>3}%{rp}")
if seven_d_pct is not None:
    reset = time_until(seven_d_reset)
    rp = f" {DIM}resets {reset}{RESET}" if reset else ""
    lim.append(f"{DIM}7d{RESET} {mini_bar(seven_d_pct)} {seven_d_pct:>3}%{rp}")
if lim:
    print(label("LIM") + GAP.join(lim))
