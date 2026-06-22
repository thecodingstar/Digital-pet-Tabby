#!/usr/bin/env python3
"""Offline simulation harness for Tabby's memory brain (brief item M0).

Streams synthetic events through the chatter with the LLM call STUBBED (no
network, no budget spent) and reports metrics so every memory change is
measurable. Compares RECALL_MODE = "vector" (legacy) vs "structured" (new).

    python sim_harness.py [N]        # default N = 1000, runs offline in < 5s
"""
import sys
import random
import tempfile
from pathlib import Path

import chatter

EVENTS = ["greet", "pet", "claude_success", "claude_failure", "claude_question",
          "claude_done", "musing", "wake", "hungry", "wants_attention", "scared"]
MOODS = ["content", "sleepy", "hungry", "lonely", "playful", "scared"]
BEHAVS = ["idle", "sit", "sleep", "wander", "zoomies", "play", "beg", "seek", "cower"]
VOCAB = ["meow", "purr", "mrrp", "chirp", "yawn", "trill", "meep", "soft", "tiny",
         "happy", "sleepy", "watch", "pounce", "nap", "stretch", "blink", "swish"]


def rand_ctx():
    return {"mood": random.choice(MOODS), "behavior": random.choice(BEHAVS),
            "energy": random.randint(0, 100), "hunger": random.randint(0, 100),
            "social": random.randint(0, 100), "fear": random.randint(0, 100)}


def make_cat():
    """A Cat wired to throwaway temp files with the network stubbed."""
    tmp = Path(tempfile.mkdtemp())
    chatter.STATE = tmp / "state.json"
    chatter.KNOW = tmp / "brain.json"
    chatter.METRICS = tmp / "metrics.json"
    chatter.CONFIG = tmp / "nope.json"
    chatter.DAILY_BUDGET = 10 ** 9          # never throttle during the sim
    c = chatter.Cat()
    c.cfg = {"base_url": "x", "api_key": "x", "model": "x"}   # enable LLM path

    def stub(messages, max_tokens, temperature):
        # short, textually diverse line (lets pools fill past dedup); the line
        # text is irrelevant to structured recall, which keys on stored context.
        return " ".join(random.sample(VOCAB, 3))
    c._post_chat = stub
    return c


def run(mode, n, seed=0):
    random.seed(seed)
    chatter.RECALL_MODE = mode
    c = make_cat()
    hits, rewards = [], []
    for _ in range(n):
        ev, ctx = random.choice(EVENTS), rand_ctx()
        before = c._metrics["local_hits"]
        c._generate(ev, ctx)
        hit = c._metrics["local_hits"] > before
        hits.append(1 if hit else 0)
        # outcome proxy: a line "lands" when the context it was learned in
        # matches the current mood -> rewards context-appropriate lines.
        served = c._last_served[1] if c._last_served else None
        if served is not None:
            good = served.get("cstruct", {}).get("mood") == ctx["mood"]
            c.report_outcome(1.0 if good else 0.0)
            rewards.append(served.get("reward", 0.5))
    m = c._metrics
    served_n = max(m["served"], 1)
    hit_n = max(m["local_hits"], 1)

    def rate(seq):
        return round(sum(seq) / max(len(seq), 1), 3)
    return {
        "mode": mode,
        "local_hit_rate": round(m["local_hits"] / served_n, 3),
        "early_hit": rate(hits[:200]),
        "late_hit": rate(hits[-200:]),
        "avg_served_sim": round(m["sim_sum"] / hit_n, 3),
        "early_reward": rate(rewards[:200]),
        "late_reward": rate(rewards[-200:]),
        "repeat_rate": round(m["repeats"] / served_n, 3),
        "api_calls": m["api"],
        "lines": sum(len(p) for p in c.know["lines"].values()),
    }


def rhythm_checks():
    """X12: rhythm / anticipation / comfort-style assertions for the X-series
    wiring. Independent of the memory sim; pure brain, offline, instant."""
    import time
    import brain
    from brain import Brain
    base = time.time()
    H = [14, 15]
    checks = []

    def chk(name, ok):
        checks.append((name, bool(ok)))

    # 1. curve peaks in H; predicted False before samples, True in H, False outside
    b = Brain()
    b.note_activity(1.0, now=base, hour=14)
    chk("predicted False before enough samples", b.predicted_active(14) is False)
    for _ in range(brain.RHYTHM_MIN_SAMPLES):
        for h in H:
            b.note_activity(1.0, now=base, hour=h)
    curve = b.active_curve()
    chk("active_curve peaks in H", max(range(24), key=lambda i: curve[i]) in H)
    chk("predicted_active True in H", b.predicted_active(14) and b.predicted_active(15))
    chk("predicted_active False outside H", not b.predicted_active(3))
    # 2. pre_active in the hour before H, not during
    chk("pre_active True at 13", b.pre_active(13))
    chk("pre_active False at 14", not b.pre_active(14))
    # 3. decay: a bump 40 days back decays below its undecayed value
    b2 = Brain()
    b2.note_activity(10.0, now=base - 40 * 86400, hour=14)
    b2.note_activity(1.0, now=base, hour=2)
    chk("old bucket decayed", b2.active_hours[14] < 10.0)

    # 4. comfort_style "space" yields a strictly smaller fear delta than cheer/None
    def fear_delta(comfort):
        bb = Brain()
        bb.apply_hints({"traits": {"shyness": 0.5}, "prefs": {"comfort_style": comfort}})
        bb.fear = 0.0
        bb.scare(45)
        return bb.fear
    fs, fc, fn = fear_delta("space"), fear_delta("cheer"), fear_delta(None)
    chk("space scare < cheer", fs < fc)
    chk("cheer == none (unchanged)", abs(fc - fn) < 1e-9)

    # 5. cold start: empty hints -> no anticipation, no drive runaway, variety kept
    b3 = Brain()
    b3.apply_hints({})
    seen, anticipated = set(), False
    for _ in range(4000):                     # ~6 min of ticks
        b3.tick(0.09)
        seen.add(b3.behavior)
        if b3.behavior == "anticipate":
            anticipated = True
    chk("cold start never anticipates", not anticipated)
    chk("cold start drives in range", 0 <= b3.energy <= 100 and 0 <= b3.hunger <= 100)
    chk("cold start keeps variety (>=5 behaviours)", len(seen) >= 5)
    return checks


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    rows = [run("vector", n), run("structured", n)]
    cols = ["mode", "local_hit_rate", "early_hit", "late_hit", "avg_served_sim",
            "early_reward", "late_reward", "repeat_rate", "api_calls", "lines"]
    print(f"=== Tabby memory sim: {n} events/run ===")
    print(" | ".join(f"{c:>15}" for c in cols))
    for r in rows:
        print(" | ".join(f"{str(r[c]):>15}" for c in cols))

    print("\n=== X-series rhythm / anticipation / comfort checks ===")
    results = rhythm_checks()
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if all(ok for _, ok in results):
        print("  all X12 checks passed")
    else:
        sys.exit("  X12 CHECKS FAILED")
