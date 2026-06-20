#!/usr/bin/env python3
"""Taskbar mascot v2 - simplified without tray icon."""
import sys, time, ctypes, ctypes.wintypes, json, os, glob
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter, QFont

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
    except:
        return 0, 1050, 1920, 30

def load_state():
    """Load latest state file."""
    state_dir = Path.home() / ".claude" / "statusline" / "state"
    try:
        files = glob.glob(str(state_dir / "*.json"))
        if not files:
            return None
        newest = max(files, key=os.path.getmtime)
        if time.time() - os.path.getmtime(newest) > 600:
            return None
        with open(newest) as f:
            return json.load(f)
    except:
        return None

class MascotWindow(QWidget):
    def __init__(self):
        super().__init__()
        tb_left, tb_top, tb_w, tb_h = get_taskbar_rect()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        w, h = 120, 60
        x = tb_left + tb_w - w - 10
        y = tb_top + 5

        self.setGeometry(x, y, w, h)
        self.emotion = "idle"
        self.colors = {
            "idle": QColor(100, 150, 200),
            "thinking": QColor(200, 100, 255),
            "tool_running": QColor(255, 150, 50),
            "tool_success": QColor(100, 255, 100),
            "tool_failure": QColor(255, 100, 100),
            "done": QColor(100, 200, 255),
        }

        self.timer = QTimer()
        self.timer.timeout.connect(self.update_and_render)
        self.timer.start(100)

        self.show()

    def update_and_render(self):
        state = load_state()
        if state:
            self.emotion = state.get("currentState", "idle")
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        color = self.colors.get(self.emotion, QColor(128, 128, 128))
        painter.fillRect(self.rect(), color)

        font = QFont("Arial", 10)
        painter.setFont(font)
        painter.drawText(self.rect(), Qt.AlignCenter, self.emotion.replace("_", "\n"))
        painter.end()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MascotWindow()
    sys.exit(app.exec_())
