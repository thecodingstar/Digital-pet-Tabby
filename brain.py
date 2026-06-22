#!/usr/bin/env python3
"""Tabby's autonomous brain + drives.

When Claude Code is idle, the mascot runs on this behaviour engine. It carries
four drives that drift over time and shape what the cat does:

    energy  - falls while active, rises while resting; low -> sleep
    hunger  - rises over time; high -> begs; feeding resets it
    social  - rises while ignored; high -> seeks attention; petting lowers it
    fear    - spikes on scary events; decays slowly; consoling drops it fast

Urgent drives interrupt normal behaviour (fear > hunger > social). Otherwise the
next behaviour is chosen with energy-weighted randomness, plus a scripted
wake-up (sleep -> stretch -> sit).
"""
import time
import random
from collections import deque

W = [f"walk-{i}" for i in range(1, 7)]
R = [f"run-{i}" for i in range(1, 7)]

# name -> dict(frames, period, speed, dur, energy, base)
# base 0 => never chosen at random; only triggered by a drive override.
BEHAVIORS = {
    "idle":    dict(frames=["idle"] * 8 + ["idle_blink"], period=220,  speed=0, dur=(2.5, 5),  energy=+0.4, base=3),
    "sit":     dict(frames=["sitting"],                   period=1000, speed=0, dur=(4, 9),    energy=+2.0, base=4),
    "sleep":   dict(frames=["sleeping"],                  period=1000, speed=0, dur=(12, 28),  energy=+7.0, base=3),
    "stretch": dict(frames=["stretch"],                   period=900,  speed=0, dur=(1.5, 2.6), energy=-0.5, base=0),
    "wander":  dict(frames=W,                             period=120,  speed=3, dur=(4, 9),    energy=-2.5, base=4),
    "zoomies": dict(frames=R,                             period=90,   speed=7, dur=(1.5, 3.5), energy=-6.0, base=1),
    "play":    dict(frames=["playful"],                   period=1000, speed=0, dur=(2, 4),    energy=-3.0, base=2),
    "curious": dict(frames=["curious", "alert"],          period=500,  speed=0, dur=(1.5, 3),  energy=-0.3, base=2),
    "happy":   dict(frames=["happy"],                     period=1000, speed=0, dur=(1.5, 3),  energy=+0.3, base=1),
    "think":   dict(frames=["thinking", "curious"],       period=500,  speed=0, dur=(2, 4),    energy=-0.2, base=1),
    "groom":   dict(frames=["love"],                      period=1000, speed=0, dur=(2, 3),    energy=+0.3, base=1),
    "grumpy":  dict(frames=["grumpy"],                    period=1000, speed=0, dur=(1.5, 3),  energy=-0.2, base=1),
    # extra idle flavour (Claude not running) — all reuse existing sprites
    "loaf":    dict(frames=["sitting"],                   period=1000, speed=0, dur=(6, 14),   energy=+1.5, base=2),
    "knead":   dict(frames=["love"],                      period=700,  speed=0, dur=(2, 4),    energy=+0.2, base=1),
    "ponder":  dict(frames=["thinking", "curious", "alert"], period=520, speed=0, dur=(2, 4),  energy=-0.2, base=1),
    "watch":   dict(frames=["alert", "curious"],          period=600,  speed=0, dur=(2, 4),    energy=-0.1, base=2),
    # drive-triggered behaviours
    "beg":     dict(frames=["hungry"],                    period=1000, speed=0, dur=(2, 4),    energy=-0.3, base=0),
    "seek":    dict(frames=["alert", "curious"],          period=420,  speed=2, dur=(2, 4),    energy=-0.6, base=0),
    "cower":   dict(frames=["scared"],                    period=1000, speed=0, dur=(2, 5),    energy=-0.3, base=0),
}

# per-second drift of each drive while no interaction happens
DRIFT = {"hunger": 0.12, "social": 0.08, "fear": -0.6}
URGENT = {"fear": 60, "hunger": 78, "social": 72}   # thresholds that take over

AFFINITY_BOUNDS = (0.2, 1.0)   # behaviour-affinity clamp (B2)
AFFINITY_ALPHA = 0.20          # affinity EWMA rate (B2)
BEHAV_HISTORY_N = 4            # recent-behaviour decay window (B6)
TRUST_INIT = 0.30              # starting trust (B4)


def _clamp(v, lo=0.0, hi=100.0):
    return lo if v < lo else hi if v > hi else v


