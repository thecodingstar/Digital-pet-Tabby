#!/usr/bin/env python3
"""Tabby taskbar mascot — renders the cat4_states sprite library on the Windows
taskbar and animates it from Claude Code activity state.

State files: ~/.claude/statusline/state/<session>.json (written by state_writer).
Sprites:     ./cat4_states/*.png  (384x384, transparent, bottom-aligned)

Run: python taskbar_mascot_cat.py
"""
import sys, time, ctypes, ctypes.wintypes, json, os, glob, random
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QWidget, QMenu, QPushButton
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import (QPixmap, QPainter, QColor, QFont, QFontMetrics,
                         QPainterPath, QPen, QRegion, QCursor)
from brain import Brain
from chatter import Cat

STOP_FILE = Path(__file__).parent / ".mascot_stop"   # touched by Quit menu

# what the cat is "doing" -> human phrase (for the hover panel)
DOING = {
    "idle": "just chilling", "sit": "sitting", "sleep": "napping",
    "stretch": "stretching", "wander": "wandering about", "zoomies": "ZOOMIES!",
    "play": "playing", "curious": "being nosy", "happy": "feeling happy",
    "think": "pondering", "groom": "grooming", "grumpy": "grumpy",
    "loaf": "loafing", "knead": "making biscuits", "ponder": "deep in thought",
    "watch": "watching you", "anticipate": "watching for you",
    "beg": "begging for food", "seek": "wants attention", "cower": "scared!",
    "startle": "startled!", "angry": "annoyed", "sad": "a bit down",
    "yawn": "yawning", "scratch": "scratching", "curl": "curled up napping",
    "hiss": "hissing!", "defensive_arch": "back arched!",
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


class QuestionBubble(QWidget):
    """Interactive card: Tabby asks a get-to-know-you question, the human clicks
    an answer. Real buttons (not click-through) so the answer registers; the
    card auto-dismisses if ignored so it never nags."""
    W = 252

    def __init__(self, on_answer):
        super().__init__()
        self.on_answer = on_answer            # callback(question, choice_idx)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.q = None
        self._btns = []
        self._qlines = []
        self.font = QFont("Segoe UI Semibold", 9)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self.dismiss)

    def _clear(self):
        for b in self._btns:
            b.deleteLater()
        self._btns = []

    def ask(self, q, cx, bottom_y):
        self._clear()
        self.q = q
        fm = QFontMetrics(self.font)
        words, lines, cur = q["text"].split(), [], ""
        for w in words:                       # wrap the question to the card width
            t = (cur + " " + w).strip()
            if fm.horizontalAdvance(t) > self.W - 24 and cur:
                lines.append(cur); cur = w
            else:
                cur = t
        if cur:
            lines.append(cur)
        self._qlines = lines
        pad = 10
        y = pad + fm.height() * len(lines) + 8
        bh = 26
        for i, opt in enumerate(q["options"][:3]):
            b = QPushButton(opt["label"], self)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#3a3f4b;color:#eaeaf0;border:1px solid #565b69;"
                "border-radius:8px;padding:4px;font:9pt 'Segoe UI';}"
                "QPushButton:hover{background:#4b5160;border-color:#6b7280;}")
            b.setGeometry(pad, y, self.W - 2 * pad, bh)
            b.clicked.connect(lambda _, k=i: self._answer(k))
            self._btns.append(b)
            y += bh + 6
        h = y + pad - 6
        self.setGeometry(int(cx - self.W / 2), int(bottom_y - h), self.W, h)
        self.update()
        self.show()
        self.raise_()
        self._timeout.start(30000)            # auto-dismiss after 30s

    def _answer(self, idx):
        cb, q = self.on_answer, self.q
        self.dismiss()
        if cb and q:
            cb(q, idx)

    def dismiss(self):
        self._timeout.stop()
        self.hide()
        self._clear()
        self.q = None

    def paintEvent(self, _):
        if not self.q:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        path = QPainterPath()
        path.addRoundedRect(0.0, 0.0, float(w - 1), float(h - 1), 12.0, 12.0)
        p.setPen(QPen(QColor(70, 74, 88), 1))
        p.setBrush(QColor(26, 28, 36, 246))
        p.drawPath(path)
        p.setFont(self.font)
        p.setPen(QColor(244, 230, 153))
        fm = QFontMetrics(self.font)
        y = 10 + fm.ascent()
        for ln in self._qlines:
            p.drawText(12, int(y), ln)
            y += fm.height()
        p.end()


