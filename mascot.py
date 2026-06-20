#!/usr/bin/env python3
"""Pixel-buddy cat mascot for the Windows taskbar.

A small frameless, always-on-top, transparent window with a cat that perches on
the top edge of the taskbar (feet on the bar) and reacts to Claude Code
activity. Switchable characters (right-click to change; choice is remembered):

  Hand-drawn pixel cats (see cats.py):  black, tabby, ginger, cream
  Image cats (PNG sprite folders):      tabby2  (cat2_states/, generated art)

Why it sits ABOVE the bar, not on it:
    The Windows 11 taskbar is an explorer-owned topmost window that repaints
    over its own rectangle, so a window placed *inside* the bar gets hidden
    underneath it. Resting the cat's feet on the bar's top edge keeps it
    visible while still looking like it stands on the taskbar.

Run:  python mascot.py      (or  pythonw mascot.py  for no console)
      python mascot.py --cat black|tabby|ginger|cream|tabby2
Quit: double-click the cat, or right-click -> Quit.  Left-drag to move.
Deps: pip install PyQt5
"""
import sys, json, os, time, glob, random, ctypes, ctypes.wintypes
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QWidget, QMenu
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QPainter, QColor, QPixmap, QImage

import cats

CONFIG = Path.home() / ".claude" / "statusline" / "mascot_config.json"

# Image-based characters: name -> sprite folder (PNG per state).
IMAGE_CHARACTERS = {"tabby2": "cat2_states", "tabby3": "cat3_states"}
ALL_CHARACTERS = cats.CHARACTERS + list(IMAGE_CHARACTERS)

# ============================================================== taskbar pos
ABM_GETTASKBARPOS = 5


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
                ("hWnd", ctypes.wintypes.HWND),
                ("uCallbackMessage", ctypes.wintypes.UINT),
                ("uEdge", ctypes.wintypes.UINT),
                ("rc", ctypes.wintypes.RECT),
                ("lParam", ctypes.wintypes.LPARAM)]


def get_taskbar_rect():
    try:
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        ctypes.windll.shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(abd))
        r = abd.rc
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return 0, 1050, 1920, 30


# ============================================================== state machine
_HOLDS = {"tool_success": 1600, "tool_failure": 1800, "done": 2500, "auth_success": 2000}


def _effective_state(st, now_s):
    if not st:
        return "idle"
    if now_s - float(st.get("lastUpdatedAt", 0)) > 600:
        return "idle"
    cs = st.get("currentState", "idle")
    sub = int(st.get("activeSubagentCount", 0) or 0)
    hold = _HOLDS.get(cs)
    changed = float(st.get("lastStateChangedAt", 0))
    if hold and (now_s - changed) * 1000 >= hold:
        if sub > 0:
            return "subagent_running"
        if cs in ("question", "permission"):
            return "thinking"
        return "idle"
    return cs


def load_state():
    dirs = [Path.home() / ".claude" / "statusline" / "state",
            Path(__file__).parent / "statusline" / "state"]
    files = []
    for d in dirs:
        try:
            files.extend(glob.glob(str(d / "*.json")))
        except Exception:
            pass
    if not files:
        return None
    try:
        newest = max(files, key=os.path.getmtime)
        if time.time() - os.path.getmtime(newest) > 600:
            return None
        with open(newest, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


# ============================================================== config
def load_character(default="tabby2"):
    try:
        c = json.load(open(CONFIG, encoding="utf-8")).get("character", default)
        return c if c in ALL_CHARACTERS else default
    except Exception:
        return default


def save_character(name):
    try:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        json.dump({"character": name}, open(CONFIG, "w", encoding="utf-8"))
    except Exception:
        pass


# ============================================================== color helpers
def _hexrgb(c):
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))


def _heat_color(hexcol, used_pct):
    if used_pct is None or used_pct <= 60:
        return hexcol
    t = min(1.0, (used_pct - 60) / 25.0)
    r, g, b = _hexrgb(hexcol)
    nr = round(r + (255 - r) * t); ng = round(g + (68 - g) * t); nb = round(b + (68 - b) * t)
    return f"#{nr:02x}{ng:02x}{nb:02x}"