class Brain:
    def __init__(self, energy=70.0):
        self.energy = energy
        self.hunger = 20.0
        self.social = 20.0
        self.fear = 0.0
        self.valence = 60.0        # smoothed pleasant<->unpleasant (B1)
        self.arousal = 50.0        # smoothed calm<->excited (B1)
        self.affinity = {}         # behaviour -> learned affinity 0..1 (B2)
        self.trust = TRUST_INIT    # dampens fear spikes (B4)
        self.jumpiness = 0.0       # rises with error storms, amplifies fear (B4)
        self.active_hours = [0] * 24   # learned user activity histogram (B5)
        self._last_console_t = 0.0
        self._behav_hist = deque(maxlen=BEHAV_HISTORY_N)   # (name, t) for B6
        self.behavior = None
        self.frames = ["idle"]
        self.period = 220
        self.speed = 0
        self._rate = 0.0
        self._dur = 0.0
        self._elapsed = 0.0
        self._set("sit")

    # --- drive state ----------------------------------------------------
    @property
    def mood(self):
        # urgent drives name the mood directly; otherwise use the smoothed
        # valence/arousal so labels don't flicker on a threshold (B1).
        if self.fear > 50:   return "scared"
        if self.hunger > 75: return "hungry"
        if self.social > 70: return "lonely"
        if self.arousal < 35: return "sleepy"
        if self.valence > 60 and self.arousal > 55: return "playful"
        return "content"

    def drives(self):
        return {"energy": self.energy, "hunger": self.hunger,
                "social": self.social, "fear": self.fear, "mood": self.mood}

    def urgent_drive(self):
        """Return the most pressing over-threshold drive, or None. While scared,
        hunger/social urgency thresholds are raised so fear takes priority (B3)."""
        if self.fear >= URGENT["fear"]:
            return "fear"
        bump = 20 if self.fear > 40 else 0
        if self.hunger >= URGENT["hunger"] + bump:
            return "hunger"
        if self.social >= URGENT["social"] + bump:
            return "social"
        return None

    # --- interactions (called from the mascot) --------------------------
    def _reinforce(self, name, target):
        """Nudge a behaviour's affinity toward target (1=earned attention, 0=not),
        bounded so innate personality still dominates (B2)."""
        if not name:
            return
        cur = self.affinity.get(name, 0.5)
        val = (1 - AFFINITY_ALPHA) * cur + AFFINITY_ALPHA * target
        self.affinity[name] = max(AFFINITY_BOUNDS[0], min(AFFINITY_BOUNDS[1], val))

    def _note_active(self):
        self.active_hours[time.localtime().tm_hour] += 1   # B5

    def feed(self, amount=85.0):
        self._reinforce(self.behavior, 1.0)    # whatever she did just earned food
        self._note_active()
        self.hunger = _clamp(self.hunger - amount)
        self._set("happy")
        return "fed"

    def receive_pet(self):
        """Petting lowers loneliness; if scared, it consoles (big fear drop)."""
        self._reinforce(self.behavior, 1.0)    # that behaviour earned attention
        self._note_active()
        consoled = self.fear > 35
        if consoled:                            # consoling builds trust (B4)
            self.trust = min(1.0, self.trust + 0.1)
            self._last_console_t = time.time()
        self.fear = _clamp(self.fear - (55 if consoled else 8))
        self.social = _clamp(self.social - 30)
        self._set("groom" if not consoled else "happy")
        return "consoled" if consoled else "petted"

    def scare(self, amount=70.0):
        self._reinforce(self.behavior, 0.0)    # got startled mid-behaviour
        # B4: trust dampens the spike; jumpiness from recent error-storms amplifies it
        effective = amount * (1 - 0.5 * self.trust) * (1 + self.jumpiness)
        self.jumpiness = min(1.0, self.jumpiness + 0.25)
        if time.time() - self._last_console_t > 30:
            self.trust = max(0.0, self.trust - 0.03)
        self.fear = _clamp(self.fear + effective)
        self._set("cower")
        return "scared"

    def force_sleep(self):
        self.energy = min(self.energy, 25.0)   # make the nap stick
        self._set("sleep")
        return "sleep"

    # --- persistence (drives + learned affinity survive restarts) ------
    def snapshot_drives(self):
        return {"energy": round(self.energy, 1), "hunger": round(self.hunger, 1),
                "social": round(self.social, 1), "fear": round(self.fear, 1),
                "valence": round(self.valence, 1), "arousal": round(self.arousal, 1),
                "trust": round(self.trust, 3), "jumpiness": round(self.jumpiness, 3),
                "active_hours": list(self.active_hours),
                "affinity": {k: round(v, 3) for k, v in self.affinity.items()}}

    def load_drives(self, d):
        if not d:
            return
        try:
            self.energy = _clamp(float(d.get("energy", self.energy)))
            self.hunger = _clamp(float(d.get("hunger", self.hunger)))
            self.social = _clamp(float(d.get("social", self.social)))
            self.fear = _clamp(float(d.get("fear", self.fear)))
            self.valence = _clamp(float(d.get("valence", self.valence)))
            self.arousal = _clamp(float(d.get("arousal", self.arousal)))
            self.trust = max(0.0, min(1.0, float(d.get("trust", self.trust))))
            self.jumpiness = max(0.0, min(1.0, float(d.get("jumpiness", self.jumpiness))))
            ah = d.get("active_hours")
            if isinstance(ah, list) and len(ah) == 24:
                self.active_hours = [int(x) for x in ah]
            aff = d.get("affinity") or {}
            self.affinity = {k: max(AFFINITY_BOUNDS[0], min(AFFINITY_BOUNDS[1], float(v)))
                             for k, v in aff.items() if isinstance(v, (int, float))}
        except (TypeError, ValueError):
            pass

    def _is_active_hour(self):
        """True if the current hour is one the human is usually around (B5)."""
        tot = sum(self.active_hours)
        if tot < 10:                       # not enough data yet -> assume lively
            return True
        return self.active_hours[time.localtime().tm_hour] >= tot / 24.0

    # --- behaviour selection -------------------------------------------
    def _factor(self, name):
        e = self.energy
        active = self._is_active_hour()
        if name == "sleep":
            f = 6.0 if e < 22 else 1.2 if e < 45 else 0.04
            return f * (1.0 if active else 1.6)        # nap more in quiet hours (B5)
        if name == "sit":
            return 2.0 if e < 50 else 0.6
        if name in ("wander", "play"):
            f = 0.4 if e < 30 else (1.7 if e > 70 else 1.0)
            return f * (1.2 if active else 0.8)        # livelier when user's around
        if name == "zoomies":
            return (0.0 if e < 60 else 1.6) * (1.2 if active else 0.7)
        if name == "grumpy":
            return 1.4 if (e < 35 or self.hunger > 55) else 0.25
        if name == "stretch":
            return 0.0
        return 1.0

    def _recent_penalty(self, name, now):
        """Decaying penalty for recently-used behaviours: 0.15 just-used,
        fading back to 1.0 over ~30s (B6) — variety without randomness."""
        pen = 1.0
        for n, t in self._behav_hist:
            if n == name:
                pen = min(pen, 0.15 + 0.85 * min((now - t) / 30.0, 1.0))
        return pen

    def _choose(self):
        # urgent drives override free choice
        u = self.urgent_drive()
        if u == "fear":   return "cower"
        if u == "hunger": return "beg"
        if u == "social": return "seek"
        now = time.time()
        names = list(BEHAVIORS)
        weights = []
        for n in names:
            aff = self.affinity.get(n, 0.5)                 # B2
            w = (BEHAVIORS[n]["base"] * self._factor(n)
                 * (0.7 + 0.6 * aff)                        # learned preference
                 * self._recent_penalty(n, now))            # B6
            weights.append(w)
        if sum(weights) <= 0:
            return "sit"
        return random.choices(names, weights=weights, k=1)[0]

    def _set(self, name):
        b = BEHAVIORS[name]
        self.behavior = name
        self.frames = b["frames"]
        self.period = b["period"]
        self.speed = b["speed"]
        self._rate = b["energy"]
        self._dur = random.uniform(*b["dur"])
        self._elapsed = 0.0
        self._behav_hist.append((name, time.time()))        # B6 history

    # --- per-tick update -----------------------------------------------
    def tick(self, dt):
        # smooth valence/arousal toward drive-derived targets (B1)
        val_t = ((100 - self.fear) * 0.4 + (100 - self.hunger) * 0.2
                 + (100 - self.social) * 0.2 + self.energy * 0.2)
        aro_t = self.energy * 0.5 + self.fear * 0.5
        self.valence += 0.1 * (val_t - self.valence)
        self.arousal += 0.1 * (aro_t - self.arousal)

        self.energy = _clamp(self.energy + self._rate * dt)
        self.hunger = _clamp(self.hunger + DRIFT["hunger"] * dt)
        self.social = _clamp(self.social + DRIFT["social"] * dt)
        self.fear = _clamp(self.fear + DRIFT["fear"] * dt)
        # B3: exertion burns hunger faster; jumpiness fades over time
        if self.behavior in ("zoomies", "play"):
            self.hunger = _clamp(self.hunger + 0.2 * dt)
        self.jumpiness = max(0.0, self.jumpiness - 0.03 * dt)
        self._elapsed += dt

        # a sudden fear spike interrupts whatever we were doing
        if self.fear >= URGENT["fear"] and self.behavior != "cower":
            self._set("cower")
            return
        if self._elapsed < self._dur:
            return
        # B3: finishing a restful behaviour gives a small contentment bump
        if self.behavior in ("groom", "sleep", "stretch"):
            self.valence = _clamp(self.valence + 5)
        if self.behavior == "sleep":
            self._set("stretch")
        elif self.behavior == "stretch":
            self._set("sit")
        else:
            self._set(self._choose())