class InfoPanel(QWidget):
    """Polished hover card: name + status, bond, and labelled drive meters."""
    BARS = [("energy", "Energy", QColor(80, 205, 130)),
            ("hunger", "Hunger", QColor(235, 170, 60)),
            ("social", "Social", QColor(95, 165, 235)),
            ("fear",   "Fear",   QColor(225, 95, 95))]
    MOOD_COLOR = {"content": QColor(120, 210, 150), "playful": QColor(95, 215, 205),
                  "sleepy": QColor(140, 160, 200), "hungry": QColor(235, 175, 70),
                  "lonely": QColor(110, 160, 235), "scared": QColor(230, 95, 95)}
    W, H = 224, 168

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.data = {}
        self.f_name = QFont("Segoe UI Semibold", 11)
        self.f_doing = QFont("Segoe UI", 8)
        self.f_label = QFont("Segoe UI", 8)
        self.f_val = QFont("Segoe UI Semibold", 8)

    def update_info(self, doing, drives, bond, cx, bottom_y):
        self.data = {"doing": doing, "drives": drives, "bond": bond}
        self.setGeometry(int(cx - self.W / 2), int(bottom_y - self.H), self.W, self.H)
        self.update()
        if not self.isVisible():
            self.show()

    def _meter(self, p, x, y, w, label, val, col):
        fm = QFontMetrics(self.f_label)
        p.setFont(self.f_label)
        p.setPen(QColor(176, 182, 196))
        p.drawText(x, y + 9, label)
        bx = x + 52
        bw = w - 52 - 34
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(48, 52, 64))                    # track
        p.drawRoundedRect(bx, y, bw, 9, 4, 4)
        fill = max(0, min(bw, int(bw * val / 100)))
        if fill > 0:
            p.setBrush(col)
            p.drawRoundedRect(bx, y, fill, 9, 4, 4)
        p.setFont(self.f_val)
        p.setPen(QColor(225, 228, 236))
        p.drawText(bx + bw + 6, y + 9, f"{val}")

    def paintEvent(self, _):
        if not self.data:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        d = self.data["drives"]
        mood = d.get("mood", "content")
        accent = self.MOOD_COLOR.get(mood, QColor(150, 160, 175))

        # card body + subtle border
        path = QPainterPath()
        path.addRoundedRect(1.0, 1.0, float(w - 2), float(h - 2), 12.0, 12.0)
        p.setPen(QPen(QColor(60, 64, 78), 1))
        p.setBrush(QColor(26, 28, 36, 245))
        p.drawPath(path)
        # mood accent strip down the left edge
        p.setPen(Qt.NoPen)
        p.setBrush(accent)
        p.drawRoundedRect(1, 1, 5, h - 2, 2, 2)

        # header: name + mood dot + status line
        x = 16
        p.setFont(self.f_name)
        p.setPen(QColor(245, 224, 150))
        p.drawText(x, 24, "Tabby")
        nm = QFontMetrics(self.f_name).horizontalAdvance("Tabby")
        p.setBrush(accent)
        p.setPen(Qt.NoPen)
        p.drawEllipse(x + nm + 8, 13, 8, 8)               # mood dot
        p.setFont(self.f_doing)
        p.setPen(QColor(150, 156, 170))
        p.drawText(x + nm + 22, 23, mood)
        p.setPen(QColor(196, 202, 214))
        p.drawText(x, 40, self.data["doing"])

        # bond row (heart-coloured)
        bond = int(self.data["bond"])
        self._meter(p, x, 52, w - x - 12, "Bond", bond, QColor(235, 110, 140))

        # drive meters
        y = 74
        for key, label, col in self.BARS:
            self._meter(p, x, y, w - x - 12, label, int(d.get(key, 0)), col)
            y += 21
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
        self.brain.apply_hints(self.cat.behavior_hints())   # X7: learned -> behaviour
        self.bubble = SpeechBubble()
        self.info = InfoPanel()
        self.qbubble = QuestionBubble(self._on_quiz_answer)   # interactive quiz (C)
        self.next_quiz_check = time.time() + 60               # first check after 1 min
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

    def _auto_ask(self, now):
        """Periodically offer a question (any mode, not just idle). Internally
        gated by cooldown/affection so it asks now and then, never nags (C)."""
        if now >= self.next_quiz_check:
            self.next_quiz_check = now + random.uniform(25, 45)   # jittered cadence
            self.cat.maybe_ask()

    def _pump_quiz(self):
        """Surface a question the worker has built (manual or auto). She sits
        still while it's on screen so the answer buttons stay put (C)."""
        if self.qbubble.q is not None:            # already on screen
            self.brain._set("sit")
            return
        q = self.cat.poll_question()
        if q:
            self.brain._set("sit")
            self.qbubble.ask(q, self.x + DISP / 2, self.y() - 4)

    def _on_quiz_answer(self, q, idx):
        line = self.cat.answer_question(q, idx)
        self.brain.apply_hints(self.cat.behavior_hints())   # X8: act on it right away
        if line:
            self._show_line(line)
            self.last_say = 0

    def _notice_cursor(self):
        """If the mouse comes near her on the taskbar, she turns to look and
        perks up (curious) — a light, no-UI way to feel the human's presence."""
        if self.qbubble.q is not None:
            return
        try:
            p = QCursor.pos()
        except Exception:
            return
        cat_cx = self.x + DISP / 2
        if abs(p.x() - cat_cx) < 160 and abs(p.y() - (self.y() + DISP)) < 160:
            self.facing = "left" if p.x() < cat_cx else "right"
            if (self.brain.behavior not in ("watch", "curious", "seek", "cower")
                    and random.random() < 0.05):   # an occasional glance, not a stare
                self.brain._set("watch")

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
        a_ask = m.addAction("💬  Ask me something")
        a_stats = m.addAction("📊  Stats")
        m.addSeparator()
        a_quit = m.addAction("✖  Quit")
        act = m.exec_(gpos)
        if act == a_ask:
            self.cat.ask_now()               # force a quiz card (manual trigger)
        elif act == a_stats:
            self._open_dashboard()
        elif act == a_feed:
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
            self.qbubble.dismiss()
            self.persist()
            QApplication.instance().quit()

    def _open_dashboard(self):
        """Regenerate the brain dashboard from the latest state and open it."""
        try:
            self.persist()                   # flush newest data to disk first
            import webbrowser, dashboard
            webbrowser.open(dashboard.build().as_uri())
        except Exception as e:
            sys.stderr.write(f"[mascot] dashboard failed: {e}\n")

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
        # A raise here would silently kill the QTimer and freeze the cat, so the
        # game loop is exception-guarded: log and retry on the next tick.
        try:
            self._tick_impl()
        except Exception as e:
            sys.stderr.write(f"[mascot] tick error: {e!r}\n")

    def _tick_impl(self):
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
            style = self.brain.hints.get("prefs", {}).get("comfort_style")  # X9
            if cs == "tool_failure":
                self.brain.scare(45)         # errors startle her (modulated in scare)
                self.cat.report_outcome(0.0)  # a scare = the last line didn't help
                if style == "cheer":         # they want cheering -> speak now
                    self.last_say = 0
            if cs in ("tool_success", "tool_failure", "question", "done", "auth_success"):
                if cs in ("tool_success", "tool_failure", "question", "done"):
                    self.brain.note_activity()   # X9: learn coding hours from coding
                self.cat.observe_claude(cs, info)
                evmap = {"tool_success": ("claude_success", 18),
                         "tool_failure": ("claude_failure", 6),
                         "question": ("claude_question", 14),
                         "done": ("claude_done", 25),
                         "auth_success": ("greet", 30)}
                ev, gap = evmap[cs]
                if not (cs == "tool_failure" and style == "space"):   # quiet on errors
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

        # quiz runs in any mode now (asks at random, not only when idle)
        self._auto_ask(now)
        self._pump_quiz()

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

        if self.speed and self.qbubble.q is None:     # don't wander off the quiz card
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
                if now >= self.next_musing:       # idle self-talk (cadence adapts
                    qf = self.cat.quiet_factor()  # to her learned chattiness)
                    self.next_musing = now + random.uniform(45, 120) * qf
                    self._emote("musing", 0, self._ctx())
                self._notice_cursor()            # perk up when the mouse is near (B)
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
            elif self.brain.pre_active():        # X10: anticipating your session
                doing = "watching for you"
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
        """Save personality + current drives (called periodically and on quit).
        Also refresh behaviour hints on this ~30s cadence (X8) — it takes the
        chatter lock, so never per tick."""
        self.cat.set_drives(self.brain.snapshot_drives())
        self.brain.apply_hints(self.cat.behavior_hints())
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
