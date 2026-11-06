"""Always-on-top translucent subtitle bar (Qt; macOS + Windows)."""
from __future__ import annotations

import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from ..config import IS_MAC, Settings

HIDE_AFTER = 12


class Overlay(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.s = settings
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.zh = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.en = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.zh.setStyleSheet("color: white; background: transparent;")
        self.en.setStyleSheet("color: rgba(255,255,255,0.72); background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 12, 24, 12)
        lay.setSpacing(4)
        lay.addWidget(self.zh, 1)
        lay.addWidget(self.en)
        self.apply_style()
        scr = QApplication.primaryScreen().availableGeometry()
        w = max(520, int(scr.width() * 0.62))
        self.setFixedWidth(w)
        self.setMinimumHeight(90)
        if settings.overlay_x >= 0 and QApplication.screenAt(QPoint(settings.overlay_x + 20,
                                                                    settings.overlay_y + 20)):
            self.move(settings.overlay_x, settings.overlay_y)
        else:
            self.move(scr.x() + (scr.width() - w) // 2, scr.y() + scr.height() - 120 - 60)
        self._drag: QPoint | None = None
        self._last = time.time()
        t = QTimer(self, interval=1000)
        t.timeout.connect(self._tick)
        t.start()
        self.show_text("对方字幕已就绪（拖动可移动，双击切换英文原文）", "")

    def apply_style(self) -> None:
        f = QFont()
        f.setPointSize(self.s.overlay_font)
        f.setBold(True)
        self.zh.setFont(f)
        f2 = QFont()
        f2.setPointSize(max(11, int(self.s.overlay_font * 0.58)))
        self.en.setFont(f2)
        self.en.setVisible(self.s.overlay_show_original)
        self.update()
        if self.isVisible():
            self._fit()

    def paintEvent(self, _e) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(QColor(18, 20, 26, int(255 * self.s.overlay_opacity)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(self.rect(), 14, 14)

    def show_text(self, translated: str, original: str) -> None:
        self.zh.setText(translated)
        self.en.setText(original)
        self._last = time.time()
        self._fit()

    def _fit(self) -> None:
        """Grow/shrink with the text while keeping the bottom edge where the user put it."""
        # Before the first show, height() is QWidget's default 480, not our bar: anchoring to that
        # bottom edge pushed the bar ~400 px below the screen on Windows (macOS clamps windows back).
        bottom = self.y() + self.height() if self.isVisible() else None
        inner = self.width() - 48
        h = 24 + self.zh.heightForWidth(inner)
        if self.en.isVisible() and self.en.text():
            h += 4 + self.en.heightForWidth(inner)
        self.setFixedHeight(max(90, h))
        if bottom is not None:
            self.move(self.x(), bottom - self.height())
        self._clamp()

    def _clamp(self) -> None:
        """Keep the whole bar inside the screen it is on (saved positions, resizes, monitor changes)."""
        scr = (QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()).availableGeometry()
        x = min(max(self.x(), scr.left()), max(scr.left(), scr.right() - self.width() + 1))
        y = min(max(self.y(), scr.top()), max(scr.top(), scr.bottom() - self.height() + 1))
        if (x, y) != (self.x(), self.y()):
            self.move(x, y)

    def _tick(self) -> None:
        if time.time() - self._last > HIDE_AFTER and self.zh.text():
            self.zh.setText("")
            self.en.setText("")

    # drag to move, double-click to toggle the original line
    def mousePressEvent(self, e) -> None:
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag is not None:
            self.move(e.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, _e) -> None:
        self._drag = None
        self.s.overlay_x, self.s.overlay_y = self.x(), self.y()
        self.s.save()

    def mouseDoubleClickEvent(self, _e) -> None:
        self.s.overlay_show_original = not self.s.overlay_show_original
        self.s.save()
        self.apply_style()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if IS_MAC and QApplication.platformName() == "cocoa":
            _mac_float_over_fullscreen(self)


def _mac_float_over_fullscreen(w: QWidget) -> None:
    """Keep the bar visible over full-screen meeting windows and on every Space."""
    try:
        import ctypes

        import AppKit
        import objc

        view = objc.objc_object(c_void_p=ctypes.c_void_p(int(w.winId())))
        win = view.window()
        win.setCollectionBehavior_(AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                                   | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        win.setLevel_(AppKit.NSStatusWindowLevel)
    except Exception:  # noqa: BLE001 — pyobjc missing: plain always-on-top still works
        pass
