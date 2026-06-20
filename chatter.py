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


MAX_LINE = 46


def _clean_line(s):
    """Sanitize an LLM/cached line: printable ASCII only (kills mojibake/emoji),
    single line, trimmed, word-bounded length. Returns None if it's junk."""
    if not s:
        return None
    s = s.strip().strip('"').strip("'").strip()
    s = s.splitlines()[0] if s else ""
    s = "".join(ch for ch in s if 32 <= ord(ch) < 127)   # printable ASCII
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > MAX_LINE:                                  # trim at a word break
        s = s[:MAX_LINE].rsplit(" ", 1)[0].strip()
    if len(s) < 2 or not re.search(r"[a-zA-Z]", s):
        return None
    return s


def _similar(line, pool):
    """True if `line` is a near-duplicate of any line already in `pool`."""
    lv = _vec(line)
    for e in pool:
        if _cos(lv, _vec(e["line"])) > 0.8:
            return True
    return False


# --- structured context similarity (M1) -------------------------------------
# Interpretable, collision-free, zero-dep alternative to hashed cosine.
FIELD_WEIGHTS = {"mood": 0.30, "behavior": 0.20, "fear_b": 0.20, "energy_b": 0.15,
                 "hunger_b": 0.15, "affection_tier": 0.10, "daypart": 0.10,
                 "claude_streak": 0.10}
_ORDINAL = {"energy_b", "hunger_b", "fear_b"}
_ORD = {"lo": 0, "mid": 1, "hi": 2}
_WSUM = sum(FIELD_WEIGHTS.values())


def _aff_tier(a):
    return "wary" if a < 10 else "warming" if a < 70 else "bonded"


def _daypart():
    h = time.localtime().tm_hour
    return ("night" if h < 5 else "morning" if h < 12
            else "day" if h < 18 else "evening")


def _field_match(k, a, b):
    if a is None or b is None or a == "" or b == "":
        return 0.0
    if a == b:
        return 1.0
    if k in _ORDINAL and a in _ORD and b in _ORD:
        d = abs(_ORD[a] - _ORD[b])
        return 1.0 if d == 0 else 0.5 if d == 1 else 0.0
    return 0.0


def _struct_sim(a, b):
    """Weighted field-match similarity of two structured contexts, 0..1."""
    if not a or not b:
        return 0.0
    return sum(w * _field_match(k, a.get(k), b.get(k))
               for k, w in FIELD_WEIGHTS.items()) / _WSUM


def _parse_ctx(flat):
    """Recover a structured ctx from a legacy flat ctx string (migration)."""
    toks = (flat or "").split()
    d = {"event": toks[0] if toks else ""}
    rest = []
    for t in toks[1:]:
        if t.startswith(("energy_", "hunger_", "fear_")):
            k, v = t.split("_", 1)
            d[k + "_b"] = v
        else:
            rest.append(t)
    d["mood"] = rest[0] if rest else ""
    d["behavior"] = rest[1] if len(rest) > 1 else ""
    return d

HERE = Path(__file__).parent
CONFIG = HERE / "cat_config.json"
STATE = HERE / "cat_state.json"
KNOW = HERE / "cat_brain.json"      # offline learned knowledge (grows over time)
METRICS = HERE / "cat_metrics.json"  # telemetry (fast-churn, gitignored)

SCHEMA_VERSION = 2
RECALL_MODE = "structured"  # "structured" (default) | "vector" (legacy fallback)

DAILY_BUDGET = 600       # max API calls/day (Groq free tier is 1000)
LINE_CAP = 24            # cached lines kept per event
COVER_CAP = 16           # learned neighbours at which local coverage is "full"
MIN_KEEP = 6             # never evict an event below this many lines
COVER_SIM_MIN = 0.60     # similarity that counts as a covered neighbour (M3)
REWARD_ALPHA = 0.25      # per-line reward EWMA rate (M2)
ANTIREPEAT_K = 8         # session anti-repeat ring size (M5)
FACT_DROP = 0.20         # drop user-facts below this confidence (R1)
FACT_CAP_PER_CAT = 3     # facts kept per category (R1)
FACT_CONF_ALPHA = 0.30   # fact confidence EWMA rate (R1)
FACT_CATS = ("tools", "schedule", "temperament", "style")
REFLECT_BUDGET_PCT = 0.10  # skip reflection once this much budget is spent (R2)
CROSS_EVENT = True         # borrow lines from compatible events when thin (M7)
COMPAT_EVENTS = {          # bidirectional compatible-event groups (M7)
    "musing": ["wake", "greet"], "wake": ["musing"], "greet": ["musing"],
    "claude_success": ["claude_done"], "claude_done": ["claude_success"],
    "wants_attention": ["pet"], "pet": ["consoled"], "consoled": ["pet"],
    "hungry": ["fed"], "fed": ["hungry"], "scared": ["consoled"],
}

