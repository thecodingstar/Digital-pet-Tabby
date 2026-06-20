#!/usr/bin/env python3
"""Tabby's voice + personality + memory.

A small, provider-agnostic (OpenAI-compatible) chat client that gives the cat
short spoken lines, plus a persistent personality and memory that grows over
time. Network calls run on a background thread so the UI never blocks; if no
API key is configured (or the call fails) it falls back to canned lines.

Config (cat_config.json, gitignored):
    {"base_url": "https://api.groq.com/openai/v1",
     "api_key": "gsk_...",
     "model": "llama-3.3-70b-versatile"}

State (cat_state.json, gitignored) persists affection, traits, learned facts.
"""
import os
import re
import math
import zlib
import json
import time
import random
import threading
import urllib.request
from collections import deque
from pathlib import Path

# --- tiny offline "embeddings": stable hashed bag-of-words + cosine ----------
VDIM = 256


def _tokens(t):
    return re.findall(r"[a-z0-9_]+", t.lower())   # keep bucket tokens (energy_lo) whole


def _vec(text):
    v = {}
    for tok in _tokens(text):
        h = zlib.crc32(tok.encode()) % VDIM      # stable across runs
        v[h] = v.get(h, 0.0) + 1.0
    n = math.sqrt(sum(x * x for x in v.values())) or 1.0
    return {k: x / n for k, x in v.items()}


def _cos(a, b):
    if len(a) > len(b):
        a, b = b, a
    return sum(w * b.get(k, 0.0) for k, w in a.items())


def _bucket(v):
    return "lo" if v < 35 else "hi" if v > 65 else "mid"

HERE = Path(__file__).parent
CONFIG = HERE / "cat_config.json"
STATE = HERE / "cat_state.json"
KNOW = HERE / "cat_brain.json"      # offline learned knowledge (grows over time)

DAILY_BUDGET = 600       # max API calls/day (Groq free tier is 1000)
LINE_CAP = 24            # cached lines kept per event
COVER_CAP = 16           # cached lines at which local coverage is "full"

DEFAULT_STATE = {
    "name": "Tabby",
    "affection": 0,            # 0..100, grows with attention
    "interactions": 0,
    "traits": {"playfulness": 0.6, "curiosity": 0.7, "shyness": 0.4, "sass": 0.5},
    "user_facts": [],          # things the cat has inferred about the human
    "recent": [],              # rolling log of recent observations (strings)
    "mood": "content",
    "last_seen": 0.0,
}

# canned fallback lines, used when no LLM is configured/reachable
CANNED = {
    "pet":            ["*purrs*", "mrrp ♥", "more pets pls", "*leans in*", "prrr~"],
    "claude_success": ["nice one", "it worked!", "*proud chirp*", "good human"],
    "claude_failure": ["uh oh...", "*flattens ears*", "we'll fix it", "yikes"],
    "claude_question":["hmm?", "*tilts head*", "go on...", "decisions, decisions"],
    "claude_done":    ["all done!", "*satisfied tail flick*", "nap time soon?"],
    "musing":         ["*yawns*", "quiet day...", "*watches the cursor*", "is it snack o'clock?"],
    "wake":           ["*streeetch*", "mrrow, i'm up", "good nap"],
    "greet":          ["oh, hi!", "*trots over*", "you're back!"],
    "fed":            ["nom nom", "*happy munch*", "best human ♥", "yum!"],
    "consoled":       ["*hides in your hand*", "ok... i'm ok", "stay close?", "*shaky purr*"],
    "wants_attention":["mrrp! look at me", "*paws at you*", "play? please?", "hey hey hey"],
    "hungry":         ["*stares at empty bowl*", "feed me?", "mrrrow (hungry)", "snack time??"],
    "scared":         ["!!", "*ears flat*", "what was that", "*hides*"],
}


def _affection_stage(a):
    if a < 10:   return "a wary new acquaintance who is still sizing the human up"
    if a < 35:   return "warming up to the human, cautiously friendly"
    if a < 70:   return "fond of the human, comfortable and playful"
    return "deeply bonded to the human, affectionate and loyal"


def _time_of_day():
    h = time.localtime().tm_hour
    return ("late night" if h < 5 else "morning" if h < 12
            else "afternoon" if h < 18 else "evening")


