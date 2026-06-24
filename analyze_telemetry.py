#!/usr/bin/env python3
"""Roll up cat_telemetry.jsonl (+ rotations) into a behaviour/drive/bond/fear/net
summary. Zero-dep, stdlib only, read-only — the cheap analysis half of the Phase-6
monitor (see docs/MONITORING.md). The mascot produces the log; this consumes it.

Usage:
  python analyze_telemetry.py            # human-readable report over all logs
  python analyze_telemetry.py --json     # machine-readable summary (for a subagent)
  python analyze_telemetry.py --since 3600   # only the last N seconds
"""
import json
import sys
import time
import glob
import os
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(since=None):
    """Read the active log + any rotations, oldest first, as parsed records."""
    paths = sorted(glob.glob(os.path.join(HERE, "cat_telemetry.*.jsonl")))   # rotated
    active = os.path.join(HERE, "cat_telemetry.jsonl")
    if os.path.exists(active):
        paths.append(active)                                                  # newest
    cutoff = (time.time() - since) if since else None
    recs = []
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    if cutoff and r.get("ts", 0) < cutoff:
                        continue
                    recs.append(r)
        except OSError:
            pass
    recs.sort(key=lambda r: r.get("ts", 0))
    return recs


def summarize(recs):
    by_kind = Counter(r.get("kind") for r in recs)
    span = (recs[-1]["ts"] - recs[0]["ts"]) if len(recs) >= 2 else 0
    hours = span / 3600.0 if span else 0.0

    # behaviour histogram + over-use (only counts real transitions => stint counts)
    beh = Counter(r["name"] for r in recs if r.get("kind") == "behavior" and r.get("name"))
    beh_total = sum(beh.values())
    overuse = {k: round(100 * v / beh_total, 1)
               for k, v in beh.items() if beh_total and v / beh_total > 0.25}

    # mood distribution (from mood transitions + heartbeats)
    mood = Counter(r["mood"] for r in recs
                   if r.get("kind") in ("mood", "hb") and r.get("mood"))

    # bond trajectory
    bonds = [r for r in recs if r.get("kind") == "bond"]
    bond_reasons = Counter(r.get("reason") for r in bonds)
    decay_total = round(sum(r.get("delta", 0) for r in bonds
                            if r.get("reason") == "decay"), 2)
    bond_first = bonds[0]["affection"] if bonds else None
    bond_last = bonds[-1]["affection"] if bonds else None

    # fear by trigger
    fears = [r for r in recs if r.get("kind") == "fear"]
    fear_by_trigger = Counter(r.get("trigger") for r in fears)

    # net flips, urgent crossings
    nets = [r for r in recs if r.get("kind") == "net"]
    net_offline = sum(1 for r in nets if r.get("online") is False)
    net_online = sum(1 for r in nets if r.get("online") is True)
    urgents = Counter(r.get("drive") for r in recs
                      if r.get("kind") == "urgent" and r.get("on"))

    # drive equilibria from heartbeats
    hbs = [r for r in recs if r.get("kind") == "hb"]
    drive_means = {}
    if hbs:
        for d in ("energy", "hunger", "social", "fear"):
            vals = [r[d] for r in hbs if d in r]
            if vals:
                drive_means[d] = round(sum(vals) / len(vals), 1)

    return {
        "records": len(recs), "span_hours": round(hours, 2),
        "by_kind": dict(by_kind),
        "behaviour_counts": dict(beh.most_common()),
        "behaviour_overuse_pct": overuse,
        "mood_distribution": dict(mood.most_common()),
        "bond": {"first": bond_first, "last": bond_last,
                 "decay_total": decay_total, "by_reason": dict(bond_reasons)},
        "fear_by_trigger": dict(fear_by_trigger),
        "net": {"went_offline": net_offline, "came_online": net_online},
        "urgent_crossings": dict(urgents),
        "drive_means": drive_means,
    }


def _report(s):
    L = []
    L.append("=== Tabby telemetry roll-up ===")
    L.append(f"records: {s['records']}   span: {s['span_hours']}h   "
             f"kinds: {s['by_kind']}")
    L.append("")
    L.append("-- behaviour (stint counts) --")
    for k, v in s["behaviour_counts"].items():
        flag = "  <-- OVER-USED (>25%)" if k in s["behaviour_overuse_pct"] else ""
        L.append(f"  {k:14} {v}{flag}")
    if not s["behaviour_counts"]:
        L.append("  (no behaviour transitions yet)")
    L.append("")
    L.append(f"-- mood --   {s['mood_distribution'] or '(none)'}")
    L.append(f"-- drive means (heartbeats) --   {s['drive_means'] or '(no heartbeats yet)'}")
    L.append(f"-- urgent crossings --   {s['urgent_crossings'] or '(none)'}")
    L.append("")
    b = s["bond"]
    L.append(f"-- bond --   first={b['first']} last={b['last']} "
             f"decay_total={b['decay_total']}   by_reason={b['by_reason'] or '(none)'}")
    L.append(f"-- fear by trigger --   {s['fear_by_trigger'] or '(none)'}")
    L.append(f"-- net flips --   offline={s['net']['went_offline']} "
             f"online={s['net']['came_online']}")
    return "\n".join(L)


def main():
    since = None
    if "--since" in sys.argv:
        try:
            since = float(sys.argv[sys.argv.index("--since") + 1])
        except (ValueError, IndexError):
            since = None
    recs = _load(since)
    s = summarize(recs)
    if "--json" in sys.argv:
        print(json.dumps(s, indent=2))
    else:
        print(_report(s))


if __name__ == "__main__":
    main()
