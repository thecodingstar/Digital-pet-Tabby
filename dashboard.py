#!/usr/bin/env python3
"""Tabby brain dashboard — a zero-dependency visual of whether she's improving.

Reads the three runtime files (cat_metrics.json / cat_state.json / cat_brain.json)
and writes a standalone dashboard.html with inline-SVG charts. No server, no CDN,
stdlib only — double-click the HTML, or run with --open to launch it.

    python dashboard.py            # regenerate dashboard.html
    python dashboard.py --open     # regenerate and open in the browser

Re-run any time to refresh. The cat's right-click "Stats" menu calls build().
"""
import os
import sys
import json
import html
import time
import webbrowser
from pathlib import Path

HERE = Path(__file__).parent
METRICS = HERE / "cat_metrics.json"
STATE = HERE / "cat_state.json"
BRAIN = HERE / "cat_brain.json"
OUT = HERE / "dashboard.html"

# palette (matches the in-app cat panels)
C_BG, C_CARD, C_LINE, C_TEXT, C_MUTE = "#15171d", "#1a1c24", "#3a3f4b", "#e6e9f0", "#9aa0b0"
C_GREEN, C_BLUE, C_GOLD, C_RED, C_PINK, C_TEAL = (
    "#5acd82", "#5fa5eb", "#f4e699", "#e15f5f", "#eb6e8c", "#5fd7cd")