# ============================================================== pixel skin
class PixelSkin:
    """Hand-drawn char-grid cats from cats.py."""
    GRID = 16

    def __init__(self, character):
        self.character = character
        self.scale = 6
        self.side_pad = 6
        self.top_pad = 12
        self.win_w = self.GRID * self.scale + self.side_pad * 2
        self.win_h = self.top_pad + 15 * self.scale

    def grid(self, emotion, now, walking):
        leg_phase = int(now * 1000 / 140) % 2 if walking else 0
        face_override = "sleepy" if (emotion == "idle" and (now % 4.0) < 0.16) else None
        return cats.compose(self.character, emotion, leg_phase, face_override)

    def paint(self, p, emotion, now, ctx_pct, bob, facing_left):
        pal = cats.PALETTES[self.character]
        walking = emotion in cats.WALK_STATES
        grid = self.grid(emotion, now, walking)
        s = self.scale
        ox, oy = self.side_pad, self.top_pad - bob
        mirror = facing_left and walking
        for ri, row in enumerate(grid):
            for ci, ch in enumerate(row):
                hexcol = pal.get(ch)
                if not hexcol:
                    continue
                if ch in cats.HEAT_CHARS:
                    hexcol = _heat_color(hexcol, ctx_pct)
                r, g, b = _hexrgb(hexcol)
                cc = (self.GRID - 1 - ci) if mirror else ci
                p.fillRect(ox + cc * s, oy + ri * s, s, s, QColor(r, g, b))
        for (ci, ri, bc) in cats.BADGES.get(emotion, ()):
            hexcol = cats.BADGE_COLORS.get(bc)
            if hexcol:
                r, g, b = _hexrgb(hexcol)
                p.fillRect(ox + ci * s, oy + ri * s, s, s, QColor(r, g, b))


# ============================================================== image skin
class ImageSkin:
    """PNG sprite-folder cat. One image per state, drawn bottom-centered."""
    BODY_H = 96                       # target standing height (px)

    def __init__(self, folder):
        self.folder = Path(__file__).parent / folder
        self._raw = {}
        for f in glob.glob(str(self.folder / "*.png")):
            self._raw[Path(f).stem] = QImage(f)
        base = self._raw.get("idle") or next(iter(self._raw.values()))
        self.factor = self.BODY_H / base.height()
        self.pix, self.pix_m = {}, {}     # normal + mirrored, scaled
        maxw = maxh = 0
        for name, im in self._raw.items():
            w = max(1, round(im.width() * self.factor))
            h = max(1, round(im.height() * self.factor))
            sm = im.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
            self.pix[name] = QPixmap.fromImage(sm)
            self.pix_m[name] = QPixmap.fromImage(sm.mirrored(True, False))
            maxw, maxh = max(maxw, w), max(maxh, h)
        self.side_pad = 8
        self.top_pad = 6
        self.win_w = maxw + self.side_pad * 2
        self.win_h = maxh + self.top_pad
        # auto-detect the walk cycle length (walk1..walkN present)
        self.walk_cycle = []
        n = 1
        while f"walk{n}" in self.pix:
            self.walk_cycle.append(f"walk{n}")
            n += 1
        if not self.walk_cycle:
            self.walk_cycle = ["idle"]

    def has(self, name):
        return name in self.pix

    def paint(self, p, name, bob, mirror=False):
        pm = (self.pix_m if mirror else self.pix).get(name) or self.pix.get("idle")
        if pm is None:
            return
        x = (self.win_w - pm.width()) // 2
        y = self.win_h - pm.height() - bob       # bottom-aligned (feet on bar)
        p.drawPixmap(x, y, pm)


