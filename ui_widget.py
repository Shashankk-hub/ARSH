"""
Frameless, Always-on-Top floating music widget.

- Collapsed: a circular "bubble" showing the current track's artwork.
- Expanded: a frosted-glass card with search, seek, controls
  (shuffle / prev / play / next / repeat), volume, and an "Up Next" queue.
"""

import os
import requests
import math
from urllib.parse import urlsplit
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QSize, QRectF, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QImage, QPainter, QPainterPath, QColor, QCursor, QLinearGradient
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QLineEdit,
    QSlider, QFrame, QScrollArea, QGraphicsDropShadowEffect
)
from audio_engine import AudioEngine, TrackInfo


def format_ms(ms: int) -> str:
    seconds = max(0, ms // 1000)
    minutes = seconds // 60
    secs = seconds % 60
    return f"{minutes:02d}:{secs:02d}"



def crop_to_fill(pixmap: QPixmap, width: int, height: int) -> QPixmap:
    scaled = pixmap.scaled(
        width,
        height,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation,
    )

    left = max(0, (scaled.width() - width) // 2)
    top = max(0, (scaled.height() - height) // 2)

    return scaled.copy(left, top, width, height)

def get_rounded_pixmap(pixmap: QPixmap, radius: int = 8) -> QPixmap:
    """Rounds the corners of a QPixmap. Pass radius == size/2 for a perfect circle."""
    if pixmap.isNull():
        return pixmap
    rounded = QPixmap(pixmap.size())
    rounded.fill(Qt.GlobalColor.transparent)
    painter = QPainter(rounded)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(0, 0, pixmap.width(), pixmap.height(), radius, radius)
    painter.setClipPath(path)
    painter.drawPixmap(0, 0, pixmap)
    painter.end()
    return rounded


def create_logo_pixmap(size: int) -> QPixmap:
    """
    Procedurally draws the ARSH mark: three rounded vertical bars of
    varying height (a simple audio/equalizer glyph), filled solid white.
    Drawn with QPainter instead of an emoji/Unicode glyph so it renders
    identically and crisply on every system, regardless of installed
    fonts -- this is what was showing up inconsistently before.
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(255, 255, 255, 240))

    bar_width = size * 0.16
    gap = size * 0.12
    radius = bar_width / 2
    heights = [0.42, 0.78, 0.58]  # short, tall, medium -- classic eq look
    total_width = bar_width * 3 + gap * 2
    start_x = (size - total_width) / 2
    baseline_y = size * 0.76
    usable_h = size * 0.56

    for i, ratio in enumerate(heights):
        bar_h = max(bar_width, usable_h * ratio)
        x = start_x + i * (bar_width + gap)
        y = baseline_y - bar_h
        painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), radius, radius)

    painter.end()
    return pixmap


class ClickableSlider(QSlider):
    clicked = pyqtSignal(int)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            val = self.minimum() + ((self.maximum() - self.minimum()) * event.pos().x()) // max(1, self.width())
            self.setValue(val)
            self.clicked.emit(val)
        super().mousePressEvent(event)


class QueueItemWidget(QFrame):
    def __init__(self, index: int, track: TrackInfo, is_current: bool, on_click_callback):
        super().__init__()
        self.track = track
        self.on_click_callback = on_click_callback
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(10)

        self.lbl_num = QLabel(f"{index + 1}" if not is_current else "▶")
        self.lbl_num.setFixedWidth(20)
        self.lbl_num.setStyleSheet(
            "color: #ffffff; font-weight: bold;" if is_current else "color: rgba(255,255,255,0.6); font-size: 11px;"
        )
        layout.addWidget(self.lbl_num)

        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        self.lbl_title = QLabel(track.title)
        self.lbl_title.setStyleSheet(
            "color: #ffffff; font-weight: 600; font-size: 12px;" if is_current else "color: rgba(255,255,255,0.85); font-size: 12px;"
        )
        self.lbl_title.setWordWrap(False)

        self.lbl_artist = QLabel(track.artist)
        self.lbl_artist.setStyleSheet("color: rgba(255,255,255,0.6); font-size: 11px;")
        self.lbl_artist.setWordWrap(False)

        info_layout.addWidget(self.lbl_title)
        info_layout.addWidget(self.lbl_artist)
        layout.addLayout(info_layout, 1)

        if track.duration:
            self.lbl_dur = QLabel(track.duration)
            self.lbl_dur.setStyleSheet("color: rgba(255,255,255,0.5); font-size: 11px;")
            layout.addWidget(self.lbl_dur)

        if is_current:
            self.setStyleSheet("""
                QFrame { background: rgba(255, 255, 255, 0.18); border-radius: 8px; }
            """)
        else:
            self.setStyleSheet("""
                QFrame { background: transparent; border-radius: 8px; }
                QFrame:hover { background: rgba(255, 255, 255, 0.10); }
            """)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.on_click_callback(self.track)
        super().mousePressEvent(event)


# Reusable style strings for the shuffle/repeat toggle buttons
TOGGLE_OFF_STYLE = (
    "QPushButton { background: rgba(255,255,255,0.15); color: rgba(255,255,255,0.85);"
    " border: none; border-radius: 15px; font-size: 13px; }"
    "QPushButton:hover { background: rgba(255,255,255,0.25); }"
)
TOGGLE_ON_STYLE = (
    "QPushButton { background: #22c55e; color: #ffffff;"
    " border: none; border-radius: 15px; font-size: 13px; }"
    "QPushButton:hover { background: #16a34a; }"
)


class EqualizerOverlay(QWidget):
    """
    Small animated "now playing" overlay: three bars that bounce up and
    down over the bubble's artwork/logo while a track is actively playing.
    Pure QPainter animation driven by a QTimer -- no image assets needed.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._phase = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(110)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        if not self._timer.isActive():
            self._timer.start()
        self.show()
        self.raise_()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._phase += 1.0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # Subtle dark backdrop so the bars stay visible over any album art color
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, 95))
        painter.drawEllipse(self.rect())

        bar_count = 3
        bar_width = w * 0.12
        gap = w * 0.10
        total_width = bar_width * bar_count + gap * (bar_count - 1)
        start_x = (w - total_width) / 2
        baseline = h * 0.72
        min_h = h * 0.14
        max_h = h * 0.46

        painter.setBrush(QColor(255, 255, 255, 235))
        for i in range(bar_count):
            t = (self._phase * 0.35) + i * 1.7
            ratio = (math.sin(t) + 1) / 2  # 0..1, phase-offset per bar
            bar_h = min_h + (max_h - min_h) * ratio
            x = start_x + i * (bar_width + gap)
            y = baseline - bar_h
            painter.drawRoundedRect(QRectF(x, y, bar_width, bar_h), bar_width / 2, bar_width / 2)


class MusicPlayerWidget(QWidget):
    """
    Main floating desktop player widget.
    Toggles between a circular collapsed Bubble and an Expanded frosted-glass Card.
    """
    _search_done = pyqtSignal(list)
    _thumb_loaded = pyqtSignal(bytes)

    BUBBLE_SIZE = 46
    EXPANDED_W = 360
    EXPANDED_H = 480

    def __init__(self, engine: AudioEngine):
        super().__init__()
        self.engine = engine
        self.is_expanded = False
        self.drag_position = QPoint()
        self.is_user_scrubbing = False
        self._has_artwork = False  # True once real album art is showing in the bubble

        self._search_done.connect(self._apply_search_results)
        self._thumb_loaded.connect(self._apply_thumbnail_bytes)

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        self.container = QFrame()
        self.container.setObjectName("MainContainer")
        self.container.setStyleSheet("#MainContainer { background: transparent; }")
        root_layout.addWidget(self.container)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 160))
        shadow.setOffset(0, 6)
        self.container.setGraphicsEffect(shadow)

        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(0, 0, 0, 0)
        self.container_layout.setSpacing(0)

        self._build_bubble()
        self._build_expanded_card()

        self.expanded_frame.hide()
        self.bubble_frame.show()
        self.adjustSize()

        self.engine.track_loaded.connect(self._on_track_loaded)
        self.engine.playback_state_changed.connect(self._on_playback_state_changed)
        self.engine.position_changed.connect(self._on_position_changed)
        self.engine.duration_changed.connect(self._on_duration_changed)
        self.engine.queue_updated.connect(self._on_queue_updated)
        self.engine.buffering_status.connect(self._on_buffering_status)
        self.engine.error_occurred.connect(self._on_error)
        self.engine.shuffle_changed.connect(self._on_shuffle_changed)
        self.engine.repeat_changed.connect(self._on_repeat_changed)

        self._apply_control_styles()

    # -------------------------------------------------------------
    # Bubble (collapsed) view
    # -------------------------------------------------------------
    def _build_bubble(self):
        self.bubble_frame = QFrame()
        self.bubble_frame.setObjectName("BubbleFrame")
        self.bubble_frame.setStyleSheet(
            "QFrame#BubbleFrame { background: transparent; border: none; }"
        )
        self.bubble_frame.setFixedSize(self.BUBBLE_SIZE, self.BUBBLE_SIZE)

        self.bubble_thumb = QPushButton(self.bubble_frame)
        self.bubble_thumb.setObjectName("BubbleThumb")
        self.bubble_thumb.setStyleSheet("""
            QPushButton#BubbleThumb {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }
        """)
        # BubbleThumb final M logo
        self.bubble_thumb.setStyleSheet("""
            QPushButton#BubbleThumb {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                color: white;
                font-size: 20px;
                font-weight: 800;
            }
        """)

        # BubbleThumb M logo
        self.bubble_thumb.setStyleSheet("""
            QPushButton#BubbleThumb {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
                color: white;
                font-size: 22px;
                font-weight: 800;
            }
        """)

        # BUBBLE_TRANSPARENT_STYLE
        self.bubble_frame.setStyleSheet(
            "QFrame { background: transparent; border: none; }"
        )
        self.bubble_thumb.setStyleSheet("""
            QPushButton#BubbleThumb {
                background: transparent;
                border: none;
                padding: 0px;
                margin: 0px;
            }
            QPushButton#BubbleThumb:hover {
                background: transparent;
                border: none;
            }
            QPushButton#BubbleThumb:pressed {
                background: transparent;
                border: none;
            }
        """)

        self.bubble_thumb.setGeometry(0, 0, self.BUBBLE_SIZE, self.BUBBLE_SIZE)
        self.bubble_thumb.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        _logo_size = max(1, int(self.BUBBLE_SIZE * 0.6))
        self.bubble_thumb.setIcon(QIcon(create_logo_pixmap(_logo_size)))
        self.bubble_thumb.setIconSize(QSize(_logo_size, _logo_size))
        self.bubble_thumb.setText("")
        self.bubble_thumb.setToolTip("Click to expand ARSH player")
        self.bubble_thumb.clicked.connect(self.toggle_expand)

        self.bubble_equalizer = EqualizerOverlay(self.bubble_frame)
        self.bubble_equalizer.setGeometry(0, 0, self.BUBBLE_SIZE, self.BUBBLE_SIZE)

        badge_size = 20
        self.bubble_badge = QLabel("A", self.bubble_frame)
        self.bubble_badge.hide()
        self.bubble_badge.setObjectName("BubbleLogoBadge")
        self.bubble_badge.setFixedSize(badge_size, badge_size)
        self.bubble_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.bubble_badge.move(self.BUBBLE_SIZE - badge_size, self.BUBBLE_SIZE - badge_size)
        self.bubble_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.bubble_badge.raise_()

        self.container_layout.addWidget(self.bubble_frame, alignment=Qt.AlignmentFlag.AlignCenter)

    # -------------------------------------------------------------
    # Expanded (frosted glass) card view
    # -------------------------------------------------------------
    def _build_expanded_card(self):
        self.expanded_frame = QFrame()
        self.expanded_frame.setObjectName("ExpandedFrame")
        expanded_layout = QVBoxLayout(self.expanded_frame)
        expanded_layout.setContentsMargins(16, 16, 16, 16)
        expanded_layout.setSpacing(12)

        top_bar = QHBoxLayout()
        lbl_title = QLabel("ARSH")
        lbl_title.setObjectName("TitleLabel")
        top_bar.addWidget(lbl_title)
        top_bar.addStretch()

        self.btn_collapse = QPushButton("▼")
        self.btn_collapse.setObjectName("ChevronButton")
        self.btn_collapse.setFixedSize(26, 26)
        self.btn_collapse.setToolTip("Collapse to Bubble")
        self.btn_collapse.clicked.connect(self.toggle_expand)
        top_bar.addWidget(self.btn_collapse)

        self.btn_close = QPushButton("✕")
        self.btn_close.setObjectName("ChevronButton")
        self.btn_close.setFixedSize(26, 26)
        self.btn_close.setToolTip("Close")
        self.btn_close.clicked.connect(self.close)
        top_bar.addWidget(self.btn_close)

        expanded_layout.addLayout(top_bar)

        search_box = QHBoxLayout()
        search_box.setSpacing(6)
        self.search_input = QLineEdit()
        self.search_input.setObjectName("SearchInput")
        self.search_input.setPlaceholderText("Search song or artist...")
        self.search_input.returnPressed.connect(self._on_search)
        search_box.addWidget(self.search_input, 1)

        self.btn_search = QPushButton("Go")
        self.btn_search.setObjectName("PrimaryButton")
        self.btn_search.setFixedSize(42, 34)
        self.btn_search.clicked.connect(self._on_search)
        search_box.addWidget(self.btn_search)

        expanded_layout.addLayout(search_box)

        hero_box = QHBoxLayout()
        hero_box.setSpacing(12)

        self.hero_thumb = QLabel()
        self.hero_thumb.setFixedSize(68, 68)
        self.hero_thumb.setObjectName("HeroThumb")
        self.hero_thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hero_thumb.setPixmap(create_logo_pixmap(40))
        hero_box.addWidget(self.hero_thumb)

        hero_text_box = QVBoxLayout()
        hero_text_box.setSpacing(2)

        self.hero_title = QLabel("No track selected")
        self.hero_title.setObjectName("HeroTitle")
        self.hero_title.setWordWrap(False)

        self.hero_artist = QLabel("Search songs to begin")
        self.hero_artist.setObjectName("HeroArtist")
        self.hero_artist.setWordWrap(False)

        self.lbl_status = QLabel("Idle")
        self.lbl_status.setObjectName("StatusLabel")

        hero_text_box.addWidget(self.hero_title)
        hero_text_box.addWidget(self.hero_artist)
        hero_text_box.addWidget(self.lbl_status)
        hero_box.addLayout(hero_text_box, 1)

        expanded_layout.addLayout(hero_box)

        seek_box = QVBoxLayout()
        seek_box.setSpacing(2)

        self.progress_slider = ClickableSlider(Qt.Orientation.Horizontal)
        self.progress_slider.setObjectName("ProgressSlider")
        self.progress_slider.setRange(0, 1000)
        self.progress_slider.sliderMoved.connect(self._on_slider_moved)
        self.progress_slider.sliderReleased.connect(self._on_slider_released)
        self.progress_slider.clicked.connect(self._on_slider_clicked)
        seek_box.addWidget(self.progress_slider)

        time_box = QHBoxLayout()
        self.lbl_time_current = QLabel("00:00")
        self.lbl_time_current.setObjectName("TimeLabel")
        self.lbl_time_total = QLabel("00:00")
        self.lbl_time_total.setObjectName("TimeLabel")
        time_box.addWidget(self.lbl_time_current)
        time_box.addStretch()
        time_box.addWidget(self.lbl_time_total)
        seek_box.addLayout(time_box)

        expanded_layout.addLayout(seek_box)

        # --- Controls row: shuffle / prev / play / next / repeat ---
        ctrl_box = QHBoxLayout()
        ctrl_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ctrl_box.setSpacing(14)

        self.btn_shuffle = QPushButton("🔀")
        self.btn_shuffle.setFixedSize(30, 30)
        self.btn_shuffle.setToolTip("Shuffle")
        self.btn_shuffle.setStyleSheet(TOGGLE_OFF_STYLE)
        self.btn_shuffle.clicked.connect(self.engine.toggle_shuffle)
        ctrl_box.addWidget(self.btn_shuffle)

        self.btn_hero_prev = QPushButton("⏮")
        self.btn_hero_prev.setObjectName("IconButton")
        self.btn_hero_prev.setFixedSize(36, 36)
        self.btn_hero_prev.clicked.connect(self.engine.play_previous)
        ctrl_box.addWidget(self.btn_hero_prev)

        self.btn_hero_play = QPushButton("▶")
        self.btn_hero_play.setObjectName("PlayButtonLarge")
        self.btn_hero_play.setFixedSize(48, 48)
        self.btn_hero_play.setStyleSheet(
            "QPushButton#PlayButtonLarge {"
            " background-color: rgba(255, 255, 255, 0.95);"
            " color: #1c1c1e;"
            " border: none;"
            " border-radius: 24px;"
            " font-size: 20px;"
            "}"
            "QPushButton#PlayButtonLarge:hover {"
            " background-color: #ffffff;"
            "}"
        )
        self.btn_hero_play.clicked.connect(self.engine.toggle_play_pause)
        ctrl_box.addWidget(self.btn_hero_play)

        self.btn_hero_next = QPushButton("⏭")
        self.btn_hero_next.setObjectName("IconButton")
        self.btn_hero_next.setFixedSize(36, 36)
        self.btn_hero_next.clicked.connect(self.engine.play_next)
        ctrl_box.addWidget(self.btn_hero_next)

        self.btn_repeat = QPushButton("🔁")
        self.btn_repeat.setFixedSize(30, 30)
        self.btn_repeat.setToolTip("Repeat: off")
        self.btn_repeat.setStyleSheet(TOGGLE_OFF_STYLE)
        self.btn_repeat.clicked.connect(self.engine.toggle_repeat)
        ctrl_box.addWidget(self.btn_repeat)

        expanded_layout.addLayout(ctrl_box)

        vol_box = QHBoxLayout()
        vol_box.setSpacing(8)
        lbl_vol_icon = QLabel("🔊")
        lbl_vol_icon.setObjectName("VolIconLabel")
        vol_box.addWidget(lbl_vol_icon)

        self.vol_slider = QSlider(Qt.Orientation.Horizontal)
        self.vol_slider.setObjectName("VolumeSlider")
        self.vol_slider.setRange(0, 100)
        self.vol_slider.setValue(85)
        self.vol_slider.valueChanged.connect(self.engine.set_volume)
        vol_box.addWidget(self.vol_slider, 1)

        self.lbl_vol_pct = QLabel("85%")
        self.lbl_vol_pct.setObjectName("VolPctLabel")
        self.vol_slider.valueChanged.connect(lambda v: self.lbl_vol_pct.setText(f"{v}%"))
        vol_box.addWidget(self.lbl_vol_pct)

        expanded_layout.addLayout(vol_box)

        self.lbl_queue_header = QLabel("Up Next (Auto Radio Queue)")
        self.lbl_queue_header.setObjectName("QueueHeader")
        expanded_layout.addWidget(self.lbl_queue_header)

        self.queue_scroll = QScrollArea()
        self.queue_scroll.setWidgetResizable(True)
        self.queue_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.queue_scroll.viewport().setStyleSheet("background: transparent;")
        self.queue_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.queue_scroll.setObjectName("QueueScroll")
        self.queue_scroll.setFixedHeight(130)

        self.queue_container = QWidget()
        self.queue_container.setStyleSheet("background: transparent;")
        self.queue_layout = QVBoxLayout(self.queue_container)
        self.queue_layout.setContentsMargins(0, 0, 4, 0)
        self.queue_layout.setSpacing(4)
        self.queue_layout.addStretch()

        self.queue_scroll.setWidget(self.queue_container)
        expanded_layout.addWidget(self.queue_scroll)

        self.container_layout.addWidget(self.expanded_frame)

    # -------------------------------------------------------------
    # Frosted-glass background painting (bubble = circle, card = rounded rect)
    # -------------------------------------------------------------
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(0, 0, -1, -1)

        if self.is_expanded:
            gradient = QLinearGradient(float(rect.left()), float(rect.top()), float(rect.left()), float(rect.bottom()))
            gradient.setColorAt(0.0, QColor(56, 189, 248, 150))   # sky-400, more translucent
            gradient.setColorAt(1.0, QColor(37, 99, 235, 135))    # techy blue-600, more translucent
            painter.setBrush(gradient)
            painter.setPen(QColor(255, 255, 255, 50))
            painter.drawRoundedRect(rect, 18, 18)
        elif not self._has_artwork:
            # No real album art loaded yet -- show the sky-blue circle so the
            # default (bars) logo stays visible against the desktop behind it.
            painter.setBrush(QColor(56, 189, 248, 140))
            painter.setPen(QColor(255, 255, 255, 70))
            painter.drawEllipse(rect)
        # else: real album art is loaded -- no backdrop, the art fills the circle on its own

        super().paintEvent(event)

    # -------------------------------------------------------------
    # Screen boundary clamping
    # -------------------------------------------------------------
    def ensure_topmost(self):
        try:
            if os.name == "nt":
                import ctypes
                hwnd = int(self.winId())
                user32 = ctypes.windll.user32
                user32.SetWindowPos.argtypes = [
                    ctypes.c_void_p, ctypes.c_void_p,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    ctypes.c_uint
                ]
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0010)
        except Exception:
            pass
        self.raise_()

    def clamp_to_screen(self, target_x: int = None, target_y: int = None):
        screen = self.screen().geometry()
        curr_x = self.x() if target_x is None else target_x
        curr_y = self.y() if target_y is None else target_y
        curr_w = self.width()
        curr_h = self.height()

        margin = 10
        max_y = screen.bottom() - curr_h + margin
        min_y = screen.top() - margin
        clamped_y = max(min_y, min(curr_y, max_y))

        max_x = screen.right() - curr_w + margin
        min_x = screen.left() - margin
        clamped_x = max(min_x, min(curr_x, max_x))

        self.move(clamped_x, clamped_y)
        self.ensure_topmost()

    # -------------------------------------------------------------
    # Expand / collapse
    # -------------------------------------------------------------
    def toggle_expand(self):
        current_bottom = self.y() + self.height()
        layout = self.layout()

        if self.is_expanded:
            self.expanded_frame.hide()
            self.bubble_frame.show()
            if layout is not None:
                layout.setContentsMargins(10, 10, 10, 10)
            self.adjustSize()
            new_y = current_bottom - self.height()
            self.is_expanded = False
        else:
            self.bubble_frame.hide()
            self.expanded_frame.show()
            if layout is not None:
                layout.setContentsMargins(10, 10, 10, 10)
            self.resize(self.EXPANDED_W, self.EXPANDED_H)
            new_y = current_bottom - self.EXPANDED_H
            self.is_expanded = True

        self.clamp_to_screen(self.x(), new_y)
        self.update()

    # -------------------------------------------------------------
    # Drag & window movement
    # -------------------------------------------------------------
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            self.ensure_topmost()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            raw_target = event.globalPosition().toPoint() - self.drag_position
            self.clamp_to_screen(raw_target.x(), raw_target.y())
            event.accept()

    def mouseReleaseEvent(self, event):
        self.ensure_topmost()
        super().mouseReleaseEvent(event)

    def enterEvent(self, event):
        self.ensure_topmost()
        super().enterEvent(event)

    # -------------------------------------------------------------
    # Search
    # -------------------------------------------------------------
    def _on_search(self):
        query = self.search_input.text().strip()
        if not query:
            return

        self.lbl_status.setText("Searching...")
        self.btn_search.setEnabled(False)

        import threading
        def _search_worker():
            results = self.engine.search_tracks(query, limit=10)
            self._search_done.emit(results)

        threading.Thread(target=_search_worker, daemon=True).start()

    def _apply_search_results(self, results: list):
        self.btn_search.setEnabled(True)
        if results:
            self.lbl_status.setText(f"Playing top match ({len(results)} found)")
            self.engine.play_track(
                results[0],
                populate_radio=True,
                fallback_tracks=results[1:],
            )
        else:
            self.lbl_status.setText("No tracks found.")

    # -------------------------------------------------------------
    # Engine event handlers
    # -------------------------------------------------------------
    def _on_track_loaded(self, track: TrackInfo):
        title = track.title[:30] + "..." if len(track.title) > 30 else track.title
        artist = track.artist[:25] + "..." if len(track.artist) > 25 else track.artist

        self.bubble_thumb.setToolTip(f"{track.title} — {track.artist}")
        self.hero_title.setText(title)
        self.hero_artist.setText(artist)
        self.lbl_status.setText("Loading...")

        if track.thumbnail_url:
            self._load_thumbnail(track.thumbnail_url)
        else:
            _logo_size = max(1, int(self.BUBBLE_SIZE * 0.6))
            self.bubble_thumb.setIcon(QIcon(create_logo_pixmap(_logo_size)))
            self.bubble_thumb.setIconSize(QSize(_logo_size, _logo_size))
            self.bubble_thumb.setText("")
            self.hero_thumb.setPixmap(create_logo_pixmap(40))
            self._has_artwork = False
            self.update()

    def _load_thumbnail(self, url: str):
        import threading

        def _fetch():
            try:
                parsed = urlsplit(url)
                host = (parsed.hostname or "").lower()
                allowed_hosts = {
                    "i.ytimg.com",
                    "yt3.ggpht.com",
                    "lh3.googleusercontent.com",
                }
                if (
                    parsed.scheme.lower() != "https"
                    or host not in allowed_hosts
                    or parsed.port not in (None, 443)
                ):
                    return

                max_bytes = 5 * 1024 * 1024
                with requests.get(
                    url,
                    timeout=(3, 4),
                    allow_redirects=False,
                    stream=True,
                ) as response:
                    if response.status_code != 200:
                        return
                    if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                        return
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > max_bytes:
                        return

                    chunks = []
                    total = 0
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > max_bytes:
                            return
                        chunks.append(chunk)
                    self._thumb_loaded.emit(b"".join(chunks))
            except Exception:
                pass

        threading.Thread(target=_fetch, daemon=True).start()

    def _apply_thumbnail_bytes(self, data: bytes):
        image = QImage.fromData(data)
        pixmap = QPixmap.fromImage(image)
        if not pixmap.isNull():
            bubble_px = get_rounded_pixmap(
                crop_to_fill(
                    pixmap,
                    self.BUBBLE_SIZE,
                    self.BUBBLE_SIZE,
                ),
                self.BUBBLE_SIZE // 2,
            )

            hero_px = get_rounded_pixmap(
                crop_to_fill(pixmap, 68, 68),
                12,
            )
            self.bubble_thumb.setIcon(QIcon(bubble_px))
            self.bubble_thumb.setIconSize(QSize(self.BUBBLE_SIZE, self.BUBBLE_SIZE))
            self.bubble_thumb.setText("")
            self.hero_thumb.setPixmap(hero_px)
            self._has_artwork = True
            self.update()

    def _on_playback_state_changed(self, is_playing: bool):
        self.btn_hero_play.setText("⏸" if is_playing else "▶")
        self.lbl_status.setText("Playing" if is_playing else "Paused")
        if is_playing:
            self.bubble_equalizer.start()
        else:
            self.bubble_equalizer.stop()

    def _on_buffering_status(self, is_buffering: bool):
        if is_buffering:
            self.lbl_status.setText("Buffering...")

    def _on_error(self, message: str):
        if self.engine.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.lbl_status.setText("Playing")
            return
        self.lbl_status.setText("Error — see log")

    def _on_shuffle_changed(self, enabled: bool):
        self.btn_shuffle.setStyleSheet(TOGGLE_ON_STYLE if enabled else TOGGLE_OFF_STYLE)

    def _on_repeat_changed(self, mode: str):
        if mode == "off":
            self.btn_repeat.setStyleSheet(TOGGLE_OFF_STYLE)
            self.btn_repeat.setText("🔁")
            self.btn_repeat.setToolTip("Repeat: off")
        elif mode == "all":
            self.btn_repeat.setStyleSheet(TOGGLE_ON_STYLE)
            self.btn_repeat.setText("🔁")
            self.btn_repeat.setToolTip("Repeat: all")
        else:
            self.btn_repeat.setStyleSheet(TOGGLE_ON_STYLE)
            self.btn_repeat.setText("🔂")
            self.btn_repeat.setToolTip("Repeat: one")

    def _on_position_changed(self, pos_ms: int):
        if not self.is_user_scrubbing:
            self.progress_slider.setValue(pos_ms)
            self.lbl_time_current.setText(format_ms(pos_ms))

    def _on_duration_changed(self, dur_ms: int):
        self.progress_slider.setRange(0, max(1, dur_ms))
        self.lbl_time_total.setText(format_ms(dur_ms))

    def _on_slider_moved(self, value: int):
        self.is_user_scrubbing = True
        self.lbl_time_current.setText(format_ms(value))

    def _on_slider_released(self):
        self.is_user_scrubbing = False
        self.engine.seek(self.progress_slider.value())

    def _on_slider_clicked(self, value: int):
        self.engine.seek(value)

    def _on_queue_updated(self, queue: list):
        while self.queue_layout.count() > 1:
            child = self.queue_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        curr_idx = self.engine.current_index
        for idx, track in enumerate(queue):
            is_cur = (idx == curr_idx)
            item_widget = QueueItemWidget(
                index=idx,
                track=track,
                is_current=is_cur,
                on_click_callback=lambda t, i=idx: self.engine.play_index(i)
            )
            self.queue_layout.insertWidget(idx, item_widget)

    # -------------------------------------------------------------
    # Static control styling (labels, inputs, sliders, queue, etc.)
    # The bubble/card BACKGROUND is drawn in paintEvent, not here.
    # -------------------------------------------------------------
    def _apply_control_styles(self):
        self.setStyleSheet("""
            #BubbleFrame { background: transparent; }
            #BubbleThumb {
                background: transparent;
                border: none;
                color: rgba(255, 255, 255, 0.9);
                font-size: 26px;
            }
            #BubbleLogoBadge {
                background: #2563eb;
                color: white;
                font-weight: 800;
                font-size: 10px;
                border-radius: 10px;
                border: 2px solid rgba(255, 255, 255, 0.4);
            }
            #ExpandedFrame { background: transparent; }
            #TitleLabel { font-weight: 700; color: rgba(255,255,255,0.95); font-size: 13px; }
            #HeroThumb { background: rgba(255,255,255,0.15); border-radius: 12px; }
            #HeroTitle { font-size: 14px; font-weight: 700; color: #ffffff; }
            #HeroArtist { font-size: 12px; color: rgba(255,255,255,0.75); }
            #StatusLabel { font-size: 11px; color: rgba(255,255,255,0.85); }
            #TimeLabel { color: rgba(255,255,255,0.7); font-size: 11px; }
            #VolIconLabel { color: rgba(255,255,255,0.75); font-size: 12px; }
            #VolPctLabel { color: rgba(255,255,255,0.7); font-size: 11px; min-width: 28px; }
            #QueueHeader { font-weight: 700; color: rgba(255,255,255,0.85); font-size: 12px; margin-top: 4px; }
            #SearchInput {
                background: rgba(255,255,255,0.18);
                border: 1px solid rgba(255,255,255,0.15);
                border-radius: 10px;
                color: #ffffff;
                padding: 6px 12px;
                font-size: 12px;
            }
            #SearchInput:focus { border: 1px solid rgba(255,255,255,0.4); background: rgba(255,255,255,0.25); }
            #PrimaryButton {
                background: rgba(255,255,255,0.9);
                color: #1c1c1e;
                font-weight: bold;
                border-radius: 10px;
                border: none;
            }
            #PrimaryButton:hover { background: #ffffff; }
            #IconButton {
                background: transparent;
                color: rgba(255,255,255,0.85);
                font-size: 15px;
                border-radius: 14px;
                border: none;
            }
            #IconButton:hover { background: rgba(255,255,255,0.15); color: white; }
            #ChevronButton {
                background: rgba(255,255,255,0.12);
                color: rgba(255,255,255,0.8);
                font-size: 11px;
                border-radius: 13px;
                border: none;
            }
            #ChevronButton:hover { background: rgba(255,255,255,0.22); color: white; }
            #ProgressSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.25); border-radius: 2px; }
            #ProgressSlider::sub-page:horizontal { background: #ffffff; border-radius: 2px; }
            #ProgressSlider::handle:horizontal {
                background: #ffffff; width: 10px; margin-top: -3px; margin-bottom: -3px; border-radius: 5px;
            }
            #VolumeSlider::groove:horizontal { height: 4px; background: rgba(255,255,255,0.25); border-radius: 2px; }
            #VolumeSlider::sub-page:horizontal { background: rgba(255,255,255,0.8); border-radius: 2px; }
            #VolumeSlider::handle:horizontal {
                background: #ffffff; width: 8px; margin-top: -2px; margin-bottom: -2px; border-radius: 4px;
            }
            #QueueScroll { background: transparent; border: none; }
            QScrollBar:vertical { border: none; background: transparent; width: 5px; }
            QScrollBar::handle:vertical { background: rgba(255,255,255,0.3); border-radius: 2px; }
        """)
