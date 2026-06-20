#!/usr/bin/env python3
"""Windows taskbar-anchored pixel-buddy mascot with emotions.

Loads mascot state from statusline/state/<session_id>.json (written by state_writer.py).
Renders the sprite on the taskbar, walking during work states and reacting to events.

PyQt5 required: pip install PyQt5
"""
import sys, json, os, time, glob, ctypes, ctypes.wintypes
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu
from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QSize
from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon, QCursor
from PyQt5.QtCore import pyqtSignal

# ---- Taskbar detection ----
ABM_GETTASKBARPOS = 5

class APPBARDATA(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.wintypes.DWORD),
                ("hWnd", ctypes.wintypes.HWND),
                ("uCallbackMessage", ctypes.wintypes.UINT),
                ("uEdge", ctypes.wintypes.UINT),
                ("rc", ctypes.wintypes.RECT),
                ("lParam", ctypes.wintypes.LPARAM)]

def get_taskbar_rect():
    """Return (left, top, width, height) of Windows taskbar."""
    try:
        abd = APPBARDATA()
        abd.cbSize = ctypes.sizeof(APPBARDATA)
        ctypes.windll.shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(abd))
        r = abd.rc
        return r.left, r.top, r.right - r.left, r.bottom - r.top
    except Exception:
        return 0, 1050, 1920, 30

# ---- Sprite rendering ----
def _hexrgb(c):
    return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16))

def _heat_palette(palette, used_pct):
    if used_pct is None or used_pct <= 60:
        return palette
    t = min(1.0, (used_pct - 60) / 25.0)
    orig = palette[2]
    if not orig:
        return palette
    r, g, b = _hexrgb(orig)
    nr = round(r + (255 - r) * t); ng = round(g + (68 - g) * t); nb = round(b + (68 - b) * t)
    out = list(palette)
    out[2] = f"#{nr:02x}{ng:02x}{nb:02x}"
    return out

def _downsample(sprite, factor=2):
    H = len(sprite)
    W = len(sprite[0]) if H else 0
    out = []
    for y in range(0, H, factor):
        row = []
        for x in range(0, W, factor):
            cands = []
            for dy in range(factor):
                for dx in range(factor):
                    yy, xx = y + dy, x + dx
                    if yy < H and xx < len(sprite[yy]):
                        v = sprite[yy][xx]
                        if v:
                            cands.append(v)
            if not cands:
                row.append(0)
            else:
                best, bc = 0, -1
                for v in set(cands):
                    n = cands.count(v)
                    if n > bc:
                        bc, best = n, v
                row.append(best)
        out.append(row)
    return out

_HOLDS = {"tool_success": 1600, "tool_failure": 1800, "done": 2500, "auth_success": 2000}
_PERIODS = {"idle": 1200, "thinking": 350, "tool_running": 250, "subagent_running": 250}

def _effective_state(st, now_s):
    if not st:
        return "idle", 0
    if now_s - float(st.get("lastUpdatedAt", 0)) > 600:
        return "idle", 0
    cs = st.get("currentState", "idle")
    sub = int(st.get("activeSubagentCount", 0) or 0)
    hold = _HOLDS.get(cs)
    changed = float(st.get("lastStateChangedAt", 0))
    if hold and (now_s - changed) * 1000 >= hold:
        if sub > 0:
            return "subagent_running", sub
        if cs in ("question", "permission"):
            return "thinking", sub
        return "idle", sub
    return cs, sub