# ============================================================== window
class Mascot(QWidget):
    def __init__(self, character=None):
        super().__init__()
        self.character = character or load_character()
        if self.character not in ALL_CHARACTERS:
            self.character = ALL_CHARACTERS[0]

        self.emotion = "idle"
        self.ctx_pct = 0
        self.bob = 0
        self.facing_left = False
        self.idle_since = time.time()
        self._drag = None
        # personality signals
        self.failed = 0                # failedToolCountInTurn
        self.tool_count = 0            # toolCountInTurn
        self.woke_until = 0            # show grumpy briefly after being woken
        self.fidget_until = 0          # show a random idle fidget pose
        self.fidget_name = "idle"
        self.next_fidget = time.time() + 16
        # horizontal stroll along the taskbar
        self.home_x = None
        self.base_y = 0
        self.span = 0
        self.walk_offset = 0          # 0 = home (right), negative = walked left
        self.walk_dir = -1            # start strolling left
        self.walk_speed = 3           # px per tick

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setWindowTitle("Pixel Buddy")

        self._build_skin()
        self.reposition()
        self._timer(self.refresh_state, 400)
        self._timer(self.tick, 60)
        self._timer(self.reposition, 3000)
        self.refresh_state()
        self.show()

    def _build_skin(self):
        if self.character in IMAGE_CHARACTERS:
            self.skin = ImageSkin(IMAGE_CHARACTERS[self.character])
        else:
            self.skin = PixelSkin(self.character)

    def _timer(self, fn, ms):
        t = QTimer(self)
        t.timeout.connect(fn)
        t.start(ms)
        return t

    def reposition(self):
        """Recompute home anchor + stroll range from the live taskbar rect."""
        if self._drag is not None:
            return
        tb_l, tb_t, tb_w, tb_h = get_taskbar_rect()
        self.home_x = tb_l + tb_w - self.skin.win_w - 170   # right, left of tray
        self.base_y = tb_t - self.skin.win_h                # feet on bar top
        left_limit = tb_l + 60
        self.span = max(0, min(520, self.home_x - left_limit))
        if self.walk_offset < -self.span:
            self.walk_offset = -self.span
        self.setGeometry(self.home_x + self.walk_offset, self.base_y,
                         self.skin.win_w, self.skin.win_h)
        self.raise_()

    def refresh_state(self):
        now = time.time()
        st = load_state()
        name = _effective_state(st, now)
        if name != self.emotion:
            was_sleeping = (self.emotion == "idle" and now - self.idle_since > 35)
            if was_sleeping and name != "idle":
                self.woke_until = now + 1.3        # grumpy at being disturbed
            if name == "idle":
                self.idle_since = now
                self.next_fidget = now + 16
        self.emotion = name
        if st:
            self.ctx_pct = st.get("ctx_pct", 0) or 0
            self.failed = int(st.get("failedToolCountInTurn", 0) or 0)
            self.tool_count = int(st.get("toolCountInTurn", 0) or 0)

    def tick(self):
        now = time.time()
        walking = self.emotion in cats.WALK_STATES
        if walking:
            self.bob = 3 if int(now * 1000 / 140) % 2 else 0
            self.walk_offset += self.walk_dir * self.walk_speed
            if self.walk_offset <= -self.span:          # bump left edge -> turn
                self.walk_offset = -self.span
                self.walk_dir = 1
            elif self.walk_offset >= 0:                  # back home -> turn
                self.walk_offset = 0
                self.walk_dir = -1
            self.facing_left = self.walk_dir < 0         # face travel direction
        else:
            self.bob = 0
            if self.walk_offset < 0:                      # stroll back home
                self.walk_offset = min(0, self.walk_offset + 6)
            self._schedule_fidget(now)
        if self.home_x is not None and self._drag is None:
            self.move(self.home_x + self.walk_offset, self.base_y)
        self.update()

    # ---- personality ----
    FIDGETS = ["playful", "curious", "stretch", "hungry"]

    def _schedule_fidget(self, now):
        """Occasionally trigger a brief idle fidget pose (image cat only)."""
        if self.emotion != "idle" or now - self.idle_since < 6:
            return
        if now >= self.next_fidget and now >= self.fidget_until:
            avail = [f for f in self.FIDGETS
                     if isinstance(self.skin, ImageSkin) and self.skin.has(f)]
            if avail:
                self.fidget_name = random.choice(avail)
                self.fidget_until = now + 2.2
            self.next_fidget = now + random.uniform(18, 32)

    def _image_frame(self, now):
        """Map all signals to a sprite name for the image cat (personality)."""
        e = self.emotion
        if now < self.woke_until and self.skin.has("grumpy"):
            return "grumpy"
        if e in cats.WALK_STATES:
            cyc = self.skin.walk_cycle
            return cyc[int(now * 1000 / 110) % len(cyc)]
        if e == "tool_failure":
            return "angry" if (self.failed >= 2 and self.skin.has("angry")) else "sad"
        if e in ("tool_success", "auth_success"):
            return "happy"
        if e == "done":
            return "love" if (self.tool_count >= 8 and self.skin.has("love")) else "done"
        if e == "permission":
            return "surprised" if self.skin.has("surprised") else "alert"
        if e in ("thinking", "question"):
            return "thinking"
        # idle family
        if now < self.fidget_until and self.skin.has(self.fidget_name):
            return self.fidget_name
        if self.ctx_pct >= 82 and self.skin.has("hungry"):
            return "hungry"                                 # low on context room
        idle_secs = now - self.idle_since
        if idle_secs > 35 and self.skin.has("sleeping"):
            return "sleeping"
        if idle_secs > 14 and self.skin.has("sitting"):
            return "sitting"
        if 8 < idle_secs <= 14 and self.skin.has("stretch"):
            return "stretch"
        if (now % 5.0) < 0.16 and self.skin.has("idle_blink"):
            return "idle_blink"
        return "idle"

    def paintEvent(self, _):
        now = time.time()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        if isinstance(self.skin, ImageSkin):
            name = self._image_frame(now)
            mirror = self.facing_left and self.emotion in cats.WALK_STATES
            self.skin.paint(p, name, self.bob, mirror)
        else:
            self.skin.paint(p, self.emotion, now, self.ctx_pct, self.bob,
                            self.facing_left)
        p.end()

    def set_character(self, name):
        self.character = name
        save_character(name)
        self._build_skin()
        self.reposition()
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.globalPos() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPos() - self._drag)

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        QApplication.quit()

    def contextMenuEvent(self, e):
        m = QMenu(self)
        m.addAction(f"State: {self.emotion}").setEnabled(False)
        m.addSeparator()
        for name in ALL_CHARACTERS:
            act = m.addAction(("● " if name == self.character else "   ") + name.title())
            act.triggered.connect(lambda _=False, n=name: self.set_character(n))
        m.addSeparator()
        m.addAction("Quit", QApplication.quit)
        m.exec_(e.globalPos())


def main():
    character = None
    if "--cat" in sys.argv:
        i = sys.argv.index("--cat")
        if i + 1 < len(sys.argv):
            character = sys.argv[i + 1]
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    _ = Mascot(character)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
