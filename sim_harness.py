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


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    rows = [run("vector", n), run("structured", n)]
    cols = ["mode", "local_hit_rate", "early_hit", "late_hit", "avg_served_sim",
            "early_reward", "late_reward", "repeat_rate", "api_calls", "lines"]
    print(f"=== Tabby memory sim: {n} events/run ===")
    print(" | ".join(f"{c:>15}" for c in cols))
    for r in rows:
        print(" | ".join(f"{str(r[c]):>15}" for c in cols))