class SpriteRenderer:
    def __init__(self):
        pack_path = Path(__file__).parent / "statusline" / "mascot_pack.json"
        with open(pack_path, encoding="utf-8") as f:
            self.pack = json.load(f)
        self.cached_icon = None

    def get_frame_data(self, state, now_s, ctx_pct, mirror=False):
        """Return (sprite_grid, palette) for current frame."""
        pal = _heat_palette(self.pack["sprite"]["palette"], ctx_pct)
        frames = self.pack["states"].get(state) or self.pack["states"]["idle"]
        period = _PERIODS.get(state, 600)
        frame_idx = int(now_s * 1000 / period) % len(frames)
        sprite = _downsample(self.pack["sprites"][frames[frame_idx]], 2)

        if mirror:
            sprite = [list(reversed(r)) for r in sprite]

        return sprite, pal

    def render_frame_to_pixmap(self, state, now_s, ctx_pct, mirror=False):
        """Return QPixmap of the sprite at current frame."""
        sprite, pal = self.get_frame_data(state, now_s, ctx_pct, mirror)
        W = len(sprite[0]) if sprite else 0
        H = len(sprite)
        scale = 6
        pixmap = QPixmap(W * scale, H * scale)
        pixmap.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pixmap)
        for ri in range(0, H, 2):
            for ci in range(W):
                top_idx = sprite[ri][ci] if ri < len(sprite) else 0
                bot_idx = sprite[ri + 1][ci] if ri + 1 < len(sprite) else 0

                top_col = pal[top_idx] if top_idx else None
                bot_col = pal[bot_idx] if bot_idx else None

                x, y = ci * scale, (ri // 2) * scale * 2

                if top_col and bot_col and top_col == bot_col:
                    r, g, b = _hexrgb(top_col)
                    painter.fillRect(x, y, scale, scale * 2, QColor(r, g, b))
                elif top_col and bot_col:
                    r, g, b = _hexrgb(top_col)
                    painter.fillRect(x, y, scale, scale, QColor(r, g, b))
                    r, g, b = _hexrgb(bot_col)
                    painter.fillRect(x, y + scale, scale, scale, QColor(r, g, b))
                elif top_col:
                    r, g, b = _hexrgb(top_col)
                    painter.fillRect(x, y, scale, scale, QColor(r, g, b))
                elif bot_col:
                    r, g, b = _hexrgb(bot_col)
                    painter.fillRect(x, y + scale, scale, scale, QColor(r, g, b))

        painter.end()
        return pixmap

    def get_icon(self):
        """Return a 16x16 QPixmap for system tray (cached)."""
        if self.cached_icon:
            return self.cached_icon

        sprite = self.pack["sprites"]["idle_1"]
        sprite = _downsample(sprite, 2)
        pal = self.pack["sprite"]["palette"]

        W, H = len(sprite[0]) if sprite else 0, len(sprite)
        pixmap = QPixmap(W, H)
        pixmap.fill(QColor(0, 0, 0, 0))

        painter = QPainter(pixmap)
        for ri in range(H):
            for ci in range(W):
                col_idx = sprite[ri][ci]
                if col_idx:
                    col = pal[col_idx]
                    r, g, b = _hexrgb(col)
                    painter.fillRect(ci, ri, 1, 1, QColor(r, g, b))
        painter.end()

        self.cached_icon = pixmap
        return pixmap

# ---- State loading ----
def _load_active_state(state_dir):
    """Load most recent session state, None if stale (>600s old)."""
    try:
        # Check project dir first, then ~/.claude/statusline/state/
        dirs = [state_dir]
        if state_dir != str(Path.home() / ".claude" / "statusline" / "state"):
            dirs.append(Path.home() / ".claude" / "statusline" / "state")

        all_files = []
        for d in dirs:
            all_files.extend(glob.glob(os.path.join(d, "*.json")))

        if not all_files:
            return None
        newest = max(all_files, key=os.path.getmtime)
        if time.time() - os.path.getmtime(newest) > 600:
            return None
        with open(newest, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None

# ---- Mascot window ----
class MascotWindow(QWidget):
    def __init__(self):
        try:
            super().__init__()
            self.renderer = SpriteRenderer()
            self.state = None
            self.state_dir = Path(__file__).parent / "statusline" / "state"
            self.state_dir.mkdir(parents=True, exist_ok=True)

            self.taskbar_left, self.taskbar_top, self.taskbar_w, self.taskbar_h = get_taskbar_rect()
            self.mascot_w, self.mascot_h = 96, 96

            self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
            self.setAttribute(Qt.WA_TranslucentBackground)

            # Position ON the taskbar (bottom-right), not above it
            # Offset: 10px from right edge, 5px from top of taskbar
            x = self.taskbar_left + self.taskbar_w - self.mascot_w - 10
            y = self.taskbar_top + 5

            import sys
            with open("mascot_init.log", "w") as f:
                f.write(f"Taskbar: ({self.taskbar_left}, {self.taskbar_top}) {self.taskbar_w}x{self.taskbar_h}\n")
                f.write(f"Window pos: ({x}, {y}) {self.mascot_w}x{self.mascot_h}\n")

            self.setGeometry(x, y, self.mascot_w, self.mascot_h)

            self.walk_offset = 0
            self.emotion = "idle"
            self.ctx_pct = 0
            self.is_dragging = False
            self.drag_start = None

            self.anim_timer = QTimer()
            self.anim_timer.timeout.connect(self.animate)
            self.anim_timer.start(50)

            self.state_timer = QTimer()
            self.state_timer.timeout.connect(self.update_state)
            self.state_timer.start(500)

            # Tray icon
            self.tray = QSystemTrayIcon(self)
            self.tray.setIcon(QIcon(self.renderer.get_icon()))
            menu = QMenu(self)
            menu.addAction("Quit", self.quit_app)
            self.tray.setContextMenu(menu)
            self.tray.show()

            self.show()
            with open("mascot_init.log", "a") as f:
                f.write("Window shown OK\n")
        except Exception as e:
            import traceback
            with open("mascot_init_error.log", "w") as f:
                f.write(f"Init failed: {e}\n{traceback.format_exc()}\n")
            raise

    def update_state(self):
        try:
            self.state = _load_active_state(str(self.state_dir))
            if self.state:
                now = time.time()
                state_name, _ = _effective_state(self.state, now)
                self.emotion = state_name
                self.ctx_pct = self.state.get("ctx_pct", 0)
        except Exception as e:
            with open("mascot_state_error.log", "a") as f:
                f.write(f"update_state error: {e}\n")

    def animate(self):
        try:
            now = time.time()

            if self.state:
                state_name, _ = _effective_state(self.state, now)
                if state_name in ("thinking", "tool_running", "subagent_running"):
                    span = 10
                    ph = int(now * 4) % (2 * span)
                    self.walk_offset = ph if ph < span else (2 * span - ph)
                else:
                    self.walk_offset = 0

            self.update()
        except Exception as e:
            with open("mascot_animate_error.log", "a") as f:
                f.write(f"animate error: {e}\n")

    def paintEvent(self, event):
        try:
            now = time.time()
            state_name = self.emotion or "idle"
            mirror = self.walk_offset > 5 if state_name in ("thinking", "tool_running", "subagent_running") else False

            # Draw semi-transparent background for testing
            painter = QPainter(self)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 50))

            # Draw mascot sprite
            pixmap = self.renderer.render_frame_to_pixmap(state_name, now, self.ctx_pct, mirror=mirror)
            painter.drawPixmap(self.walk_offset * 6, 0, pixmap)
            painter.end()

            self.tray.setToolTip(self.emotion)
        except Exception as e:
            with open("mascot_paint_error.log", "a") as f:
                import traceback
                f.write(f"paint error: {e}\n{traceback.format_exc()}\n")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_start = event.globalPos() - self.pos()

    def mouseMoveEvent(self, event):
        if self.is_dragging:
            self.move(event.globalPos() - self.drag_start)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.is_dragging = False

    def mouseDoubleClickEvent(self, event):
        pass

    def quit_app(self):
        self.tray.hide()
        QApplication.quit()

if __name__ == "__main__":
    try:
        log_file = Path(__file__).parent / "mascot_error.log"
        with open(log_file, "a") as f:
            f.write(f"\n=== Start {time.time()} ===\n")

        app = QApplication(sys.argv)
        window = MascotWindow()
        with open(log_file, "a") as f:
            f.write(f"Window created OK\n")
        sys.exit(app.exec_())
    except Exception as e:
        import traceback
        log_file = Path(__file__).parent / "mascot_error.log"
        with open(log_file, "a") as f:
            f.write(f"ERROR: {e}\n{traceback.format_exc()}\n")
        sys.exit(1)
