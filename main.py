"""
main.py
Application Entry Point for YouTube Music Desktop Player Widget.
Coordinates:
- QApplication lifecycle
- AudioEngine initialization
- MusicPlayerWidget placement docked above the taskbar
- System Tray integration with quick controls

Creates the ARSH desktop player and system tray controls.
"""

import sys
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QAction, QPixmap, QPainter, QColor, QFont
from PyQt6.QtCore import Qt
from audio_engine import AudioEngine
from ui_widget import MusicPlayerWidget


def create_tray_icon_pixmap() -> QPixmap:
    """Generates a sleek procedural tray icon without requiring external asset files."""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # Gradient/purple circle background
    painter.setBrush(QColor("#8b5cf6"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(2, 2, 28, 28, 14, 14)

    # White play triangle
    painter.setBrush(QColor("#ffffff"))
    painter.setFont(QFont("Arial", 14, QFont.Weight.Bold))
    painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, "▶")
    painter.end()
    return pixmap


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("ARSH")
    app.setQuitOnLastWindowClosed(False)

    # Initialize Engine & Main UI Widget
    engine = AudioEngine()
    widget = MusicPlayerWidget(engine)

    # Position the widget docked right above the taskbar in the empty area
    widget.show()
    widget.adjustSize()

    screen = app.primaryScreen().availableGeometry()
    # Sits nicely in the right-center region above the taskbar
    default_x = max(screen.left() + 20, screen.right() - 580)
    default_y = screen.bottom() - widget.height() - 12
    widget.move(default_x, default_y)
    widget.clamp_to_screen()

    # System Tray Setup
    tray_icon = QSystemTrayIcon()
    tray_pixmap = create_tray_icon_pixmap()
    tray_icon.setIcon(QIcon(tray_pixmap))
    tray_icon.setToolTip("ARSH")

    tray_menu = QMenu()

    action_play = QAction("Play / Pause", tray_menu)
    action_play.triggered.connect(engine.toggle_play_pause)
    tray_menu.addAction(action_play)

    action_next = QAction("Next Track", tray_menu)
    action_next.triggered.connect(engine.play_next)
    tray_menu.addAction(action_next)

    action_prev = QAction("Previous Track", tray_menu)
    action_prev.triggered.connect(engine.play_previous)
    tray_menu.addAction(action_prev)

    tray_menu.addSeparator()

    action_show = QAction("Show / Focus Widget", tray_menu)
    action_show.triggered.connect(lambda: (widget.show(), widget.raise_()))
    tray_menu.addAction(action_show)

    action_quit = QAction("Quit", tray_menu)
    action_quit.triggered.connect(lambda: (engine.player.stop(), app.quit()))
    tray_menu.addAction(action_quit)

    tray_icon.setContextMenu(tray_menu)
    tray_icon.show()

    # Run Event Loop
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
