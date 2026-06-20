#!/usr/bin/env python3
"""Tabby taskbar mascot — renders the cat4_states sprite library on the Windows
taskbar and animates it from Claude Code activity state.

State files: ~/.claude/statusline/state/<session>.json (written by state_writer).
Sprites:     ./cat4_states/*.png  (384x384, transparent, bottom-aligned)

Run: python taskbar_mascot_cat.py
"""
import sys, time, ctypes, ctypes.wintypes, json, os, glob, random
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QWidget, QMenu
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import (QPixmap, QPainter, QColor, QFont, QFontMetrics,
                         QPainterPath, QPen, QRegion)
from brain import Brain
from chatter import Cat

STOP_FILE = Path(__file__).parent / ".mascot_stop"   # touched by Quit menu

# what the cat is "doing" -> human phrase (for the hover panel)
DOING = {
    "idle": "just chilling", "sit": "sitting", "sleep": "napping",
    "stretch": "stretching", "wander": "wandering about", "zoomies": "ZOOMIES!",
    "play": "playing", "curious": "being nosy", "happy": "feeling happy",
    "think": "pondering", "groom": "grooming", "grumpy": "grumpy",
    "beg": "begging for food", "seek": "wants attention", "cower": "scared!",
}
REACT_DOING = {
    "tool_running": "watching you work", "subagent_running": "following along",
    "thinking": "thinking with you", "tool_success": "cheering you on",
    "tool_failure": "worried for you", "question": "what'll you pick?",
    "done": "proud of you", "permission": "awaiting your ok",
    "auth_success": "glad you're back",
}

ABM_GETTASKBARPOS = 5
SPRITE_DIR = Path(__file__).parent / "cat4_states"
DISP = 106                 # on-screen sprite size (px)
TICK = 90                 # animation tick (ms)
FOOT_OVERLAP = 7         # px the feet rest onto the taskbar's top edge

# SetWindowPos constants for forcing z-order above the taskbar
HWND_TOPMOST = -1
SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOACTIVATE = 0x0010

# Reactive map: Claude state -> (frames, ms-per-frame, move-speed px/tick).
# Used while Claude is actively working. "idle" / no-state hands control to the
# autonomous Brain (brain.py) instead, so the cat acts on its own.
REACTIVE = {
    "thinking":         (["thinking", "thinking", "curious"], 360, 0),
    "tool_running":     ([f"run-{i}" for i in range(1, 7)], 90, 7),
    "subagent_running": ([f"walk-{i}" for i in range(1, 7)], 120, 3),
    "tool_success":     (["done"], 1000, 0),
    "tool_failure":     (["scared"], 1000, 0),
    "question":         (["thinking", "curious"], 450, 0),
    "permission":       (["alert"], 600, 0),
    "done":             (["done", "happy"], 450, 0),
    "auth_success":     (["happy"], 800, 0),
}


class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
                ("hWnd", ctypes.wintypes.HWND),
                ("uCallbackMessage", ctypes.wintypes.UINT),
                ("uEdge", ctypes.wintypes.UINT),
                ("rc", ctypes.wintypes.RECT),
                ("lParam", ctypes.wintypes.LPARAM)]


ABE_TOP, ABE_BOTTOM = 1, 3


def get_taskbar_rect():
    """Return (left, top, width, height, edge). edge is ABE_* (which screen
    side the taskbar is docked to)."""
    try:
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        ctypes.windll.shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(abd))
        r = abd.rc
        return r.left, r.top, r.right - r.left, r.bottom - r.top, abd.uEdge
    except Exception:
        return 0, 1050, 1920, 30, ABE_BOTTOM


def load_state():
    sd = Path.home() / ".claude" / "statusline" / "state"
    try:
        files = glob.glob(str(sd / "*.json"))
        if not files:
            return None
        newest = max(files, key=os.path.getmtime)
        if time.time() - os.path.getmtime(newest) > 600:
            return None
        with open(newest) as f:
            return json.load(f)
    except Exception:
        return None


