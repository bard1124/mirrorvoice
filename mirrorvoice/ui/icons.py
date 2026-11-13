"""App icon (window title bar, taskbar / Dock) and the tray / menu-bar icon with a status dot."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ..config import FROZEN, IS_MAC, resource_dir

STATE_COLORS = {"running": "#1a9e4b", "muted": "#d08700", "stopped": "#8a8a8a"}
_ICONS: dict[str, QIcon] = {}


def assets_dir() -> Path:
    """packaging/assets in a source checkout; when frozen, the images next to this package — the
    ones of a code patch when one is running (<patch>/assets), else the bundled ones."""
    if FROZEN:
        beside = Path(__file__).resolve().parents[2] / "assets"
        return beside if beside.is_dir() else resource_dir() / "assets"
    return Path(__file__).resolve().parents[2] / "packaging" / "assets"


def _load(name: str) -> QIcon:
    if name not in _ICONS:
        p = assets_dir() / name
        _ICONS[name] = QIcon(str(p)) if p.exists() else QIcon()
    return _ICONS[name]


def brand_icon() -> QIcon:
    """The full-bleed artwork: the logo inside the panel, the Windows taskbar and tray."""
    return _load("icon.png")


def app_icon() -> QIcon:
    """Window / application icon. On macOS it becomes the Dock tile while the app runs, so it uses
    the padded Apple-grid version (packaging/mac/make_icon.py); the full-bleed artwork looked one
    size larger than every other icon in the Dock."""
    if IS_MAC:
        ic = _load("icon-macos.png")
        if not ic.isNull():
            return ic
    return brand_icon()


def tray_icon(state: str) -> QIcon:
    """App icon with a coloured dot (running / muted / stopped) in the bottom-right corner."""
    pm = QPixmap(brand_icon().pixmap(64, 64))
    if pm.isNull():
        pm = QPixmap(64, 64)
        pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(QPen(QColor("white"), 3))
    p.setBrush(QColor(STATE_COLORS.get(state, STATE_COLORS["stopped"])))
    r = pm.width() * 0.36
    p.drawEllipse(QRectF(pm.width() - r - 1, pm.height() - r - 1, r, r))
    p.end()
    return QIcon(pm)


# ---------------------------------------------------------------- line icons for the navigation
# name -> strokes on a 24 x 24 grid: ("l", x1, y1, x2, y2) | ("r", x, y, w, h, radius) |
# ("a", x, y, w, h, start°, span°): part of the ellipse in that box, angles as Qt counts them
# (0 at three o'clock, anticlockwise)
NAV_ICONS = {
    None: (("r", 9, 3, 6, 11, 3), ("a", 5, 4, 14, 14, 180, 180), ("l", 12, 18, 12, 21)),                    # 同传: a microphone
    "_glossary": (("r", 5, 4, 14, 16, 2.5), ("l", 9, 9, 15, 9), ("l", 9, 13, 15, 13)),                      # a page of terms
    "_style": (("r", 3, 5, 18, 14, 3), ("l", 7, 14, 11, 14), ("l", 13, 14, 17, 14)),                        # captions
    "_voice": (("l", 4, 10, 4, 14), ("l", 8, 6, 8, 18), ("l", 12, 3, 12, 21), ("l", 16, 7, 16, 17), ("l", 20, 10, 20, 14)),
    "_online": (("a", 3, 10, 8, 8, 90, 180), ("a", 7, 5, 10, 10, 20, 160), ("a", 14, 10, 7, 8, 270, 180),   # a cloud
                ("l", 7, 18, 17.5, 18)),
    "_open_transcripts": (("l", 8, 6, 20, 6), ("l", 8, 12, 20, 12), ("l", 8, 18, 20, 18), ("l", 4, 6, 4.1, 6),
                          ("l", 4, 12, 4.1, 12), ("l", 4, 18, 4.1, 18)),
    "_wizard": (("l", 4, 7, 20, 7), ("l", 4, 17, 20, 17), ("r", 7, 4.5, 5, 5, 2.5), ("r", 13, 14.5, 5, 5, 2.5)),  # sliders
    "_check_updates": (("a", 4, 4, 16, 16, 50, 290), ("l", 17.2, 2.8, 17.2, 6.9), ("l", 17.2, 6.9, 13.1, 6.9)),  # round again
}


def nav_icon(name, off: str, on: str, size: int = 20) -> QIcon:
    """The navigation entry's icon, drawn in `off` (and in `on` while the entry is the chosen one)."""
    from PySide6.QtCore import QPointF

    def draw(color: str) -> QPixmap:
        pm = QPixmap(size * 2, size * 2)
        pm.setDevicePixelRatio(2)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.scale(size / 24, size / 24)
        p.setPen(QPen(QColor(color), 2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for kind, *a in NAV_ICONS.get(name, ()):
            if kind == "l":
                p.drawLine(QPointF(a[0], a[1]), QPointF(a[2], a[3]))
            elif kind == "r":
                p.drawRoundedRect(QRectF(*a[:4]), a[4], a[4])
            else:
                p.drawArc(QRectF(*a[:4]), int(a[4] * 16), int(a[5] * 16))
        p.end()
        return pm

    icon = QIcon()
    icon.addPixmap(draw(off), QIcon.Mode.Normal, QIcon.State.Off)
    icon.addPixmap(draw(on), QIcon.Mode.Normal, QIcon.State.On)
    return icon