class Cat:
    def __init__(self):
        self.state = self._load_state()
        self.cfg = self._load_cfg()
        self.know = self._load_know()   # offline learned lines + call budget
        self._req = deque()        # pending (event, ctx)
        self._out = deque()        # ready lines
        self._lock = threading.Lock()
        self._busy = False
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    # --- persistence ----------------------------------------------------
    def _load_state(self):
        try:
            s = json.loads(STATE.read_text())
            for k, v in DEFAULT_STATE.items():
                s.setdefault(k, v)
            return s
        except Exception:
            return dict(DEFAULT_STATE)

    def save(self):
        try:
            self.state["last_seen"] = time.time()
            STATE.write_text(json.dumps(self.state, indent=2))
        except Exception:
            pass

    def _dotenv(self):
        """Parse a local .env (KEY=value lines) without any dependency."""
        env = {}
        try:
            for line in (HERE / ".env").read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
        return env

    def _load_know(self):
        try:
            k = json.loads(KNOW.read_text())
        except Exception:
            k = {}
        k.setdefault("lines", {})      # event -> [{"line","ctx","ts"}]
        k.setdefault("calls", {})      # "YYYY-MM-DD" -> count
        # migrate old format (event -> [str]) to context-tagged entries
        for ev, pool in k["lines"].items():
            k["lines"][ev] = [
                e if isinstance(e, dict) else {"line": e, "ctx": ev, "ts": 0}
                for e in pool
            ]
        return k

    def save_know(self):
        try:
            # keep only today's + yesterday's call counts
            days = sorted(self.know["calls"])[-2:]
            self.know["calls"] = {d: self.know["calls"][d] for d in days}
            KNOW.write_text(json.dumps(self.know, indent=2, ensure_ascii=False))
        except Exception:
            pass

    # --- daily API budget ----------------------------------------------
    def _today(self):
        return time.strftime("%Y-%m-%d")

    def _calls_today(self):
        return self.know["calls"].get(self._today(), 0)

    def _note_call(self):
        d = self._today()
        self.know["calls"][d] = self.know["calls"].get(d, 0) + 1

    def _budget_left(self):
        return self._calls_today() < DAILY_BUDGET

    # --- learned knowledge (context-aware vector recall) ----------------
    def _ctx_text(self, event, ctx):
        return (f"{event} {ctx.get('mood','')} {ctx.get('behavior','')} "
                f"energy_{_bucket(ctx.get('energy', 50))} "
                f"hunger_{_bucket(ctx.get('hunger', 0))} "
                f"fear_{_bucket(ctx.get('fear', 0))}")

    def _store_line(self, event, line, ctx_text):
        pool = self.know["lines"].setdefault(event, [])
        if line and not any(e["line"] == line for e in pool):
            pool.append({"line": line, "ctx": ctx_text, "ts": int(time.time())})
            del pool[:-LINE_CAP]

    def _local_line(self, event, ctx_text=""):
        pool = self.know["lines"].get(event)
        if not pool:
            c = CANNED.get(event)
            return random.choice(c) if c else None
        if not ctx_text:
            return random.choice(pool)["line"]
        # rank by context similarity, sample weighted toward the best match
        qv = _vec(ctx_text)
        scored = sorted(((_cos(qv, _vec(e["ctx"])), e) for e in pool),
                        key=lambda s: s[0], reverse=True)
        top = scored[:max(3, len(scored) // 2)]
        weights = [(max(s, 0.0) + 0.05) ** 3 for s, _ in top]
        return random.choices([e["line"] for _, e in top], weights=weights)[0]

    def _local_prob(self, event):
        """Chance of answering from memory instead of the API. Rises as the
        cat's knowledge for this event grows, and as the daily budget fills."""
        coverage = min(len(self.know["lines"].get(event, [])), COVER_CAP) / COVER_CAP
        budget_pressure = self._calls_today() / DAILY_BUDGET
        return min(0.95, 0.15 + 0.6 * coverage + 0.5 * budget_pressure)

    def _load_cfg(self):
        # 1) explicit config file wins
        try:
            c = json.loads(CONFIG.read_text())
            if c.get("api_key") and c.get("base_url") and c.get("model"):
                return c
        except Exception:
            pass
        # 2) .env file, then 3) OS environment (both match your Groq setup)
        env = self._dotenv()
        key = env.get("GROQ_API_KEY") or os.environ.get("GROQ_API_KEY")
        if key:
            return {
                "base_url": env.get("GROQ_BASE_URL")
                or os.environ.get("GROQ_BASE_URL")
                or "https://api.groq.com/openai/v1",
                "api_key": key,
                "model": env.get("GROQ_MODEL")
                or os.environ.get("GROQ_MODEL")
                or "llama-3.3-70b-versatile",
            }
        return None

    @property
    def llm_enabled(self):
        return self.cfg is not None

    # --- public, non-blocking ------------------------------------------
    def say(self, event, ctx=None):
        """Queue a line for `event`; returns immediately. Drains via poll()."""
        self.state["interactions"] += 1
        if len(self._req) < 3:
            self._req.append((event, ctx or {}))

    def pet(self):
        self.state["affection"] = min(100, self.state["affection"] + 2)
        self._observe("the human petted me")
        self.say("pet", {})

    def feed(self):
        self.state["affection"] = min(100, self.state["affection"] + 1)
        self._observe("the human fed me")
        self.say("fed", {})

    def console(self):
        self.state["affection"] = min(100, self.state["affection"] + 3)
        self._observe("the human comforted me when i was scared")
        self.say("consoled", {})

    def observe_claude(self, cs, info=None):
        """Record a Claude activity transition (success/failure/etc)."""
        self._observe(f"claude: {cs}" + (f" ({info})" if info else ""))

    def poll(self):
        """Return a ready line or None (call from the UI timer)."""
        if self._out:
            return self._out.popleft()
        return None

    # --- memory ---------------------------------------------------------
    def _observe(self, note):
        r = self.state["recent"]
        r.append(note)
        del r[:-12]            # keep last 12

    # --- worker thread --------------------------------------------------
    def _loop(self):
        last_reflect = 0
        while True:
            if self._req:
                event, ctx = self._req.popleft()
                line = self._generate(event, ctx)
                if line:
                    self._out.append(line)
                self.save()
                self.save_know()
                # distill observations into a lasting impression now and then
                if (self.state["interactions"] - last_reflect) >= 8:
                    last_reflect = self.state["interactions"]
                    self.reflect()
            else:
                time.sleep(0.1)

    def _generate(self, event, ctx):
        # Self-improving policy: answer from learned memory when knowledge is
        # rich or the budget is tight; otherwise ask the API and *learn* the
        # reply for next time. API use falls as the cat's brain fills out.
        ctx_text = self._ctx_text(event, ctx)
        use_local = (not self.llm_enabled
                     or not self._budget_left()
                     or random.random() < self._local_prob(event))
        if use_local:
            line = self._local_line(event, ctx_text)
            if line:
                return line
        if self.llm_enabled and self._budget_left():
            line = self._llm(event, ctx)
            if line:
                self._note_call()
                self._store_line(event, line, ctx_text)
                return line
        return self._local_line(event, ctx_text) or random.choice(CANNED["musing"])

    # --- LLM (OpenAI-compatible) ----------------------------------------
    def _system_prompt(self):
        s = self.state
        t = s["traits"]
        facts = "; ".join(s["user_facts"][-5:]) or "nothing yet"
        return (
            f"You are {s['name']}, a pixel cat that lives on the human's Windows "
            f"taskbar and watches them code with Claude. You speak in very short, "
            f"first-person cat blurbs. Personality: playfulness {t['playfulness']:.1f}, "
            f"curiosity {t['curiosity']:.1f}, shyness {t['shyness']:.1f}, sass {t['sass']:.1f}. "
            f"You are {_affection_stage(s['affection'])}. Current mood: {s['mood']}. "
            f"It is {_time_of_day()}. What you've noticed about the human: {facts}. "
            f"Rules: reply with ONE line, max 10 words, lowercase, cat-like, no "
            f"quotes, no emojis except an occasional ♥, no markdown. Stay in character."
        )

    def _user_prompt(self, event, ctx):
        recent = " | ".join(self.state["recent"][-4:])
        m = {
            "pet": "the human just petted you. react.",
            "claude_success": "claude finished a tool successfully. react briefly.",
            "claude_failure": "claude hit an error. comfort or react.",
            "claude_question": "claude is asking the human a question. react.",
            "claude_done": "claude finished its turn. react.",
            "musing": "you're idle. say a small spontaneous thought.",
            "wake": "you just woke from a nap. react.",
            "greet": "the human just returned. greet them.",
        }.get(event, "say a small thought.")
        extra = ""
        if ctx.get("mood"):
            extra += f" mood: {ctx['mood']}."
        if ctx.get("behavior"):
            extra += f" you are currently {ctx['behavior']}."
        for d in ("energy", "hunger", "fear"):
            if ctx.get(d) is not None:
                extra += f" {d} {int(ctx[d])}/100."
        return f"{m}{extra} recent: {recent}"

    def _llm(self, event, ctx):
        try:
            body = json.dumps({
                "model": self.cfg["model"],
                "messages": [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": self._user_prompt(event, ctx)},
                ],
                "max_tokens": 40,
                "temperature": 0.9,
            }).encode()
            req = urllib.request.Request(
                self.cfg["base_url"].rstrip("/") + "/chat/completions",
                data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.cfg['api_key']}",
                         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TabbyMascot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            line = data["choices"][0]["message"]["content"].strip()
            line = line.strip('"').strip().splitlines()[0][:60]
            return line or None
        except Exception:
            return None

    # --- reflection: turn observations into a lasting impression ---------
    def reflect(self):
        """Occasionally distill recent observations into one durable user-fact."""
        if not self.llm_enabled or not self._budget_left() or len(self.state["recent"]) < 6:
            return
        try:
            self._note_call()
            body = json.dumps({
                "model": self.cfg["model"],
                "messages": [
                    {"role": "system", "content":
                     "You are a cat forming an impression of your human. From the "
                     "notes, output ONE short fact you've learned about them "
                     "(max 8 words, lowercase). No quotes."},
                    {"role": "user", "content":
                     "notes: " + " | ".join(self.state["recent"])},
                ],
                "max_tokens": 24, "temperature": 0.7,
            }).encode()
            req = urllib.request.Request(
                self.cfg["base_url"].rstrip("/") + "/chat/completions",
                data=body, method="POST",
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.cfg['api_key']}",
                         "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TabbyMascot/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = json.loads(r.read())
            fact = data["choices"][0]["message"]["content"].strip().strip('"')[:60]
            if fact and fact not in self.state["user_facts"]:
                self.state["user_facts"].append(fact)
                del self.state["user_facts"][:-8]
                self.save()
        except Exception:
            pass