class SpeechBubble(QWidget):
    """Small rounded speech bubble shown above the cat for a few seconds."""
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.text = ""
        self.font = QFont("Segoe UI", 9)
        self._hide = QTimer(self)
        self._hide.setSingleShot(True)
        self._hide.timeout.connect(self.hide)

    def show_text(self, text, cx, bottom_y):
        self.text = text
        fm = QFontMetrics(self.font)
        tw = min(220, fm.horizontalAdvance(text) + 2)
        lines = [text]
        if fm.horizontalAdvance(text) > 220:           # wrap to 2 lines
            mid = len(text) // 2
            sp = text.rfind(" ", 0, mid + 8)
            if sp > 0:
                lines = [text[:sp], text[sp + 1:]]
                tw = min(220, max(fm.horizontalAdvance(l) for l in lines) + 2)
        pad = 12
        w = tw + pad * 2
        h = fm.height() * len(lines) + pad * 2 + 8     # +tail
        self._lines = lines
        x = int(cx - w / 2)
        y = int(bottom_y - h)
        self.setGeometry(x, y, w, h)
        self.update()
        self.show()
        self._hide.start(max(2500, min(6000, 1400 + len(text) * 90)))

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        fm = QFontMetrics(self.font)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(w - 1), float(h - 10), 10.0, 10.0)
        # little tail at bottom-center
        cx = w / 2
        path.moveTo(cx - 8, h - 11)
        path.lineTo(cx, h - 1)
        path.lineTo(cx + 8, h - 11)
        p.setPen(QPen(QColor(60, 60, 70), 1))
        p.setBrush(QColor(255, 255, 255, 235))
        p.drawPath(path)
        p.setFont(self.font)
        p.setPen(QColor(30, 30, 40))
        y = 12 + fm.ascent()
        for ln in getattr(self, "_lines", [self.text]):
            p.drawText(int((w - fm.horizontalAdvance(ln)) / 2), int(y), ln)
            y += fm.height()
        p.end()


class InfoPanel(QWidget):
    """Hover panel: what the cat is doing + drive bars + bond %."""
    BARS = [("energy", QColor(90, 200, 120)), ("hunger", QColor(230, 170, 60)),
            ("social", QColor(90, 160, 230)), ("fear", QColor(220, 90, 90))]

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.data = {}
        self.title = QFont("Segoe UI Semibold", 9)
        self.small = QFont("Segoe UI", 8)

    def update_info(self, doing, drives, bond, cx, bottom_y):
        self.data = {"doing": doing, "drives": drives, "bond": bond}
        w, h = 168, 112
        self.setGeometry(int(cx - w / 2), int(bottom_y - h), w, h)
        self.update()
        if not self.isVisible():
            self.show()

    def paintEvent(self, _):
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(w - 1), float(h - 1), 10.0, 10.0)
        p.setPen(QPen(QColor(70, 70, 80), 1))
        p.setBrush(QColor(28, 30, 38, 238))
        p.drawPath(path)
        p.setFont(self.title)
        p.setPen(QColor(245, 220, 150))
        p.drawText(12, 20, f"Tabby — {self.data['doing']}")
        p.setFont(self.small)
        p.setPen(QColor(200, 205, 215))
        d = self.data["drives"]
        p.drawText(12, 36, f"mood: {d.get('mood','?')}   bond {self.data['bond']}%")
        y = 46
        for name, col in self.BARS:
            val = int(d.get(name, 0))
            p.setPen(QColor(190, 195, 205))
            p.drawText(12, y + 9, name[:3])
            x0, bw = 40, w - 52
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(55, 58, 68))
            p.drawRoundedRect(x0, y, bw, 8, 4, 4)
            p.setBrush(col)
            p.drawRoundedRect(x0, y, int(bw * val / 100), 8, 4, 4)
            y += 15
        p.end()


