"""Floating two-way conversation window: the same translucent dark style as the subtitle bar.

Shows my sentences and the other side's (original + translation) as a scrolling list, stays on
top of meeting windows, draggable by its title row, resizable from the bottom-right corner. The
control panel keeps its own transcript list; this is an optional pop-out of it.
"""
from __future__ import annotations

from collections import deque
from html import escape

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QSizeGrip, QTextEdit, QVBoxLayout, QWidget

from ..config import IS_MAC, Settings

MAX_ENTRIES = 60
COLORS = {"me": "#8ec5ff", "them": "#d2b8ff"}
TAGS = {"me": "我", "them": "对方"}


class ConversationWindow(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.s = settings
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # macOS hides tool windows whenever the app is not frontmost — i.e. during the meeting, and
        # right away when opened from the menu-bar icon (the app is in the background then)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
        self.entries: deque[tuple[str, str, str, bool]] = deque(maxlen=MAX_ENTRIES)
        self.on_closed = None  # set by the control panel to keep its toggles in sync

        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 10, 16, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        self.title = QLabel("对话")
        self.title.setStyleSheet("color: rgba(255,255,255,0.75); background: transparent; font-weight: 600;")
        close = QPushButton("✕", objectName="iconBtn")
        close.setFixedSize(22, 22)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.clicked.connect(self.hide)
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(close)
        lay.addLayout(head)
        self.view = QTextEdit(readOnly=True)
        self.view.setFrameShape(QTextEdit.Shape.NoFrame)
        self.view.setStyleSheet("QTextEdit{background: transparent; color: white; border: none;}"
                                "QScrollBar:vertical{background: transparent; width: 6px;}"
                                "QScrollBar::handle:vertical{background: rgba(255,255,255,0.3); border-radius: 3px;}"
                                "QScrollBar::add-line, QScrollBar::sub-line{height: 0;}")
        self.view.viewport().setAutoFillBackground(False)
        lay.addWidget(self.view, 1)
        foot = QHBoxLayout()
        foot.addStretch()
        grip = QSizeGrip(self)
        grip.setStyleSheet("background: transparent;")
        foot.addWidget(grip)
        lay.addLayout(foot)

        self.setMinimumSize(480, 160)
        w = 840 if settings.convo_w == 420 else settings.convo_w  # 420 was the old default
        self.resize(max(480, w), max(160, settings.convo_h))
        scr = QApplication.primaryScreen().availableGeometry()
        if settings.convo_x >= 0 and QApplication.screenAt(QPoint(settings.convo_x + 20, settings.convo_y + 20)):
            self.move(settings.convo_x, settings.convo_y)
        else:
            self.move(scr.x() + scr.width() - self.width() - 24, scr.y() + 80)
        self._drag: QPoint | None = None
        self.apply_style()
        self._clamp()

    def _clamp(self) -> None:
        """Keep the whole window inside the screen it is on (saved position from a narrower
        window, resize past the edge, monitor change)."""
        scr = (QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
        if self.width() > scr.width() or self.height() > scr.height():
            self.resize(min(self.width(), scr.width()), min(self.height(), scr.height()))
        x = min(max(self.x(), scr.left()), max(scr.left(), scr.right() - self.width() + 1))
        y = min(max(self.y(), scr.top()), max(scr.top(), scr.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    # ---------------------------------------------------------------- content
    def add(self, who: str, src: str, tgt: str, passthrough: bool = False) -> None:
        self.entries.append((who, src, tgt, passthrough))
        self._render()

    def clear(self) -> None:
        self.entries.clear()
        self._render()

    def apply_style(self) -> None:
        f = QFont()
        f.setPointSize(self.s.convo_font)
        self.view.setFont(f)
        self.title.setFont(QFont(f.family(), max(10, int(self.s.convo_font * 0.8))))
        self._render()
        self.update()

    def _render(self) -> None:
        if not self.entries:
            self.view.setHtml('<div style="color: rgba(255,255,255,0.45);">开始后，这里显示我和对方的原文与译文。'
                              '拖动标题移动，右下角拖动缩放。</div>')
            return
        small = max(9, int(self.s.convo_font * 0.8))
        parts = []
        for who, src, tgt, passthrough in self.entries:
            tag = TAGS.get(who, who) + ("·原声" if passthrough else "")
            color = COLORS.get(who, "#ffffff")
            line = (f'<div style="margin: 0 0 7px 0;">'
                    f'<span style="color: {color}; font-weight: 600;">{escape(tag)}</span>'
                    f'&nbsp;<span style="color: rgba(255,255,255,0.62); font-size: {small}pt;">{escape(src)}</span>'
                    f'<br><span style="color: white;">{escape(tgt)}</span></div>')
            parts.append(line)
        self.view.setHtml("".join(parts))
        self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    # ---------------------------------------------------------------- look & feel
    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(18, 20, 26, int(255 * self.s.overlay_opacity)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 14, 14)

    def mousePressEvent(self, e) -> None:
        if e.position().y() <= 36:  # title row drags the window
            self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        if self._drag is not None:
            self._drag = None
            self._clamp()
            self._remember()

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.isVisible():
            self._clamp()
            self._remember()

    def _remember(self) -> None:
        self.s.convo_x, self.s.convo_y = self.x(), self.y()
        self.s.convo_w, self.s.convo_h = self.width(), self.height()
        self.s.save()

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        if self.on_closed:
            self.on_closed()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if IS_MAC and QApplication.platformName() == "cocoa":
            from .overlay import _mac_float_over_fullscreen

            _mac_float_over_fullscreen(self)