def _load(p, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def esc(s):
    return html.escape(str(s))


# --- inline SVG primitives ---------------------------------------------------
def _line_chart(days, series, w=560, h=220, dom=(0.0, 1.0)):
    """series: list of (name, color, [values aligned to days]). y-domain dom."""
    if not days:
        return "<p class='mute'>no data yet</p>"
    pad_l, pad_r, pad_t, pad_b = 38, 12, 14, 28
    iw, ih = w - pad_l - pad_r, h - pad_t - pad_b
    lo, hi = dom
    span = (hi - lo) or 1.0
    n = len(days)

    def X(i):
        return pad_l + (iw if n == 1 else iw * i / (n - 1))

    def Y(v):
        return pad_t + ih * (1 - (v - lo) / span)

    parts = [f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}'>"]
    # gridlines + y labels
    for g in range(5):
        gv = lo + span * g / 4
        gy = Y(gv)
        parts.append(f"<line x1='{pad_l}' y1='{gy:.1f}' x2='{w-pad_r}' y2='{gy:.1f}' "
                     f"stroke='{C_LINE}' stroke-width='1' opacity='0.5'/>")
        parts.append(f"<text x='{pad_l-6}' y='{gy+3:.1f}' fill='{C_MUTE}' "
                     f"font-size='9' text-anchor='end'>{gv:.2f}</text>")
    # x labels (day, short)
    for i, d in enumerate(days):
        parts.append(f"<text x='{X(i):.1f}' y='{h-8}' fill='{C_MUTE}' font-size='9' "
                     f"text-anchor='middle'>{esc(d[5:])}</text>")
    # each series
    for name, color, vals in series:
        pts = [(X(i), Y(v)) for i, v in enumerate(vals) if v is not None]
        if len(pts) > 1:
            poly = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
            parts.append(f"<polyline points='{poly}' fill='none' stroke='{color}' "
                         f"stroke-width='2'/>")
        for x, y in pts:
            parts.append(f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3' fill='{color}'/>")
    parts.append("</svg>")
    # legend
    leg = " ".join(f"<span class='dot' style='background:{c}'></span>{esc(nm)}"
                   for nm, c, _ in series)
    return "".join(parts) + f"<div class='legend'>{leg}</div>"


def _bars(items, w=560, bar_h=18, gap=8, dom=1.0, fmt="{:.2f}"):
    """items: list of (label, value, color). Horizontal bars scaled to dom."""
    if not items:
        return "<p class='mute'>nothing yet</p>"
    lab_w, val_w = 130, 44
    iw = w - lab_w - val_w
    h = len(items) * (bar_h + gap) + gap
    parts = [f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}'>"]
    y = gap
    for label, val, color in items:
        frac = max(0.0, min(1.0, (val or 0) / dom))
        parts.append(f"<text x='0' y='{y+bar_h-4}' fill='{C_TEXT}' font-size='11'>{esc(label)}</text>")
        parts.append(f"<rect x='{lab_w}' y='{y}' width='{iw}' height='{bar_h}' rx='5' fill='{C_LINE}'/>")
        parts.append(f"<rect x='{lab_w}' y='{y}' width='{iw*frac:.1f}' height='{bar_h}' rx='5' fill='{color}'/>")
        parts.append(f"<text x='{w}' y='{y+bar_h-4}' fill='{C_MUTE}' font-size='10' "
                     f"text-anchor='end'>{esc(fmt.format(val))}</text>")
        y += bar_h + gap
    parts.append("</svg>")
    return "".join(parts)


def _histogram(counts, w=560, h=120, color=C_TEAL):
    """24-bucket active-hours histogram."""
    if not counts or sum(counts) == 0:
        return "<p class='mute'>no activity rhythm learned yet</p>"
    pad_b = 18
    iw, ih = w - 8, h - pad_b
    bw = iw / 24
    mx = max(counts) or 1
    parts = [f"<svg viewBox='0 0 {w} {h}' width='100%' height='{h}'>"]
    for i, c in enumerate(counts):
        bh = ih * (c / mx)
        x = 4 + i * bw
        parts.append(f"<rect x='{x:.1f}' y='{ih-bh:.1f}' width='{bw-2:.1f}' height='{bh:.1f}' "
                     f"rx='2' fill='{color}'/>")
        if i % 3 == 0:
            parts.append(f"<text x='{x+bw/2:.1f}' y='{h-5}' fill='{C_MUTE}' font-size='8' "
                         f"text-anchor='middle'>{i}</text>")
    parts.append("</svg>")
    return "".join(parts)


def _trend(days, metrics, key, higher_better=True):
    """First-vs-last verdict arrow for a metric across days."""
    vals = [metrics[d].get(key) for d in days if metrics[d].get(key) is not None]
    if len(vals) < 2:
        return "<span class='mute'>need more days</span>"
    first, last = vals[0], vals[-1]
    up = last > first
    good = (up == higher_better) if first != last else None
    arrow = "&#8594;" if first == last else ("&#8599;" if up else "&#8600;")
    cls = "flat" if good is None else ("good" if good else "bad")
    return (f"<span class='trend {cls}'>{arrow} {first:.2f} &#8594; {last:.2f}</span>")


def build():
    """Read the runtime files and (over)write dashboard.html. Returns its path."""
    metrics = _load(METRICS, {})
    state = _load(STATE, {})
    brain = _load(BRAIN, {})
    days = sorted(metrics)

    name = esc(state.get("name", "Tabby"))
    aff = int(state.get("affection", 0))
    inter = int(state.get("interactions", 0))
    mood = esc(state.get("mood", "?"))
    traits = state.get("traits", {})
    drives = state.get("drives", {}) or {}
    affinity = drives.get("affinity", {}) or {}
    active = drives.get("active_hours", []) or []
    beh_secs = drives.get("behavior_secs", {}) or {}
    beh_cnt = drives.get("behavior_counts", {}) or {}
    facts = [f for f in state.get("user_facts", []) if isinstance(f, dict)]
    profile = state.get("user_profile", {}) or {}

    def col(key):
        return [metrics[d].get(key) for d in days]

    # --- memory-quality chart (0..1) + reliance chart (counts) ---
    quality = _line_chart(days, [
        ("local hit rate", C_GREEN, col("local_hit_rate")),
        ("served sim", C_BLUE, col("avg_served_sim")),
        ("mean reward", C_GOLD, col("mean_served_reward")),
        ("repeat rate", C_RED, col("repeat_rate")),
    ])
    max_count = max([1] + [metrics[d].get("served", 0) for d in days])
    reliance = _line_chart(days, [
        ("served", C_TEXT, col("served")),
        ("api calls", C_RED, col("api")),
        ("local hits", C_GREEN, col("local_hits")),
    ], dom=(0, max_count))

    # --- personality + behaviour + rhythm ---
    trait_bars = _bars([(k, traits.get(k, 0), C_PINK) for k in
                        ("playfulness", "curiosity", "shyness", "sass")])
    aff_items = sorted(affinity.items(), key=lambda kv: -kv[1])
    aff_bars = _bars([(k, v, C_TEAL) for k, v in aff_items])
    hist = _histogram(active if len(active) == 24 else [])

    # behaviour usage: % of time spent in each (over-used states flagged red)
    total_secs = sum(beh_secs.values()) or 1.0
    beh_items = sorted(beh_secs.items(), key=lambda kv: -kv[1])

    def _beh_color(p):
        return C_RED if p > 25 else C_GOLD if p > 15 else C_TEAL
    beh_bars = _bars([(f"{k}  x{beh_cnt.get(k, 0)}", v / total_secs * 100,
                       _beh_color(v / total_secs * 100))
                      for k, v in beh_items], dom=100, fmt="{:.0f}%")
    if beh_items:
        top = beh_items[0]
        beh_note = (f"top state <b>{esc(top[0])}</b> takes "
                    f"{top[1]/total_secs*100:.0f}% of her time across "
                    f"{len(beh_items)} behaviours — red bars (&gt;25%) are over-used.")
    else:
        beh_note = "no autonomous behaviour recorded yet."

    # --- learned lines per event (count + avg reward) ---
    lines = brain.get("lines", {})
    line_rows = []
    for ev, pool in sorted(lines.items(), key=lambda kv: -len(kv[1])):
        rs = [e.get("reward", 0.5) for e in pool]
        avg = sum(rs) / len(rs) if rs else 0
        uses = sum(e.get("uses", 0) for e in pool)
        line_rows.append(f"<tr><td>{esc(ev)}</td><td>{len(pool)}</td>"
                         f"<td>{uses}</td><td>{avg:.2f}</td></tr>")
    line_table = ("<table><tr><th>event</th><th>lines</th><th>uses</th><th>avg reward</th></tr>"
                  + "".join(line_rows) + "</table>") if line_rows else "<p class='mute'>no lines yet</p>"

    # --- confidence facts ---
    facts_sorted = sorted(facts, key=lambda f: -f.get("confidence", 0))[:8]
    fact_bars = _bars([(f"{f['text'][:22]} ({f.get('category','?')[:4]})",
                        f.get("confidence", 0), C_GOLD) for f in facts_sorted])

    # --- quiz answers (what she's learned about you) ---
    pq = sorted(profile.values(), key=lambda v: v.get("ts", 0), reverse=True)
    quiz_rows = "".join(
        f"<tr><td>{esc(v.get('q',''))}</td><td class='ans'>{esc(v.get('a',''))}</td></tr>"
        for v in pq if isinstance(v, dict))
    quiz_table = ("<table><tr><th>she asked</th><th>you said</th></tr>" + quiz_rows
                  + "</table>") if quiz_rows else "<p class='mute'>no quiz answers yet</p>"

    # --- verdict ---
    verdict = (f"local hit rate {_trend(days, metrics, 'local_hit_rate', True)} &nbsp; "
               f"repeats {_trend(days, metrics, 'repeat_rate', False)} &nbsp; "
               f"reward {_trend(days, metrics, 'mean_served_reward', True)}")

    gen = time.strftime("%Y-%m-%d %H:%M")
    doc = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{name} — brain dashboard</title>
<style>
  body{{margin:0;background:{C_BG};color:{C_TEXT};font:14px/1.5 'Segoe UI',system-ui,sans-serif}}
  .wrap{{max-width:1180px;margin:0 auto;padding:24px}}
  h1{{font-size:22px;margin:0 0 2px}} h2{{font-size:14px;color:{C_MUTE};margin:0 0 12px;font-weight:600}}
  .sub{{color:{C_MUTE};margin-bottom:18px}}
  .grid{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
  .card{{background:{C_CARD};border:1px solid {C_LINE};border-radius:14px;padding:16px}}
  .card.full{{grid-column:1/3}}
  .stats{{display:flex;gap:26px;margin:6px 0 18px;flex-wrap:wrap}}
  .stat .n{{font-size:26px;font-weight:700}} .stat .l{{color:{C_MUTE};font-size:12px}}
  .legend{{margin-top:8px;color:{C_MUTE};font-size:11px}}
  .dot{{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 5px 0 12px;vertical-align:middle}}
  table{{width:100%;border-collapse:collapse;font-size:12px}}
  th,td{{text-align:left;padding:5px 8px;border-bottom:1px solid {C_LINE}}}
  th{{color:{C_MUTE};font-weight:600}} td.ans{{color:{C_GOLD}}}
  .mute{{color:{C_MUTE}}}
  .verdict{{background:{C_CARD};border:1px solid {C_LINE};border-radius:12px;padding:12px 16px;margin-bottom:18px}}
  .trend.good{{color:{C_GREEN}}} .trend.bad{{color:{C_RED}}} .trend.flat{{color:{C_MUTE}}}
  .bond{{height:10px;background:{C_LINE};border-radius:6px;overflow:hidden;margin-top:6px}}
  .bond>i{{display:block;height:100%;width:{aff}%;background:{C_PINK}}}
</style></head><body><div class="wrap">
  <h1>🐈 {name}'s brain</h1>
  <div class="sub">generated {gen} · mood: {mood} · schema v{esc(state.get('schema_version','?'))}</div>

  <div class="stats">
    <div class="stat"><div class="n">{aff}%</div><div class="l">bond</div>
      <div class="bond" style="width:120px"><i></i></div></div>
    <div class="stat"><div class="n">{inter}</div><div class="l">interactions</div></div>
    <div class="stat"><div class="n">{len(facts)}</div><div class="l">facts learned</div></div>
    <div class="stat"><div class="n">{len(profile)}</div><div class="l">quiz answers</div></div>
    <div class="stat"><div class="n">{sum(len(p) for p in lines.values())}</div><div class="l">learned lines</div></div>
  </div>

  <div class="verdict"><b>Improving?</b> &nbsp; {verdict}
    <div class="mute" style="font-size:11px;margin-top:4px">↗ local hit rate up + repeats down = she leans on memory more, calls the API less.</div>
  </div>

  <div class="grid">
    <div class="card"><h2>Memory quality over time</h2>{quality}</div>
    <div class="card"><h2>API reliance (lower api = better)</h2>{reliance}</div>
    <div class="card"><h2>Personality traits</h2>{trait_bars}</div>
    <div class="card"><h2>Behaviour affinity (what earns your attention)</h2>{aff_bars}</div>
    <div class="card full"><h2>Behaviour usage — time spent per state</h2>{beh_bars}
      <div class="mute" style="font-size:11px;margin-top:8px">{beh_note}</div></div>
    <div class="card full"><h2>Active-hours rhythm (when she thinks you're around)</h2>{hist}</div>
    <div class="card"><h2>What she believes about you (confidence)</h2>{fact_bars}</div>
    <div class="card"><h2>Quiz answers</h2>{quiz_table}</div>
    <div class="card full"><h2>Learned lines per event</h2>{line_table}</div>
  </div>
</div></body></html>"""

    OUT.write_text(doc, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"[dashboard] wrote {path}")
    if "--open" in sys.argv:
        webbrowser.open(path.as_uri())