class Mascot(QWidget):
    def __init__(self):
        super().__init__()
        self.tb = get_taskbar_rect()           # left, top, w, h, edge
        self.edge = self.tb[4]
        # NOTE: no WindowTransparentForInput -> the cat is clickable (pet it).
        # A per-frame mask (see _apply_mask) makes only the cat pixels hit-able,
        # so clicks on the transparent area pass through to whatever is behind.
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setCursor(Qt.PointingHandCursor)

        self.pix = self._load_sprites()        # name -> QPixmap
        # personality / voice (created first so the brain can restore drives)
        self.cat = Cat()
        self.brain = Brain()                    # autonomous behaviour engine
        self.brain.load_drives(self.cat.get_drives())   # survive restarts
        self.bubble = SpeechBubble()
        self.info = InfoPanel()
        self.hovering = False
        self.mode = "brain"                     # "brain" | "react"
        self.src_key = None                     # id of current frame list (reset detector)
        self._mask_key = None                   # id of pixmap the mask was built from
        self.frames = self.brain.frames
        self.period = self.brain.period
        self.speed = self.brain.speed
        self.fi = 0
        self.facing = "right"                   # right | left
        self.last_frame_t = time.time()
        self.last_tick = time.time()
        self.next_state_poll = 0.0              # throttle load_state()
        self._state = None
        self.next_save = time.time() + 30       # periodic drive persistence
        self.x = self.tb[0] + self.tb[2] - DISP - 220  # start near right, clear tray
        self.dir = -1                            # pacing direction
        self.left_bound = self.tb[0] + 20
        self.right_bound = self.tb[0] + self.tb[2] - DISP - 200
        if self.right_bound <= self.left_bound:  # narrow / side taskbar guard
            self.right_bound = self.left_bound + 1
            self.x = self.left_bound
        self.cs = None                # current Claude state this tick
        self.prev_cs = None
        self.last_say = 0.0
        self.next_musing = time.time() + random.uniform(25, 60)
        self.prev_drive = None        # last urgent drive announced

        self.setGeometry(int(self.x), self.y(), DISP, DISP)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(TICK)
        self.show()
        self._raise_above_taskbar()
        self.cat.say("greet")        # hello on launch

    # --- communication --------------------------------------------------
    def _emote(self, event, gap, ctx=None):
        """Ask the cat to say something for `event`, throttled by `gap` seconds."""
        now = time.time()
        if now - self.last_say < gap:
            return
        self.last_say = now
        self.cat.say(event, ctx or {})

    def _ctx(self):
        """Context handed to the cat's voice: drives + current behaviour."""
        c = self.brain.drives()
        c["behavior"] = self.brain.behavior
        return c

    def _show_line(self, text):
        cx = self.x + DISP / 2
        self.bubble.show_text(text, cx, self.y() + 24)

    def mousePressEvent(self, e):
        if e.button() == Qt.RightButton:
            self._context_menu(e.globalPos())
            return
        res = self.brain.receive_pet()       # left-click = pet (or console if scared)
        if res == "consoled":
            self.cat.console()
        else:
            self.cat.pet()
        self.cat.report_outcome(1.0)         # attention -> the last line "landed"
        self.last_say = 0                    # let the reaction speak now
        self.update()

    def _context_menu(self, gpos):
        m = QMenu()
        m.setStyleSheet("QMenu{background:#1e2026;color:#eee;border:1px solid #555;}"
                        "QMenu::item:selected{background:#3a3f4b;}")
        a_feed = m.addAction("🍗  Feed")
        a_pet = m.addAction("✋  Pet")
        a_sleep = m.addAction("😴  Sleep")
        m.addSeparator()
        a_quit = m.addAction("✖  Quit")
        act = m.exec_(gpos)
        if act == a_feed:
            self.brain.feed(); self.cat.feed(); self.cat.report_outcome(1.0)
            self.last_say = 0
        elif act == a_pet:
            res = self.brain.receive_pet()
            self.cat.console() if res == "consoled" else self.cat.pet()
            self.cat.report_outcome(1.0); self.last_say = 0
        elif act == a_sleep:
            self.brain.force_sleep(); self.cat.say("sleep"); self.last_say = 0
        elif act == a_quit:
            STOP_FILE.write_text("stop")     # tell the watcher not to relaunch
            self.persist()
            QApplication.instance().quit()

    def enterEvent(self, _):
        self.hovering = True

    def leaveEvent(self, _):
        self.hovering = False
        self.info.hide()

    def _load_sprites(self):
        d = {}
        for f in glob.glob(str(SPRITE_DIR / "*.png")):
            name = os.path.splitext(os.path.basename(f))[0]
            pm = QPixmap(f).scaled(DISP, DISP, Qt.KeepAspectRatio,
                                   Qt.FastTransformation)
            d[name] = pm
        if not d:   # gitignored sprites missing on a fresh clone -> cat invisible
            msg = (f"[mascot] WARNING: no sprites in {SPRITE_DIR} — the cat will "
                   f"be invisible. Restore cat4_states/*.png (git add -f).\n")
            sys.stderr.write(msg)
            try:
                (Path(__file__).parent / "mascot.log").open("a", encoding="utf-8").write(msg)
            except Exception:
                pass
        return d

    def _sprite(self, name):
        """Return pixmap for name honoring facing; uses *-left mirror if present,
        else falls back to the right-facing/emotion sprite."""
        if self.facing == "left":
            alt = name.replace("run-", "run-left-").replace("walk-", "walk-left-")
            if alt in self.pix:
                return self.pix[alt]
        return self.pix.get(name)

    def _tick(self):
        now = time.time()
        dt = now - self.last_tick
        self.last_tick = now

        self._zcount = getattr(self, "_zcount", 0) + 1
        if self._zcount % 20 == 0:        # ~ every 2s, keep above the taskbar
            self._raise_above_taskbar()

        if now >= self.next_state_poll:        # throttle disk polling (~3/s)
            self._state = load_state()
            self.next_state_poll = now + 0.3
        st = self._state
        cs = st.get("currentState") if st else None
        self.cs = cs

        # react to Claude state *transitions* (talk + remember), throttled
        if cs != self.prev_cs:
            info = st.get("lastToolName") if st else None
            if cs == "tool_failure":
                self.brain.scare(45)         # errors startle the cat
                self.cat.report_outcome(0.0)  # a scare = the last line didn't help
            if cs in ("tool_success", "tool_failure", "question", "done", "auth_success"):
                self.cat.observe_claude(cs, info)
                evmap = {"tool_success": ("claude_success", 18),
                         "tool_failure": ("claude_failure", 6),
                         "question": ("claude_question", 14),
                         "done": ("claude_done", 25),
                         "auth_success": ("greet", 30)}
                ev, gap = evmap[cs]
                self._emote(ev, gap, self._ctx())
            self.prev_cs = cs

        # Claude busy -> react to real state; otherwise the brain drives.
        if cs and cs != "idle" and cs in REACTIVE:
            self.mode = "react"
            self.frames, self.period, self.speed = REACTIVE[cs]
        else:
            self.mode = "brain"
            self.brain.tick(dt)
            self.frames = self.brain.frames
            self.period = self.brain.period
            self.speed = self.brain.speed

        # reset frame index whenever the active clip changes
        key = id(self.frames)
        if key != self.src_key:
            self.src_key = key
            self.fi = 0
            self.last_frame_t = now

        if (now - self.last_frame_t) * 1000 >= self.period:
            self.fi = (self.fi + 1) % len(self.frames)
            self.last_frame_t = now

        # shape the window to the current sprite (click-through transparency)
        cur = self._sprite(self.frames[self.fi])
        if cur is not None and id(cur) != self._mask_key:
            self._mask_key = id(cur)
            self._apply_mask(cur)

        if self.speed:
            self.x += self.dir * self.speed
            self.facing = "left" if self.dir < 0 else "right"
            if self.x <= self.left_bound:
                self.x = self.left_bound; self.dir = 1
            elif self.x >= self.right_bound:
                self.x = self.right_bound; self.dir = -1
            self.move(int(self.x), self.y())

        # drive-driven self-expression (hunger / loneliness / fear)
        if self.mode == "brain":
            u = self.brain.urgent_drive()
            if u is None:
                self.prev_drive = None
                if now >= self.next_musing:       # idle self-talk
                    self.next_musing = now + random.uniform(45, 120)
                    self._emote("musing", 0, self._ctx())
            elif u != self.prev_drive:            # announce a new urgent need
                self.prev_drive = u
                ev = {"fear": "scared", "hunger": "hungry",
                      "social": "wants_attention"}[u]
                self._emote(ev, 8, self._ctx())

        # show any ready line; keep the bubble riding above the cat
        line = self.cat.poll()
        if line:
            self._show_line(line)
        elif self.bubble.isVisible():
            self.bubble.move(int(self.x + DISP / 2 - self.bubble.width() / 2),
                             self.bubble.y())

        # hover panel: what she's doing + drives + bond
        if self.hovering:
            if self.mode == "react":
                doing = REACT_DOING.get(self.cs, "with you")
            else:
                doing = DOING.get(self.brain.behavior, self.brain.behavior)
            self.info.update_info(doing, self.brain.drives(),
                                  int(self.cat.affection()),
                                  self.x + DISP / 2, self.y() - 4)

        # persist drives periodically so they survive the next restart
        if now >= self.next_save:
            self.next_save = now + 30
            self.persist()

        self.update()

    def y(self):
        # bottom taskbar (default): cat stands ON the bar's top edge, body above.
        # top taskbar: cat hangs just under the bar's bottom edge.
        if self.edge == ABE_TOP:
            return self.tb[1] + self.tb[3] - FOOT_OVERLAP
        return self.tb[1] - DISP + FOOT_OVERLAP

    def _apply_mask(self, pm):
        """Shape the window to the sprite's opaque pixels so clicks on the
        transparent area fall through to whatever is behind the cat."""
        try:
            offx = (DISP - pm.width()) // 2
            offy = DISP - pm.height()
            region = QRegion(pm.mask()).translated(offx, offy)
            self.setMask(region)
        except Exception:
            self.clearMask()

    def persist(self):
        """Save personality + current drives (called periodically and on quit)."""
        self.cat.set_drives(self.brain.snapshot_drives())
        self.cat.save()
        self.cat.save_know()

    def _raise_above_taskbar(self):
        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetWindowPos(
                hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def paintEvent(self, _):
        pm = self._sprite(self.frames[self.fi])
        if pm is None:
            return
        p = QPainter(self)
        p.drawPixmap((DISP - pm.width()) // 2, DISP - pm.height(), pm)
        p.end()


def _set_dpi_aware():
    """Make the process DPI-aware so the WinAPI taskbar rect and Qt geometry are
    both in real physical pixels (no virtualization) -> they stay aligned on
    scaled displays. Must run before QApplication is created."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor aware
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()     # older Windows fallback
        except Exception:
            pass


if __name__ == "__main__":
    _set_dpi_aware()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)
    m = Mascot()
    app.aboutToQuit.connect(m.persist)
    sys.exit(app.exec_())
