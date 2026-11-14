"""Always-on-top translucent subtitle bar (Qt; macOS + Windows)."""
from __future__ import annotations

import sys
import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QVBoxLayout, QWidget

from ..config import IS_MAC, Settings

HIDE_AFTER = 12


class Overlay(QWidget):
    def __init__(self, settings: Settings) -> None:
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                         | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.s = settings
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)  # see ConversationWindow
        self.zh = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.en = QLabel(alignment=Qt.AlignmentFlag.AlignCenter, wordWrap=True)
        self.zh.setStyleSheet("color: white; background: transparent;")
        self.en.setStyleSheet("color: rgba(255,255,255,0.72); background: transparent;")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 12, 24, 12)
        lay.setSpacing(4)
        lay.addWidget(self.zh, 1)
        lay.addWidget(self.en)
        self.close_btn = QPushButton("✕", self, objectName="iconBtn")
        self.close_btn.setFixedSize(22, 22)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setToolTip("隐藏字幕条（托盘菜单或“字幕样式”里可再打开）")
        self.close_btn.clicked.connect(self.hide)
        self.on_hidden = None  # control panel keeps its toggles in sync
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
        self._interim = ""  # a finished sentence: its provisional translation is replaced
        self.zh.setText(translated)
        self.en.setText(original)
        self._last = time.time()
        self._fit()

    def show_partial(self, original: str, interim: str = "") -> None:
        """Live subtitle while the sentence is still being spoken (online recognition): the
        recognised words so far, and a provisional translation when there is one. The final
        translation replaces both through show_text."""
        if interim:
            self._interim = interim
        top = interim or getattr(self, "_interim", "") or (original if not self.s.overlay_show_original else "…")
        self.zh.setText(top)
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

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self.close_btn.move(self.width() - self.close_btn.width() - 8, 6)

    def hideEvent(self, e) -> None:
        super().hideEvent(e)
        if self.on_hidden:
            self.on_hidden()

    def showEvent(self, e) -> None:
        super().showEvent(e)
        if IS_MAC and QApplication.platformName() == "cocoa":
            _mac_float_over_fullscreen(self)
        self.apply_sharing()

    def apply_sharing(self) -> None:
        if self.isVisible() and QApplication.platformName() != "offscreen":
            keep_out_of_sharing(self, self.s.hide_from_share)


WDA_NONE, WDA_EXCLUDEFROMCAPTURE = 0x0, 0x11  # SetWindowDisplayAffinity (Windows 10 2004+)


def keep_out_of_sharing(w: QWidget, hide: bool) -> bool:
    """Ask the system to leave this window out of screen sharing, recordings and screenshots
    (hide=False: let it be seen again). True when the system took the request.

    Windows 10 2004+: reliable. macOS: the window's sharing type — honoured by the older capture
    path, but from macOS 15.4 a meeting app that records through ScreenCaptureKit may show the
    window all the same (Apple: no public way to prevent that), so this is a request, not a promise."""
    try:
        if IS_MAC:
            import ctypes

            import AppKit
            import objc

            win = objc.objc_object(c_void_p=ctypes.c_void_p(int(w.winId()))).window()
            win.setSharingType_(AppKit.NSWindowSharingNone if hide else AppKit.NSWindowSharingReadOnly)
            return True
        if sys.platform == "win32":
            import ctypes

            return bool(ctypes.windll.user32.SetWindowDisplayAffinity(
                ctypes.c_void_p(int(w.winId())), WDA_EXCLUDEFROMCAPTURE if hide else WDA_NONE))
    except Exception:  # noqa: BLE001 — never a reason not to show the window
        pass
    return False


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
