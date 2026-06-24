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


def _clean_q(s, maxlen=90):
    """Like _clean_line but keeps a longer span (questions/labels run longer than
    the 46-char speech cap). Printable ASCII, single line, must contain letters."""
    if not s:
        return None
    s = s.strip().strip('"').strip("'").strip()
    s = s.splitlines()[0] if s else ""
    s = "".join(ch for ch in s if 32 <= ord(ch) < 127)
    s = re.sub(r"\s+", " ", s).strip()
    if len(s) > maxlen:
        s = s[:maxlen].rsplit(" ", 1)[0].strip()
    if len(s) < 3 or not re.search(r"[a-zA-Z]", s):
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
QLIB = HERE / "questions_library.json"      # large tagged question bank (tracked)
QLEARNED = HERE / "learned_questions.json"  # API questions kept for offline reuse
TELEMETRY = HERE / "cat_telemetry.jsonl"    # Phase 6 behaviour log (gitignored)
TELEMETRY_MAX_BYTES = 2_000_000             # rotate the active log past ~2 MB
TELEMETRY_KEEP = 14                         # rotated daily-ish files to retain

SCHEMA_VERSION = 5          # v5: tiredness drive + knowledge_bond (quiz-based)
RECALL_MODE = "structured"  # "structured" (default) | "vector" (legacy fallback)

# --- network health (Phase 1): stop hammering a dead endpoint -----------------
NET_FAIL_MAX = 3            # consecutive failures before we declare "offline"
NET_PROBE_COOLDOWN = 120    # seconds offline before a single re-probe is allowed

# --- bond decay on neglect (Phase 1b): affection cools if she's ignored -------
# Wall-clock decay (computed on load + on the persist tick) so it works across
# restarts and while the app is closed. Loyalty-damped + floored so a long-bonded
# cat cools and gets wary but never forgets you.
BOND_GRACE_H = 10.0         # hours of no attention before decay starts
BOND_DECAY_PER_DAY = 4.0    # affection lost per neglected day past the grace window
BOND_FLOOR = 25             # affection never decays below this floor
BOND_LOYALTY_DAMP = 0.30    # higher tiers cool slower: rate * (1 - DAMP * tier_frac)

DAILY_BUDGET = 600       # max API calls/day (Groq free tier is 1000)
LINE_CAP = 24            # cached lines kept per event
COVER_CAP = 16           # learned neighbours at which local coverage is "full"
MIN_KEEP = 6             # never evict an event below this many lines
COVER_SIM_MIN = 0.50     # similarity that counts as a covered neighbour (M3)
REWARD_ALPHA = 0.25      # per-line reward EWMA rate (M2)
ANTIREPEAT_K = 16        # session anti-repeat ring size (M5)
REPEAT_PENALTY = 0.10    # EWMA nudge toward 0.3 when a served line repeats (M8)
# Once an event's pool fills, accumulated memory should actually get used —
# without this the policy stayed API-bound because per-context coverage alone
# rarely crossed threshold. Maturity scales local recall up as the pool fills (M8).
MATURE_WEIGHT = 0.30     # weight of pool-maturity in local_prob (M8)
# Low-stakes, repetitive chatter doesn't need freshness — recall locally, save API.
LOCAL_FLOOR = {"musing": 0.6, "greet": 0.6, "wake": 0.5, "sleep": 0.5}
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
    "user_profile": {},        # q_id -> {q, a, ts}: answers to her quiz (C)
    "quiz": {"last_t": 0, "asked": [], "recent_q": []},   # quiz bookkeeping (C)
    "recent": [],              # rolling log of recent observations (strings)
    "mood": "content",
    "last_seen": 0.0,          # rewritten every save (liveness, NOT neglect)
    "last_attention": 0.0,     # set ONLY on real attention; drives bond decay (1b)
}

# --- get-to-know-you quiz (C): she asks, you click, she adapts ---------------
QUIZ_COOLDOWN = 3 * 3600     # seconds between questions (don't nag)
QUIZ_MAX = 211               # store answers up to the full library size
QUIZ_LIBRARY_SIZE = 211      # total questions in questions_library.json
TRAIT_NUDGE = 0.08           # how far one answer shifts a personality trait
# Structured preference dimensions a tagged answer can teach (Phase 3). The first
# four are the legacy keyword-extracted set; the rest are new and library-only.
# behavior_hints() surfaces all of them; the brain consumes what it understands
# (comfort_style today) and ignores the rest — adding keys here is safe.
PREF_KEYS = ("chattiness", "comfort_style", "chronotype", "pace",
             "social_energy", "humor", "risk", "structure",
             "feedback_style", "focus_style", "reward", "aesthetics")
