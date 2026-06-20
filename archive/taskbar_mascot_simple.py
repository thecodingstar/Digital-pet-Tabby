#!/usr/bin/env python3
"""Minimal mascot - just a colored box on taskbar."""
import sys, time, ctypes, ctypes.wintypes
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QColor, QPainter

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

class MascotWindow(QWidget):
    def __init__(self):
        super().__init__()
        tb_left, tb_top, tb_w, tb_h = get_taskbar_rect()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        w, h = 100, 50
        x = tb_left + tb_w - w - 10
        y = tb_top + 5

        self.setGeometry(x, y, w, h)
        self.color = QColor(100, 200, 255)
        self.show()

        self.timer = QTimer()
        self.timer.timeout.connect(self.animate)
        self.timer.start(100)

    def animate(self):
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.color)
        painter.drawText(self.rect(), Qt.AlignCenter, "MASCOT")
        painter.end()

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        w = MascotWindow()
        sys.exit(app.exec_())
    except Exception as e:
        with open("simple_error.log", "w") as f:
            f.write(str(e))
        sys.exit(1)
