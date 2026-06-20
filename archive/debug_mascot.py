#!/usr/bin/env python3
"""Debug version of mascot that logs everything."""
import sys, json, os, time, glob, ctypes, ctypes.wintypes
from pathlib import Path

log = open("debug.log", "w")

def log_msg(msg):
    print(msg, file=log)
    log.flush()

log_msg(f"Starting at {time.time()}")

log_msg("Importing PyQt5...")
try:
    from PyQt5.QtWidgets import QApplication, QWidget, QSystemTrayIcon, QMenu
    from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QSize
    from PyQt5.QtGui import QPixmap, QPainter, QColor, QIcon
    log_msg("PyQt5 imported OK")
except Exception as e:
    log_msg(f"PyQt5 import failed: {e}")
    sys.exit(1)

log_msg("Testing taskbar detection...")
try:
    ABM_GETTASKBARPOS = 5
    class APPBARDATA(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.wintypes.DWORD),
                    ("hWnd", ctypes.wintypes.HWND),
                    ("uCallbackMessage", ctypes.wintypes.UINT),
                    ("uEdge", ctypes.wintypes.UINT),
                    ("rc", ctypes.wintypes.RECT),
                    ("lParam", ctypes.wintypes.LPARAM)]

    abd = APPBARDATA()
    abd.cbSize = ctypes.sizeof(APPBARDATA)
    ctypes.windll.shell32.SHAppBarMessage(ABM_GETTASKBARPOS, ctypes.byref(abd))
    r = abd.rc
    log_msg(f"Taskbar: ({r.left}, {r.top}, {r.right}, {r.bottom})")
except Exception as e:
    log_msg(f"Taskbar detection failed: {e}")
    sys.exit(1)

log_msg("Creating QApplication...")
try:
    app = QApplication(sys.argv)
    log_msg("QApplication created OK")
except Exception as e:
    log_msg(f"QApplication failed: {e}")
    import traceback
    log_msg(traceback.format_exc())
    sys.exit(1)

log_msg("Creating window...")
try:
    w = QWidget()
    w.setWindowTitle("Debug Mascot")
    w.setGeometry(100, 100, 100, 100)
    w.show()
    log_msg("Window shown")
except Exception as e:
    log_msg(f"Window creation failed: {e}")
    import traceback
    log_msg(traceback.format_exc())
    sys.exit(1)

log_msg("Starting event loop...")
try:
    log_msg("About to call app.exec_()")
    result = app.exec_()
    log_msg(f"app.exec_() returned {result}")
except Exception as e:
    log_msg(f"Event loop failed: {e}")
    import traceback
    log_msg(traceback.format_exc())

log_msg("Done")
log.close()
