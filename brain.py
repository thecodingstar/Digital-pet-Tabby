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
    # drive-triggered behaviours
    "beg":     dict(frames=["hungry"],                    period=1000, speed=0, dur=(2, 4),    energy=-0.3, base=0),
    "seek":    dict(frames=["alert", "curious"],          period=420,  speed=2, dur=(2, 4),    energy=-0.6, base=0),
    "cower":   dict(frames=["scared"],                    period=1000, speed=0, dur=(2, 5),    energy=-0.3, base=0),
}

# per-second drift of each drive while no interaction happens
DRIFT = {"hunger": 0.12, "social": 0.08, "fear": -0.6}
URGENT = {"fear": 60, "hunger": 78, "social": 72}   # thresholds that take over


def _clamp(v, lo=0.0, hi=100.0):
    return lo if v < lo else hi if v > hi else v


class Brain:
    def __init__(self, energy=70.0):
        self.energy = energy
        self.hunger = 20.0
        self.social = 20.0
        self.fear = 0.0
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
        if self.fear > 50:   return "scared"
        if self.hunger > 75: return "hungry"
        if self.social > 70: return "lonely"
        if self.energy < 25: return "sleepy"
        if self.social < 25 and self.energy > 60: return "playful"
        return "content"

    def drives(self):
        return {"energy": self.energy, "hunger": self.hunger,
                "social": self.social, "fear": self.fear, "mood": self.mood}

    def urgent_drive(self):
        """Return the most pressing over-threshold drive, or None."""
        if self.fear >= URGENT["fear"]:
            return "fear"
        if self.hunger >= URGENT["hunger"]:
            return "hunger"
        if self.social >= URGENT["social"]:
            return "social"
        return None

    # --- interactions (called from the mascot) --------------------------
    def feed(self, amount=85.0):
        self.hunger = _clamp(self.hunger - amount)
        self._set("happy")
        return "fed"

    def receive_pet(self):
        """Petting lowers loneliness; if scared, it consoles (big fear drop)."""
        consoled = self.fear > 35
        self.fear = _clamp(self.fear - (55 if consoled else 8))
        self.social = _clamp(self.social - 30)
        self._set("groom" if not consoled else "happy")
        return "consoled" if consoled else "petted"

    def scare(self, amount=70.0):
        self.fear = _clamp(self.fear + amount)
        self._set("cower")
        return "scared"

    def force_sleep(self):
        self.energy = min(self.energy, 25.0)   # make the nap stick
        self._set("sleep")
        return "sleep"

    # --- behaviour selection -------------------------------------------
    def _factor(self, name):
        e = self.energy
        if name == "sleep":
            return 6.0 if e < 22 else 1.2 if e < 45 else 0.04
        if name == "sit":
            return 2.0 if e < 50 else 0.6
        if name in ("wander", "play"):
            return 0.4 if e < 30 else (1.7 if e > 70 else 1.0)
        if name == "zoomies":
            return 0.0 if e < 60 else 1.6
        if name == "grumpy":
            return 1.4 if (e < 35 or self.hunger > 55) else 0.25
        if name == "stretch":
            return 0.0
        return 1.0

    def _choose(self):
        # urgent drives override free choice
        u = self.urgent_drive()
        if u == "fear":   return "cower"
        if u == "hunger": return "beg"
        if u == "social": return "seek"
        names = list(BEHAVIORS)
        weights = [BEHAVIORS[n]["base"] * self._factor(n) for n in names]
        for i, n in enumerate(names):
            if n == self.behavior:
                weights[i] *= 0.15
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

    # --- per-tick update -----------------------------------------------
    def tick(self, dt):
        self.energy = _clamp(self.energy + self._rate * dt)
        self.hunger = _clamp(self.hunger + DRIFT["hunger"] * dt)
        self.social = _clamp(self.social + DRIFT["social"] * dt)
        self.fear = _clamp(self.fear + DRIFT["fear"] * dt)
        self._elapsed += dt

        # a sudden fear spike interrupts whatever we were doing
        if self.fear >= URGENT["fear"] and self.behavior != "cower":
            self._set("cower")
            return
        if self._elapsed < self._dur:
            return
        if self.behavior == "sleep":
            self._set("stretch")
        elif self.behavior == "stretch":
            self._set("sit")
        else:
            self._set(self._choose())
