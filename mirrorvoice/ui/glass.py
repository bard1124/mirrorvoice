"""Frosted glass behind a window (赛博 theme): what lies behind the window shows through, blurred, under
a white veil.

macOS: the window's ground is made clear and the window server blurs what is behind it (as
Terminal does). Windows 11 (22H2+): the window asks DWM for its Acrylic backdrop, which turns flat
while the window is in the background. Anywhere else, or when either fails, the window keeps its
opaque white: the veil (QSS) is only laid on once the window really is see-through.

A window has to be see-through from the moment it exists (frost(), before it is first shown); a
window made while another theme was on gets the glass at the next start.
"""
from __future__ import annotations

import sys

from PySide6.QtCore import QEvent, QObject, Qt, QTimer
from PySide6.QtWidgets import QWidget

from . import theme

# the veil: a white haze over the blur, as thin as the user's 透明度 wants (0 = nearly opaque, 100 =
# only the blur). The sidebar shows the most of what is behind; panels and cards sit a little lighter.
_VEIL = """
QMainWindow, QDialog {{ background: rgba(255,255,255,{GROUND:.3f}); }}
QWidget#content {{ background: rgba(255,255,255,{CONTENT:.3f}); }}
QScrollArea#page, QScrollArea#page > QWidget > QWidget#content {{ background: transparent; }}
QFrame#sidebar {{ background: transparent; border: none; border-right: 1px solid rgba(23,134,194,0.12); }}
QFrame#topbar {{ background: transparent; border: none; border-bottom: 1px solid rgba(23,134,194,0.12); }}
QListWidget#log {{ background: rgba(255,255,255,{PANEL:.3f}); }}
QFrame#card {{ background: rgba(255,255,255,{CARD:.3f}); border: 1px solid rgba(255,255,255,0.9); }}
QGroupBox::title {{ background: transparent; }}
"""
CLEAR = 60  # the default 透明度, in percent
BLUR = 20   # macOS: how soft what is behind the window gets (a radius in points)


def qss(clear: int = CLEAR) -> str:
    """The veil for a 透明度 of `clear` percent."""
    k = 1 - max(0, min(100, clear)) / 100
    return _VEIL.format(GROUND=0.85 * k, CONTENT=0.35 * k, PANEL=0.20 + 0.55 * k, CARD=0.38 + 0.52 * k)


_allowed = True
_clear = CLEAR


def allow(on: bool, clear: int = CLEAR) -> None:
    """The user's settings: glass off keeps every window opaque; clear is its 透明度 in percent."""
    global _allowed, _clear
    _allowed, _clear = bool(on), int(clear)


def supported() -> bool:
    if sys.platform == "darwin":
        try:
            import AppKit  # noqa: F401
            import objc  # noqa: F401
        except ImportError:
            return False
        return True
    if sys.platform == "win32":
        try:
            return sys.getwindowsversion().build >= 22621  # DWMWA_SYSTEMBACKDROP_TYPE
        except AttributeError:
            return False
    return False


def wanted() -> bool:
    return _allowed and theme.current().glass and supported()


class _OnShow(QObject):
    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:  # noqa: N802
        if ev.type() == QEvent.Type.Show and isinstance(obj, QWidget):
            QTimer.singleShot(0, obj, _Refresher(obj).run)  # a hop: the native window is up by then
        return False


class _Refresher:
    __slots__ = ("w",)

    def __init__(self, w: QWidget) -> None:
        self.w = w

    def run(self) -> None:
        refresh(self.w)


def frost(w: QWidget) -> bool:
    """Make a top-level window see-through before it is shown, if the theme wants glass. Returns
    whether it was; the blur itself goes in when it is shown (refresh)."""
    if not wanted():
        return False
    w.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    w.setProperty("frostable", True)
    w.installEventFilter(_OnShow(w))
    return True


def frostable(w: QWidget) -> bool:
    return bool(w.property("frostable"))


def refresh(w: QWidget) -> bool:
    """The glass on or off as the theme in use wants it (after a theme switch, too). True: it is on."""
    on = frostable(w) and wanted() and _attach(w)
    if not on:
        _detach(w)
    w.setStyleSheet(qss(_clear) if on else "")
    return on


def _native() -> str:
    """The windowing system the windows really live in ('' under offscreen: no native handle to touch)."""
    from PySide6.QtGui import QGuiApplication

    return {"cocoa": "darwin", "windows": "win32"}.get(QGuiApplication.platformName(), "")


def _attach(w: QWidget) -> bool:
    if _native() != sys.platform:
        return False
    try:
        if sys.platform == "darwin":
            return _mac_attach(w)
        if sys.platform == "win32":
            return _win_backdrop(w, 3)  # DWMSBT_TRANSIENTWINDOW: Acrylic
    except Exception:  # noqa: BLE001 — no glass is never worth a crash
        pass
    return False


def _detach(w: QWidget) -> None:
    if not frostable(w) or _native() != sys.platform:
        return
    try:
        if sys.platform == "darwin":
            _mac_detach(w)
        elif sys.platform == "win32":
            _win_backdrop(w, 1)  # DWMSBT_NONE
    except Exception:  # noqa: BLE001
        pass


def _mac_attach(w: QWidget) -> bool:
    """The window's own ground made clear, then blurred lightly the way Terminal does it: the
    wallpaper stays recognisable through the veil."""
    import ctypes

    import AppKit
    import objc

    nw = objc.objc_object(c_void_p=ctypes.c_void_p(int(w.winId()))).window()
    if nw is None:
        return False
    nw.setOpaque_(False)
    nw.setBackgroundColor_(AppKit.NSColor.clearColor())
    _mac_blur(nw.windowNumber(), BLUR)
    return True


def _mac_detach(w: QWidget) -> None:
    import ctypes

    import AppKit
    import objc

    nw = objc.objc_object(c_void_p=ctypes.c_void_p(int(w.winId()))).window()
    if nw is not None:
        nw.setBackgroundColor_(AppKit.NSColor.windowBackgroundColor())
        _mac_blur(nw.windowNumber(), 0)


def _mac_blur(window_number: int, radius: int) -> bool:
    """CGSSetWindowBackgroundBlurRadius (private, what Terminal and iTerm2 use): no blur if it is
    not there — the window is still see-through."""
    import ctypes

    try:
        cg = ctypes.cdll.LoadLibrary("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        conn = cg.CGSDefaultConnectionForThread
        conn.restype = ctypes.c_void_p
        blur = cg.CGSSetWindowBackgroundBlurRadius
        blur.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        blur.restype = ctypes.c_int
        return blur(conn(), int(window_number), int(radius)) == 0
    except (OSError, AttributeError):
        return False


def _win_backdrop(w: QWidget, kind: int) -> bool:
    import ctypes
    from ctypes import wintypes

    class MARGINS(ctypes.Structure):
        _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]

    dwm = ctypes.windll.dwmapi
    hwnd = wintypes.HWND(int(w.winId()))
    light = ctypes.c_int(0)
    dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(light), ctypes.sizeof(light))  # DWMWA_USE_IMMERSIVE_DARK_MODE
    edge = -1 if kind != 1 else 0
    dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(MARGINS(edge, edge, edge, edge)))
    value = ctypes.c_int(kind)
    return dwm.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value)) == 0  # SYSTEMBACKDROP_TYPE
