import sys
import json
import random
from pathlib import Path
from PyQt5.QtWidgets import QApplication, QWidget, QLabel
from PyQt5.QtCore import Qt, QTimer, QPoint, pyqtSignal
from PyQt5.QtGui import QFont, QColor
from PyQt5.QtCore import QThread


class MascotWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)

        # Mascot label
        self.mascot = QLabel("😊")
        self.mascot.setFont(QFont("Arial", 60))
        self.mascot.setParent(self)

        # Status label (below mascot)
        self.status = QLabel("Idle")
        self.status.setFont(QFont("Arial", 10))
        self.status.setStyleSheet("color: white;")
        self.status.setParent(self)

        # State
        self.emotion = "happy"  # happy, thinking, done, error
        self.x = 100
        self.y = 100
        self.vx = random.choice([-2, 2])
        self.vy = random.choice([-1, 1])
        self.drag_start = None
        self.is_dragging = False

        self.setGeometry(self.x, self.y, 100, 120)
        self.show()

        # Animation timer
        self.timer = QTimer()
        self.timer.timeout.connect(self.animate)
        self.timer.start(50)

        # File watcher for state changes
        self.state_file = Path.home() / ".claude" / "mascot_state.json"
        self.file_timer = QTimer()
        self.file_timer.timeout.connect(self.check_state_file)
        self.file_timer.start(500)

    def animate(self):
        """Move mascot around screen"""
        self.x += self.vx
        self.y += self.vy

        # Bounce off edges (assumes ~1920x1080, leave space for taskbar)
        if self.x < 0 or self.x > 1820:
            self.vx *= -1
        if self.y < 0 or self.y > 930:
            self.vy *= -1

        self.setGeometry(self.x, self.y, 100, 120)

    def check_state_file(self):
        """Poll for state changes from Claude Code hook"""
        if self.state_file.exists():
            try:
                with open(self.state_file, 'r') as f:
                    state = json.load(f)
                    self.set_emotion(state.get("emotion", "happy"))
                    self.status.setText(state.get("status", ""))
            except json.JSONDecodeError:
                pass

    def set_emotion(self, emotion):
        """Update mascot appearance based on emotion"""
        if emotion == self.emotion:
            return

        self.emotion = emotion
        emotions = {
            "happy": ("😊", QColor(255, 200, 0)),
            "thinking": ("🤔", QColor(100, 150, 255)),
            "done": ("🎉", QColor(0, 255, 0)),
            "error": ("😞", QColor(255, 100, 100)),
            "working": ("⚙️", QColor(200, 200, 200)),
        }

        emoji, color = emotions.get(emotion, ("😐", QColor(128, 128, 128)))
        self.mascot.setText(emoji)
        self.status.setStyleSheet(f"color: rgb({color.red()}, {color.green()}, {color.blue()});")

    def mousePressEvent(self, event):
        """Start dragging"""
        if event.button() == Qt.LeftButton:
            self.is_dragging = True
            self.drag_start = event.globalPos() - self.pos()
            self.vx = 0
            self.vy = 0

    def mouseMoveEvent(self, event):
        """Drag mascot"""
        if self.is_dragging:
            self.move(event.globalPos() - self.drag_start)
            self.x = self.pos().x()
            self.y = self.pos().y()

    def mouseReleaseEvent(self, event):
        """Stop dragging, resume movement"""
        if event.button() == Qt.LeftButton:
            self.is_dragging = False
            self.vx = random.choice([-2, 2])
            self.vy = random.choice([-1, 1])

    def mouseDoubleClickEvent(self, event):
        """Toggle pause on double-click"""
        if self.vx == 0 and self.vy == 0:
            self.vx = random.choice([-2, 2])
            self.vy = random.choice([-1, 1])
        else:
            self.vx = 0
            self.vy = 0


def write_state(emotion, status):
    """Helper to set mascot state from Claude Code hook"""
    state_file = Path.home() / ".claude" / "mascot_state.json"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    with open(state_file, 'w') as f:
        json.dump({"emotion": emotion, "status": status}, f)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MascotWindow()
    sys.exit(app.exec_())