DEFAULT_STATE = {
    "schema_version": SCHEMA_VERSION,
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
    "pet":            ["*purrs*", "mrrp <3", "more pets pls", "*leans in*", "prrr~"],
    "claude_success": ["nice one", "it worked!", "*proud chirp*", "good human"],
    "claude_failure": ["uh oh...", "*flattens ears*", "we'll fix it", "yikes"],
    "claude_question":["hmm?", "*tilts head*", "go on...", "decisions, decisions"],
    "claude_done":    ["all done!", "*satisfied tail flick*", "nap time soon?"],
    "musing":         ["*yawns*", "quiet day...", "*watches the cursor*", "is it snack o'clock?"],
    "wake":           ["*streeetch*", "mrrow, i'm up", "good nap"],
    "sleep":          ["*curls up*", "mmm, nap time", "zzz...", "g'night"],
    "greet":          ["oh, hi!", "*trots over*", "you're back!"],
    "fed":            ["nom nom", "*happy munch*", "best human <3", "yum!"],
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
        self._recent_ids = deque(maxlen=ANTIREPEAT_K)   # session anti-repeat (M5)
        self._last_served = None   # (event, line) most recently shown, for reward
        self._metrics = {"served": 0, "local_hits": 0, "api": 0,
                         "reflection": 0, "sim_sum": 0.0, "reward_sum": 0.0,
                         "repeats": 0}
        # one reentrant lock guards state / know / queues. The UI thread and the
        # worker thread both touch them; network calls happen OUTSIDE the lock.
        self._lock = threading.RLock()
        self._worker = threading.Thread(target=self._loop, daemon=True)
        self._worker.start()

    @staticmethod
    def _atomic_write(path, text):
        """Write a file atomically so a crash / concurrent write can't corrupt
        it: write a temp sibling, then os.replace (atomic on the same volume)."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    # --- persistence ----------------------------------------------------
    def _load_state(self):
        try:
            s = json.loads(STATE.read_text(encoding="utf-8"))
            for k, v in DEFAULT_STATE.items():
                s.setdefault(k, v)
            s["schema_version"] = SCHEMA_VERSION
            s["user_facts"] = self._migrate_facts(s.get("user_facts", []))
            return s
        except Exception:
            return dict(DEFAULT_STATE)

    @staticmethod
    def _migrate_facts(facts):
        """v1 flat strings -> v2 confidence records; decay by age, drop weak."""
        now = time.time()
        out = []
        for f in facts:
            if isinstance(f, str):
                cl = _clean_line(f)
                if cl:
                    out.append({"text": cl, "category": "style", "evidence": 1,
                                "confidence": 0.5, "last_seen": now})
            elif isinstance(f, dict) and f.get("text"):
                cl = _clean_line(f["text"])
                if not cl:
                    continue
                f["text"] = cl
                age_days = max(0.0, (now - f.get("last_seen", now)) / 86400)
                f["confidence"] = f.get("confidence", 0.5) * (0.985 ** age_days)
                if f["confidence"] >= FACT_DROP:
                    out.append(f)
        return out

    def _merge_fact(self, category, text):
        """Add or reinforce a confidence-weighted fact (R1). Holds lock."""
        text = _clean_line(text)
        if not text:
            return
        if category not in FACT_CATS:
            category = "style"
        recs = self.state["user_facts"]
        now = time.time()
        for f in recs:
            if f.get("category") == category and _cos(_vec(f["text"]), _vec(text)) > 0.8:
                f["evidence"] = f.get("evidence", 1) + 1
                f["confidence"] = min(1.0, (1 - FACT_CONF_ALPHA) * f.get("confidence", 0.5)
                                      + FACT_CONF_ALPHA)
                f["last_seen"] = now
                if len(text) < len(f["text"]):
                    f["text"] = text
                return
        recs.append({"text": text, "category": category, "evidence": 1,
                     "confidence": 0.5, "last_seen": now})
        cat_recs = [f for f in recs if f.get("category") == category]
        if len(cat_recs) > FACT_CAP_PER_CAT:
            recs.remove(min(cat_recs, key=lambda f: f.get("confidence", 0)))

    def save(self):
        try:
            with self._lock:
                self.state["last_seen"] = time.time()
                data = json.dumps(self.state, indent=2, ensure_ascii=False)
            self._atomic_write(STATE, data)        # file IO outside the lock
        except Exception:
            pass

    def _dotenv(self):
        """Parse a local .env (KEY=value lines) without any dependency."""
        env = {}
        try:
            for line in (HERE / ".env").read_text(encoding="utf-8").splitlines():
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
            k = json.loads(KNOW.read_text(encoding="utf-8"))
        except Exception:
            k = {}
        k.setdefault("lines", {})      # event -> [entry]
        k.setdefault("calls", {})      # "YYYY-MM-DD" -> count
        k["schema_version"] = SCHEMA_VERSION
        # migrate old format + self-heal: clean each line (strips mojibake),
        # add v2 fields (reward/uses/last_used/cstruct), drop junk + near-dups.
        for ev, pool in list(k["lines"].items()):
            healed = []
            for e in pool:
                if not isinstance(e, dict):
                    e = {"line": e, "ctx": ev, "ts": 0}
                cl = _clean_line(e.get("line"))
                if not cl:
                    continue
                e["line"] = cl
                e.setdefault("ctx", ev)
                e.setdefault("ts", 0)
                e.setdefault("reward", 0.5)         # v2: line quality (M2)
                e.setdefault("uses", 0)
                e.setdefault("last_used", 0)
                if not e.get("cstruct"):            # v2: structured ctx (M1)
                    e["cstruct"] = _parse_ctx(e["ctx"])
                if any(x["line"] == cl for x in healed) or _similar(cl, healed):
                    continue
                healed.append(e)
            k["lines"][ev] = healed[-LINE_CAP:]
        return k

    def save_know(self):
        try:
            with self._lock:
                # keep only today's + yesterday's call counts
                days = sorted(self.know["calls"])[-2:]
                self.know["calls"] = {d: self.know["calls"][d] for d in days}
                data = json.dumps(self.know, indent=2, ensure_ascii=False)
            self._atomic_write(KNOW, data)         # file IO outside the lock
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

    # --- learned knowledge (structured, reward-weighted recall) ---------
    # all methods here assume the caller holds self._lock
    def _ctx_text(self, event, ctx):
        """Legacy flat ctx string (kept for vector mode + as a stored tag)."""
        return (f"{event} {ctx.get('mood','')} {ctx.get('behavior','')} "
                f"energy_{_bucket(ctx.get('energy', 50))} "
                f"hunger_{_bucket(ctx.get('hunger', 0))} "
                f"fear_{_bucket(ctx.get('fear', 0))}")

    def _struct(self, event, ctx):
        """Typed context dict used by structured similarity (M1)."""
        recent = self.state.get("recent", [])[-6:]
        fails = sum(1 for r in recent if "tool_failure" in r)
        succ = sum(1 for r in recent if "tool_success" in r)
        streak = ("flaky" if fails >= 2 else
                  "productive" if (succ >= 2 and fails == 0) else "calm")
        return {"event": event, "mood": ctx.get("mood", ""),
                "behavior": ctx.get("behavior", ""),
                "energy_b": _bucket(ctx.get("energy", 50)),
                "hunger_b": _bucket(ctx.get("hunger", 0)),
                "fear_b": _bucket(ctx.get("fear", 0)),
                "affection_tier": _aff_tier(self.state.get("affection", 0)),
                "daypart": _daypart(), "claude_streak": streak}

    def _entry_sim(self, cstruct, qflat, e):
        if RECALL_MODE == "vector":
            return _cos(_vec(qflat), _vec(e.get("ctx", "")))
        return _struct_sim(cstruct, e.get("cstruct") or _parse_ctx(e.get("ctx", "")))

    def _store_line(self, event, line, qflat, cstruct):
        line = _clean_line(line)
        if not line:
            return
        pool = self.know["lines"].setdefault(event, [])
        if any(e["line"] == line for e in pool) or _similar(line, pool):
            return                                  # skip exact + near duplicates
        pool.append({"line": line, "ctx": qflat, "cstruct": cstruct,
                     "ts": int(time.time()), "reward": 0.5,
                     "uses": 0, "last_used": 0})
        self._evict(pool)

    def _evict(self, pool):
        """Drop the WORST line(s) over LINE_CAP by quality + diversity + recency
        (M4), never below MIN_KEEP. Keeps high-reward, distinct lines."""
        if len(pool) <= LINE_CAP:
            return
        lasts = [e.get("last_used", 0) for e in pool]
        lo, span = min(lasts), (max(lasts) - min(lasts)) or 1

        def keep_score(e):
            others = [x for x in pool if x is not e]
            div = (sum(1 - _cos(_vec(e["line"]), _vec(x["line"])) for x in others)
                   / len(others)) if others else 1.0
            rec = (e.get("last_used", 0) - lo) / span
            return 0.5 * e.get("reward", 0.5) + 0.3 * div + 0.2 * rec

        while len(pool) > LINE_CAP and len(pool) > MIN_KEEP:
            pool.remove(min(pool, key=keep_score))

    def _ctx_coverage(self, event, cstruct, qflat):
        """How well the CURRENT context is covered (M3), not raw line count."""
        pool = self.know["lines"].get(event, [])
        n = sum(1 for e in pool if self._entry_sim(cstruct, qflat, e) >= COVER_SIM_MIN)
        return min(n, COVER_CAP) / COVER_CAP

    def _local_prob_ctx(self, event, cstruct, qflat):
        cov = self._ctx_coverage(event, cstruct, qflat)
        bp = self._calls_today() / DAILY_BUDGET
        return max(0.0, min(0.95, 0.10 + 0.55 * cov + 0.45 * bp))

    def _candidates(self, event, cstruct, qflat):
        """Own-event lines (weight 1.0) plus, when the current context is thinly
        covered, lines borrowed from compatible events at weight 0.5 (M7)."""
        cands = [(e, 1.0) for e in self.know["lines"].get(event, [])]
        if CROSS_EVENT:
            own = self.know["lines"].get(event, [])
            neigh = sum(1 for e in own
                        if self._entry_sim(cstruct, qflat, e) >= COVER_SIM_MIN)
            if neigh < MIN_KEEP:
                for ev2 in COMPAT_EVENTS.get(event, []):
                    cands += [(e, 0.5) for e in self.know["lines"].get(ev2, [])]
        return cands

    def _recall(self, event, cstruct, qflat):
        """Return (line, (sim, entry)) or (canned_line, None) or (None, None)."""
        cands = self._candidates(event, cstruct, qflat)
        if not cands:
            c = CANNED.get(event)
            return (random.choice(c), None) if c else (None, None)
        scored = []
        for e, mult in cands:
            sim = self._entry_sim(cstruct, qflat, e)
            anti = 0.15 if e["line"] in self._recent_ids else 1.0   # M5
            w = (max(sim, 0.0) + 0.05) ** 3 * (0.5 + e.get("reward", 0.5)) * anti * mult
            scored.append((w, sim, e))
        scored.sort(key=lambda s: s[0], reverse=True)
        top = scored[:max(3, len(scored) // 2)]
        idx = random.choices(range(len(top)), weights=[w for w, _, _ in top])[0]
        _, sim, e = top[idx]
        return e["line"], (sim, e)

    def _serve(self, event, res, local=False, api=False):
        """Record a served line: metrics, anti-repeat ring, reward target."""
        line, info = res
        m = self._metrics
        m["served"] += 1
        if api:
            m["api"] += 1
        if local:
            m["local_hits"] += 1
        if info and info[1] is not None:
            sim, entry = info
            entry["uses"] = entry.get("uses", 0) + 1
            entry["last_used"] = int(time.time())
            self._last_served = (event, entry)
            if local:                       # only real recalls count toward sim/reward
                m["sim_sum"] += sim
                m["reward_sum"] += entry.get("reward", 0.5)
        else:
            self._last_served = (event, None)
        if line in self._recent_ids:
            m["repeats"] += 1
        self._recent_ids.append(line)

    def _find_entry(self, event, line):
        for e in self.know["lines"].get(event, []):
            if e["line"] == line:
                return e
        return None

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
        with self._lock:
            self.state["interactions"] += 1
            if len(self._req) < 3:
                self._req.append((event, ctx or {}))

    def pet(self):
        with self._lock:
            self.state["affection"] = min(100, self.state["affection"] + 2)
            self._observe("the human petted me")
        self.say("pet", {})

    def feed(self):
        with self._lock:
            self.state["affection"] = min(100, self.state["affection"] + 1)
            self._observe("the human fed me")
        self.say("fed", {})

    def console(self):
        with self._lock:
            self.state["affection"] = min(100, self.state["affection"] + 3)
            self._observe("the human comforted me when i was scared")
        self.say("consoled", {})

    def observe_claude(self, cs, info=None):
        """Record a Claude activity transition (success/failure/etc)."""
        with self._lock:
            self._observe(f"claude: {cs}" + (f" ({info})" if info else ""))

    def affection(self):
        with self._lock:
            return self.state.get("affection", 0)

    def set_drives(self, drives):
        """Persist the brain's numeric drives so they survive a restart."""
        with self._lock:
            self.state["drives"] = drives

    def get_drives(self):
        with self._lock:
            return dict(self.state.get("drives") or {})

    def poll(self):
        """Return a ready line or None (call from the UI timer)."""
        with self._lock:
            return self._out.popleft() if self._out else None

    # --- memory ---------------------------------------------------------
    def _observe(self, note):
        # caller must hold self._lock
        r = self.state["recent"]
        r.append(note)
        del r[:-12]            # keep last 12

    # --- worker thread --------------------------------------------------
    def _loop(self):
        # seed from persisted count so we don't reflect on the very first
        # interaction after a restart
        with self._lock:
            last_reflect = self.state["interactions"]
        while True:
            with self._lock:
                item = self._req.popleft() if self._req else None
            if item is None:
                time.sleep(0.1)
                continue
            event, ctx = item
            line = self._generate(event, ctx)
            if line:
                with self._lock:
                    self._out.append(line)
            self.save()
            self.save_know()
            self._save_metrics()
            with self._lock:
                due = (self.state["interactions"] - last_reflect) >= 8
                if due:
                    last_reflect = self.state["interactions"]
            if due:
                self.reflect()

    def _generate(self, event, ctx):
        # Self-improving policy: answer from learned memory when the CURRENT
        # context is well covered or the budget is tight; otherwise ask the API
        # and learn the reply (filling the gap). API use falls as the brain fills.
        qflat = self._ctx_text(event, ctx)
        with self._lock:
            cstruct = self._struct(event, ctx)
            enabled = self.llm_enabled
            budget = self._budget_left()
            use_local = (not enabled or not budget
                         or random.random() < self._local_prob_ctx(event, cstruct, qflat))
            if use_local:
                res = self._recall(event, cstruct, qflat)
                if res[0]:
                    self._serve(event, res, local=True)
                    return res[0]
        if enabled and budget:
            line = self._llm(event, ctx)               # network, no lock held
            if line:
                with self._lock:
                    self._note_call()
                    self._store_line(event, line, qflat, cstruct)
                    entry = self._find_entry(event, line)
                    self._serve(event, (line, (1.0, entry) if entry else None),
                                api=True)
                return line
        with self._lock:
            res = self._recall(event, cstruct, qflat)
            if res[0]:
                self._serve(event, res, local=True)
                return res[0]
            return random.choice(CANNED["musing"])

    def report_outcome(self, signal):
        """The mascot reports how a shown line landed (1.0 good .. 0.0 bad);
        nudges that line's reward via EWMA so memory improves in quality (M2)."""
        with self._lock:
            if not self._last_served:
                return
            _, entry = self._last_served
            self._last_served = None
            if entry is None:
                return
            entry["reward"] = ((1 - REWARD_ALPHA) * entry.get("reward", 0.5)
                               + REWARD_ALPHA * max(0.0, min(1.0, signal)))

    def _save_metrics(self):
        """Append today's telemetry to cat_metrics.json (worker thread only)."""
        try:
            with self._lock:
                m = dict(self._metrics)
            served = max(m["served"], 1)
            hits = max(m["local_hits"], 1)
            try:
                allm = json.loads(METRICS.read_text(encoding="utf-8"))
            except Exception:
                allm = {}
            allm[self._today()] = {
                "served": m["served"], "api": m["api"],
                "reflection": m["reflection"], "local_hits": m["local_hits"],
                "local_hit_rate": round(m["local_hits"] / served, 3),
                "avg_served_sim": round(m["sim_sum"] / hits, 3),
                "mean_served_reward": round(m["reward_sum"] / hits, 3),
                "repeat_rate": round(m["repeats"] / served, 3),
            }
            self._atomic_write(METRICS, json.dumps(allm, indent=2))
        except Exception:
            pass

    # --- LLM (OpenAI-compatible) ----------------------------------------
    def _system_prompt(self):
        s = self.state
        t = s["traits"]
        recs = sorted(s.get("user_facts", []),
                      key=lambda f: f.get("confidence", 0) if isinstance(f, dict) else 0,
                      reverse=True)
        texts = [f["text"] for f in recs[:5] if isinstance(f, dict)]
        facts = "; ".join(texts) or "nothing yet"
        return (
            f"You are {s['name']}, a pixel cat that lives on the human's Windows "
            f"taskbar and watches them code with Claude. You speak in very short, "
            f"first-person cat blurbs. Personality: playfulness {t['playfulness']:.1f}, "
            f"curiosity {t['curiosity']:.1f}, shyness {t['shyness']:.1f}, sass {t['sass']:.1f}. "
            f"You are {_affection_stage(s['affection'])}. Current mood: {s['mood']}. "
            f"It is {_time_of_day()}. What you've noticed about the human: {facts}. "
            f"Rules: reply with ONE line, max 10 words, lowercase, cat-like, "
            f"plain ASCII text only, no quotes, no emojis, no markdown. Stay in character."
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
            "sleep": "you're being told to nap. settle down sleepily.",
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
        # M6: show your best past lines for this event as voice examples
        pool = self.know["lines"].get(event, [])
        best = sorted(pool, key=lambda e: e.get("reward", 0.5), reverse=True)[:3]
        voice = ""
        if best:
            voice = " your voice (don't copy): " + " / ".join(e["line"] for e in best)
        return (f"{m}{extra}{voice} recent: {recent}. "
                f"reply in <= {MAX_LINE} chars, plain ascii, no quotes.")

    def _post_chat(self, messages, max_tokens, temperature):
        """POST to the OpenAI-compatible endpoint. cfg read under lock; the
        network call itself runs without the lock held."""
        with self._lock:
            cfg = dict(self.cfg)
        body = json.dumps({"model": cfg["model"], "messages": messages,
                           "max_tokens": max_tokens, "temperature": temperature}).encode()
        req = urllib.request.Request(
            cfg["base_url"].rstrip("/") + "/chat/completions",
            data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg['api_key']}",
                     "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) TabbyMascot/1.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read())
        return data["choices"][0]["message"]["content"]

    def _llm(self, event, ctx):
        try:
            with self._lock:                            # build prompts under lock
                messages = [
                    {"role": "system", "content": self._system_prompt()},
                    {"role": "user", "content": self._user_prompt(event, ctx)},
                ]
            line = self._post_chat(messages, 40, 0.9)   # network unlocked
            return _clean_line(line)
        except Exception:
            return None

    # --- reflection: turn observations into a lasting impression ---------
    def reflect(self):
        """Occasionally distill recent observations into one durable user-fact."""
        with self._lock:
            # R2: don't let reflection eat scarce budget
            if (not self.llm_enabled or not self._budget_left()
                    or self._calls_today() / DAILY_BUDGET > (1 - REFLECT_BUDGET_PCT)
                    or len(self.state["recent"]) < 6):
                return
            self._note_call()
            self._metrics["reflection"] += 1
            notes = " | ".join(self.state["recent"])
        try:
            messages = [
                {"role": "system", "content":
                 "You are a cat forming an impression of your human. From the "
                 "notes, output ONE line as 'category|fact' where category is one "
                 "of tools, schedule, temperament, style. fact is max 8 words, "
                 "lowercase, plain ascii, no quotes."},
                {"role": "user", "content": "notes: " + notes},
            ]
            out = self._post_chat(messages, 24, 0.7)
        except Exception:
            return
        cat, sep, txt = out.partition("|")
        category = cat.strip().lower() if sep else "style"
        fact = _clean_line(txt if sep else out)
        if fact:
            with self._lock:
                self._merge_fact(category, fact)
            self.save()