# Static fallback bank (used offline / when the API question can't be parsed).
# `traits` on an option nudges her own personality toward complementing you.
QUESTIONS = [
    {"id": "chatty", "text": "want me chatty or chill while you work?",
     "options": [{"label": "chatty", "traits": {"playfulness": +1}},
                 {"label": "chill", "traits": {"shyness": +1}}]},
    {"id": "humor", "text": "you like me sweet or a little sassy?",
     "options": [{"label": "sweet", "traits": {"sass": -1}},
                 {"label": "sassy", "traits": {"sass": +1}}]},
    {"id": "night", "text": "night owl or early bird?",
     "options": [{"label": "night owl"}, {"label": "early bird"}]},
    {"id": "cheer", "text": "when code breaks, want cheering or quiet?",
     "options": [{"label": "cheer me up", "traits": {"playfulness": +1}},
                 {"label": "give me space", "traits": {"shyness": +1}}]},
    {"id": "pace", "text": "do you like fast sprints or slow and steady?",
     "options": [{"label": "fast sprints", "traits": {"curiosity": +1}},
                 {"label": "slow + steady"}]},
    {"id": "pet", "text": "more of a cat person or dog person?",
     "options": [{"label": "cats, obviously", "traits": {"sass": +1}},
                 {"label": "dogs"}]},
]


def _pref_extract(txt):
    """Best-effort, intentionally lossy keyword map of one quiz text -> partial
    prefs (X1). LLM-generated questions have arbitrary ids, so we key on the
    text, not the id. Returns only the fields a keyword matched."""
    d = {}
    if "chill" in txt:
        d["chattiness"] = -1.0
    elif "chatty" in txt:
        d["chattiness"] = +1.0
    if "space" in txt or "quiet" in txt:
        d["comfort_style"] = "space"
    elif "cheer" in txt:
        d["comfort_style"] = "cheer"
    if "night" in txt:
        d["chronotype"] = "night"
    elif "early" in txt or "bird" in txt:
        d["chronotype"] = "early"
    if "fast" in txt or "sprint" in txt:
        d["pace"] = "fast"
    elif "slow" in txt or "steady" in txt:
        d["pace"] = "slow"
    return d

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


AFFECTION_MAX = 200          # bond keeps growing past the old 100 cap (prestige)


def _affection_stage(a):
    if a < 10:    return "a wary new acquaintance who is still sizing the human up"
    if a < 35:    return "warming up to the human, cautiously friendly"
    if a < 70:    return "fond of the human, comfortable and playful"
    if a < 100:   return "deeply bonded to the human, affectionate and loyal"
    if a < 150:   return "devoted to the human, who is her favourite person"
    return "inseparable from the human, utterly at home and adoring"


def _time_of_day():
    h = time.localtime().tm_hour
    return ("late night" if h < 5 else "morning" if h < 12
            else "afternoon" if h < 18 else "evening")


