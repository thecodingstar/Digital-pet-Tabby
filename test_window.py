#!/usr/bin/env python3
"""Minimal test window - verify PyQt5 works with taskbar positioning."""
import sys
from PyQt5.QtWidgets import QApplication, QWidget
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor

app = QApplication(sys.argv)

# Create simple colored window
window = QWidget()
window.setWindowTitle("Mascot Test Window")
window.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)

# Position at taskbar right edge
window.setGeometry(3300, 1280, 120, 100)

# Solid color background (not transparent) so we can see it
window.setStyleSheet("background-color: red;")

print("Window created and positioned at (3300, 1280)")
print("Should see red rectangle at taskbar right edge")

window.show()
sys.exit(app.exec_())