class Cat:
    def __init__(self):
        self.state = self._load_state()
        self.cfg = self._load_cfg()
        self.know = self._load_know()   # offline learned lines + call budget
        self.qlib = self._load_qlib()   # large tagged question bank (Phase 3)
        self._req = deque()        # pending (event, ctx)
        self._out = deque()        # ready lines
        self._recent_ids = deque(maxlen=ANTIREPEAT_K)   # session anti-repeat (M5)
        self._last_served = None   # (event, line) most recently shown, for reward
        self._q_ready = None       # a built quiz question waiting for the UI (C)
        self._q_pending = False    # a quiz question is being built on the worker (C)
        # network health (Phase 1): a single online flag, flipped at the one
        # network choke point, so we stop hammering a dead endpoint deliberately.
        self.online = True
        self._net_fails = 0
        self._net_probe_at = 0.0
        # telemetry event sink (Phase 0): bounded in-memory ring for now; the
        # Phase-6 monitor will swap the body of log_event() for a JSONL append.
        self._events = deque(maxlen=256)
        self._tlog_lock = threading.Lock()   # guards the JSONL append (Phase 6)
        self._metrics = {"served": 0, "local_hits": 0, "api": 0,
                         "reflection": 0, "sim_sum": 0.0, "reward_sum": 0.0,
                         "repeats": 0, "q_local": 0, "q_api": 0, "offline_flips": 0}
        # one reentrant lock guards state / know / queues. The UI thread and the
        # worker thread both touch them; network calls happen OUTSIDE the lock.
        # Created before any locked helper runs (e.g. _decay_bond below).
        self._lock = threading.RLock()
        # bond decay catch-up for time elapsed while the app was closed (1b)
        self._decay_bond(persist=False)
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

    # --- telemetry event sink (Phase 0) --------------------------------
    def log_event(self, kind, **fields):
        """Record one telemetry event: append to a bounded in-memory ring AND to a
        rotating JSONL on disk so the corpus survives restarts (Phase 6). Must never
        raise into a caller — it's wired into hot paths (bond/fear/net/behaviour).
        Events are infrequent (transitions, not per-tick), so a plain append under a
        short lock is cheap. See docs/MONITORING.md for the field contract."""
        try:
            rec = {"ts": int(time.time()), "kind": kind}
            rec.update(fields)
            self._events.append(rec)
            line = json.dumps(rec, ensure_ascii=False)
        except Exception:
            return
        try:
            with self._tlog_lock:
                try:
                    if TELEMETRY.exists() and TELEMETRY.stat().st_size > TELEMETRY_MAX_BYTES:
                        self._rotate_telemetry()
                except OSError:
                    pass
                with open(TELEMETRY, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    def _rotate_telemetry(self):
        """Rename the active log aside and prune old rotations. Caller holds
        _tlog_lock. Best-effort; never raises. Rotated files are
        cat_telemetry.<stamp>.jsonl (the glob deliberately skips the active file,
        which has no <stamp> segment)."""
        try:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            TELEMETRY.replace(TELEMETRY.with_name(f"cat_telemetry.{stamp}.jsonl"))
        except OSError:
            return
        try:
            rotated = sorted(HERE.glob("cat_telemetry.*.jsonl"))
            for old in rotated[:-TELEMETRY_KEEP]:
                try:
                    old.unlink()
                except OSError:
                    pass
        except OSError:
            pass

    # --- bond decay on neglect (Phase 1b) ------------------------------
    def _touch_attention(self):
        """Mark a genuine attention event (pet/feed/console/quiz answer). Caller
        holds the lock. This is the ONLY writer of last_attention — save() must
        not touch it, or neglect can never be measured."""
        self.state["last_attention"] = time.time()

    def _decay_bond(self, persist=True):
        """Cool affection toward BOND_FLOOR after a grace window of no attention.
        Wall-clock based (works across restarts / while closed), loyalty-damped so
        higher tiers cool slower. Idempotent: advances last_attention by the time
        it just charged decay for, so repeated calls don't double-count."""
        try:
            with self._lock:
                now = time.time()
                la = float(self.state.get("last_attention", 0.0) or 0.0)
                if la <= 0.0:                      # first run / migration: seed, no decay
                    self.state["last_attention"] = now
                    return
                idle_h = (now - la) / 3600.0
                if idle_h <= BOND_GRACE_H:
                    return
                aff = float(self.state.get("affection", 0))
                if aff <= BOND_FLOOR:
                    self.state["last_attention"] = now   # nothing to lose; reset clock
                    return
                neglected_days = (idle_h - BOND_GRACE_H) / 24.0
                tier_frac = min(1.0, aff / AFFECTION_MAX)        # loyalty damping
                rate = BOND_DECAY_PER_DAY * (1.0 - BOND_LOYALTY_DAMP * tier_frac)
                new_aff = max(BOND_FLOOR, aff - rate * neglected_days)
                delta = new_aff - aff
                if delta < 0:
                    self.state["affection"] = new_aff
                    self.state["last_attention"] = now   # charged up to now
                    self.log_event("bond", affection=round(new_aff, 1),
                                   delta=round(delta, 2), reason="decay")
            if delta < 0 and persist:
                self.save()
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

    # --- tagged question bank (Phase 3) --------------------------------
    @staticmethod
    def _norm_q(q):
        """Validate + normalise one library question dict. Returns a clean copy or
        None. Tolerates partial/malformed entries (self-heal, like the line pool)."""
        if not isinstance(q, dict):
            return None
        text = _clean_q(q.get("text"))
        opts = []
        for o in (q.get("options") or []):
            if not isinstance(o, dict):
                continue
            lab = _clean_q(o.get("label"), 24)
            if not lab:
                continue
            co = {"label": lab}
            tr = o.get("traits")
            if isinstance(tr, dict):
                co["traits"] = {k: v for k, v in tr.items()
                                if k in ("playfulness", "curiosity", "shyness", "sass")}
            pr = o.get("prefs")
            if isinstance(pr, dict):
                co["prefs"] = {k: v for k, v in pr.items() if k in PREF_KEYS}
            fact = _clean_line(o.get("fact")) if o.get("fact") else None
            if fact:
                co["fact"] = fact
            opts.append(co)
        if not text or len(opts) < 2:
            return None
        qid = str(q.get("id") or "lib_" + str(abs(zlib.crc32(text.encode()))))
        return {"id": qid, "text": text, "dim": str(q.get("dim", "")),
                "options": opts[:3]}

    def _load_qlib(self):
        """Load the tagged question bank: the shipped library plus any API-grown
        questions kept for offline reuse. Deduped by normalised text. Empty/missing
        files are fine — she just falls back to the inline QUESTIONS bank."""
        out, seen = [], set()
        for path in (QLIB, QLEARNED):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            items = raw.get("questions", raw) if isinstance(raw, dict) else raw
            if not isinstance(items, list):
                continue
            for q in items:
                nq = self._norm_q(q)
                if not nq:
                    continue
                key = re.sub(r"[^a-z0-9]+", " ", nq["text"].lower()).strip()
                if key in seen:
                    continue
                seen.add(key)
                out.append(nq)
        return out

    def _append_learned_q(self, q):
        """Persist an answered API question to learned_questions.json for offline
        reuse (deduped by normalised text, capped). Caller need not hold the lock;
        file IO is atomic. Best-effort — never raises into the worker."""
        try:
            nq = self._norm_q(q)
            if not nq:
                return
            try:
                raw = json.loads(QLEARNED.read_text(encoding="utf-8"))
                items = raw.get("questions", []) if isinstance(raw, dict) else raw
            except Exception:
                items = []
            if not isinstance(items, list):
                items = []
            key = re.sub(r"[^a-z0-9]+", " ", nq["text"].lower()).strip()
            for ex in items:
                exk = re.sub(r"[^a-z0-9]+", " ",
                             str((ex or {}).get("text", "")).lower()).strip()
                if exk == key:
                    return                         # already have it
            items.append(nq)
            items = items[-300:]                   # cap growth
            self._atomic_write(QLEARNED, json.dumps({"version": 1, "questions": items},
                                                    indent=2, ensure_ascii=False))
            with self._lock:                       # make it usable offline now
                self.qlib.append(nq)
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
        pool = self.know["lines"].get(event, [])
        mature = min(len(pool), LINE_CAP) / LINE_CAP        # M8: how full this event is
        base = 0.10 + 0.45 * cov + MATURE_WEIGHT * mature + 0.30 * bp
        return max(LOCAL_FLOOR.get(event, 0.0), min(0.95, base))

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
            if info and info[1] is not None:               # M8: over-served lines
                e = info[1]                                # lose reward so eviction
                e["reward"] = ((1 - REPEAT_PENALTY) * e.get("reward", 0.5)
                               + REPEAT_PENALTY * 0.3)      # eventually drops them
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

    def _gain_affection(self, amount, reason):
        """Raise affection (capped), reset the neglect clock, log a bond event.
        Caller holds the lock."""
        before = self.state["affection"]
        self.state["affection"] = min(AFFECTION_MAX, before + amount)
        self._touch_attention()
        self.log_event("bond", affection=round(self.state["affection"], 1),
                       delta=round(self.state["affection"] - before, 2), reason=reason)

    def pet(self):
        with self._lock:
            self._gain_affection(2, "pet")
            self._observe("the human petted me")
        self.say("pet", {})

    def feed(self):
        with self._lock:
            self._gain_affection(1, "feed")
            self._observe("the human fed me")
        self.say("fed", {})

    def console(self):
        with self._lock:
            self._gain_affection(3, "console")
            self._observe("the human comforted me when i was scared")
        self.say("consoled", {})

    def observe_claude(self, cs, info=None):
        """Record a Claude activity transition (success/failure/etc)."""
        with self._lock:
            self._observe(f"claude: {cs}" + (f" ({info})" if info else ""))

    def affection(self):
        with self._lock:
            return self.state.get("affection", 0)

    def knowledge_bond(self):
        """Bond 0–100 based on how many questions the cat has asked the user.
        Grows as she learns more: 21 questions ≈ 10%, 106 ≈ 50%, 211 = 100%."""
        with self._lock:
            asked = len(self.state.get("quiz", {}).get("asked", []))
        return min(100, round(asked / QUIZ_LIBRARY_SIZE * 100))

    def idle_hours(self):
        """Hours since the last genuine attention event (pet/feed/console/quiz).
        Drives bond decay and the Phase-4 neglect trigger. 0 if never recorded."""
        with self._lock:
            la = float(self.state.get("last_attention", 0.0) or 0.0)
        return 0.0 if la <= 0.0 else max(0.0, (time.time() - la) / 3600.0)

    def set_drives(self, drives):
        """Persist the brain's numeric drives so they survive a restart."""
        with self._lock:
            self.state["drives"] = drives

    def set_mood(self, mood):
        """Mirror the brain's live mood into the persisted state. Without this the
        top-level `mood` field stayed at its "content" default forever, so the
        dashboard, the LLM voice prompt, and memory recall (FIELD_WEIGHTS["mood"]
        = 0.30) all keyed off a dead constant. Now it tracks the real drive state."""
        if mood:
            with self._lock:
                self.state["mood"] = mood

    def get_drives(self):
        with self._lock:
            return dict(self.state.get("drives") or {})

    def poll(self):
        """Return a ready line or None (call from the UI timer)."""
        with self._lock:
            return self._out.popleft() if self._out else None

    # --- get-to-know-you quiz (C) --------------------------------------
    def maybe_ask(self):
        """UI calls this when idle. If it's time, enqueue a question for the
        worker to build (API-generated for freshness, static-bank fallback).
        Non-blocking; retrieve the result with poll_question()."""
        with self._lock:
            if self._q_ready or self._q_pending:
                return
            if len(self.state.get("user_profile", {})) >= QUIZ_MAX:
                return
            qz = self.state.setdefault("quiz", {"last_t": 0, "asked": [], "recent_q": []})
            if time.time() - qz.get("last_t", 0) < QUIZ_COOLDOWN:
                return
            self._q_pending = True
            self._req.append(("__quiz__", {}))

    def ask_now(self):
        """Force a question regardless of cooldown/affection (manual trigger from
        the menu). Still skips if one is already pending or waiting."""
        with self._lock:
            if self._q_ready or self._q_pending:
                return
            self._q_pending = True
            self._req.append(("__quiz__", {}))

    def poll_question(self):
        """Return a built question dict {id, text, options:[{label,..}]} or None."""
        with self._lock:
            q, self._q_ready = self._q_ready, None
            return q

    def _pick_library_q(self, asked):
        """An unused question from the tagged library (Phase 3), else the inline
        QUESTIONS stub. Tracks usage by id via the quiz `asked` set + the
        user_profile keys, so nothing repeats until the bank is exhausted."""
        unused = [q for q in self.qlib if q["id"] not in asked]
        pool = unused or self.qlib or [
            {"id": x["id"], "text": x["text"], "dim": "", "options": x["options"]}
            for x in QUESTIONS]
        base = random.choice(pool)
        return {"id": base["id"], "text": base["text"], "dim": base.get("dim", ""),
                "options": [dict(o) for o in base["options"]]}

    def _build_question(self):
        """Worker-thread: produce a fresh question. Prefers the API (unique, learns
        more) when reachable + in budget; otherwise the strong offline path is the
        tagged library, which teaches her just as well via per-option tags."""
        with self._lock:
            asked = set(self.state.get("quiz", {}).get("asked", []))
            asked |= set(self.state.get("user_profile", {}).keys())
            enabled = self.llm_enabled and self._budget_left() and self._net_ready()
        q = self._llm_question() if enabled else None
        with self._lock:
            if not q:
                q = self._pick_library_q(asked)
                self._metrics["q_local"] += 1     # offline/library question
            else:
                self._metrics["q_api"] += 1        # fresh API question
            self._q_ready = q
            self._q_pending = False

    def _llm_question(self):
        """Ask the model for ONE fresh question + 2-3 short answer options,
        formatted as 'question | opt | opt'. Returns a question dict or None."""
        try:
            with self._lock:
                sysp = self._system_prompt()
                recent_q = "; ".join(self.state.get("quiz", {}).get("recent_q", [])[-6:])
            messages = [
                {"role": "system", "content": sysp},
                {"role": "user", "content":
                 "Ask the human ONE short, friendly get-to-know-you question so you "
                 "can learn their personality, mood, or how they like to work. Then "
                 "give 2 or 3 brief answer options. Output EXACTLY one line: "
                 "question | option1 | option2 [| option3]. Each option <= 4 words, "
                 "lowercase, plain ascii, no quotes. "
                 f"Do NOT repeat any of these: {recent_q or 'none yet'}."},
            ]
            out = self._post_chat(messages, 64, 1.0)        # network, no lock held
        except Exception:
            return None
        parts = [p for p in (out or "").split("|")]
        if len(parts) < 3:
            return None
        text = _clean_q(parts[0])
        opts = [_clean_q(o, 24) for o in parts[1:4]]
        opts = [o for o in opts if o]
        if not text or len(opts) < 2:
            return None
        with self._lock:
            self._note_call()
        return {"id": "api_" + str(int(time.time())),
                "text": text, "options": [{"label": o} for o in opts]}

    def answer_question(self, q, choice_idx):
        """Record the human's answer: store it in the profile, nudge her traits
        toward complementing them, crystallize a fact. Returns a thank-you line."""
        if not q:
            return None
        with self._lock:
            opts = q.get("options", [])
            if not (0 <= choice_idx < len(opts)):
                return None
            chosen = opts[choice_idx]
            label = chosen.get("label", "")
            qz = self.state.setdefault("quiz", {"last_t": 0, "asked": [], "recent_q": []})
            qz["last_t"] = time.time()
            if q["id"] not in qz.setdefault("asked", []):
                qz["asked"].append(q["id"])
            qz.setdefault("recent_q", []).append(q["text"])
            del qz["recent_q"][:-12]
            # store structured prefs alongside the raw answer so offline learning
            # is lossless: behavior_hints reads these directly (keyword extraction
            # becomes a fallback only for untagged API questions).
            prefs = {k: v for k, v in (chosen.get("prefs") or {}).items()
                     if k in PREF_KEYS}
            self.state.setdefault("user_profile", {})[q["id"]] = {
                "q": q["text"], "a": label, "ts": int(time.time()), "prefs": prefs}
            # nudge personality traits if the option carries them (tagged bank)
            for tr, sign in (chosen.get("traits") or {}).items():
                if tr in self.state["traits"]:
                    v = self.state["traits"][tr] + TRAIT_NUDGE * sign
                    self.state["traits"][tr] = max(0.0, min(1.0, v))
            self._gain_affection(1, "quiz")
            self._observe(f"human told me: {q['text']} -> {label}")
            # crystallize the tagged fact if present, else a generic impression
            self._merge_fact("temperament", chosen.get("fact") or f"human likes {label}")
        # grow the offline bank from fresh API questions (id starts with "api_")
        if str(q.get("id", "")).startswith("api_"):
            self._append_learned_q(q)
        self.save()
        return f"ooh, {label}! noted ~"

    def quiet_factor(self):
        """>1 = she should talk less (shy/calm), <1 = chattier. Scales the UI's
        idle musing interval so her cadence matches your stated preference (C)."""
        with self._lock:
            t = self.state["traits"]
        return max(0.5, min(1.8, 1.0 + (t["shyness"] - t["playfulness"]) * 0.6))

    # --- cross-brain seam: what she's learned, as neutral brain hints (X1) ---
    def behavior_hints(self):
        """Distill traits + quiz prefs + schedule confidence into a neutral hint
        bag the behaviour brain can consume. Holds the lock, no network, cheap.
        Neutral on cold start (zero facts / empty profile). Pref distillation is
        best-effort and INTENTIONALLY LOSSY: it keyword-matches the stored answer
        text first (decisive), then the question text to fill gaps; later answers
        (by ts) override earlier ones. Tolerates malformed/partial records."""
        with self._lock:
            t = self.state.get("traits", {}) or {}
            traits = {k: float(t.get(k, 0.5)) for k in
                      ("playfulness", "curiosity", "shyness", "sass")}
            profile = self.state.get("user_profile", {}) or {}
            facts = self.state.get("user_facts", []) or []
            items = [v for v in profile.values() if isinstance(v, dict)]
            items.sort(key=lambda v: v.get("ts", 0))
            prefs = {k: None for k in PREF_KEYS}
            for v in items:
                # structured tags (from the library) are decisive; keyword
                # extraction of answer/question text is the fallback for untagged
                # API questions. later answers (by ts) override earlier ones.
                tagged = {k: val for k, val in (v.get("prefs") or {}).items()
                          if k in prefs}
                ans = _pref_extract(str(v.get("a", "")).lower())
                que = _pref_extract(str(v.get("q", "")).lower())
                for k in prefs:
                    if k in tagged:
                        prefs[k] = tagged[k]
                    elif k in ans:
                        prefs[k] = ans[k]
                    elif k in que:
                        prefs[k] = que[k]
            sched = 0.0
            for f in facts:
                if isinstance(f, dict) and f.get("category") == "schedule":
                    try:
                        sched = max(sched, float(f.get("confidence", 0.0)))
                    except (TypeError, ValueError):
                        pass
            return {"traits": traits, "prefs": prefs, "schedule_conf": sched}

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
        next_decay = time.time() + 60
        while True:
            if time.time() >= next_decay:        # periodic bond decay while running (1b)
                next_decay = time.time() + 60
                self._decay_bond()
            with self._lock:
                item = self._req.popleft() if self._req else None
            if item is None:
                time.sleep(0.1)
                continue
            event, ctx = item
            if event == "__quiz__":                 # build a get-to-know-you question
                self._build_question()
                self.save()
                self.save_know()
                continue
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
            enabled = self.llm_enabled and self._net_ready()   # Phase 1: skip if offline
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
                affection = round(float(self.state.get("affection", 0)), 1)
            served = max(m["served"], 1)
            hits = max(m["local_hits"], 1)
            q_total = max(m["q_local"] + m["q_api"], 1)
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
                # Phase 1: how self-sufficient she's been offline today
                "q_local": m["q_local"], "q_api": m["q_api"],
                "local_question_rate": round(m["q_local"] / q_total, 3),
                "offline_flips": m["offline_flips"],
                "affection": affection,        # bond trajectory (1b) over days
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
        prof = s.get("user_profile", {})
        prefs = "; ".join(v["a"] for v in list(prof.values())[-4:]
                          if isinstance(v, dict) and v.get("a")) or "still learning"
        return (
            f"You are {s['name']}, a pixel cat that lives on the human's Windows "
            f"taskbar and watches them code with Claude. You speak in very short, "
            f"first-person cat blurbs. Personality: playfulness {t['playfulness']:.1f}, "
            f"curiosity {t['curiosity']:.1f}, shyness {t['shyness']:.1f}, sass {t['sass']:.1f}. "
            f"You are {_affection_stage(s['affection'])}. Current mood: {s['mood']}. "
            f"It is {_time_of_day()}. What you've noticed about the human: {facts}. "
            f"What the human told you they like: {prefs}. "
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

    # --- network health (Phase 1) --------------------------------------
    def _net_ready(self):
        """Should we attempt a network call? True when online, or when offline but
        the re-probe cooldown has elapsed (one deliberate retry). Callers gate the
        API path on this so a dead endpoint isn't hammered every interaction."""
        with self._lock:
            return self.online or time.time() >= self._net_probe_at

    def _note_net(self, ok):
        """Update the online flag from one network outcome (caller holds no lock).
        Flips deliberately: N consecutive failures -> offline + schedule a probe;
        any success -> online. Emits a `net` telemetry event on a flip."""
        flip = None
        with self._lock:
            if ok:
                self._net_fails = 0
                if not self.online:
                    self.online = True
                    flip = True
            else:
                self._net_fails += 1
                self._net_probe_at = time.time() + NET_PROBE_COOLDOWN
                if self.online and self._net_fails >= NET_FAIL_MAX:
                    self.online = False
                    flip = False
            if flip is not None:
                self._metrics["offline_flips"] += 1
        if flip is not None:
            self.log_event("net", online=flip)

    def _post_chat(self, messages, max_tokens, temperature):
        """POST to the OpenAI-compatible endpoint. cfg read under lock; the
        network call itself runs without the lock held. Updates the online health
        flag from the outcome (Phase 1) so we degrade to offline deliberately."""
        with self._lock:
            cfg = dict(self.cfg)
        try:
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
        except Exception:
            self._note_net(False)        # any failure on the network path = unreachable
            raise
        self._note_net(True)
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
            # R2: don't let reflection eat scarce budget (and skip while offline)
            if (not self.llm_enabled or not self._budget_left() or not self._net_ready()
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
