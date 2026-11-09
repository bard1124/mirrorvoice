"""App icon (window title bar, taskbar / Dock) and the tray / menu-bar icon with a status dot."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ..config import FROZEN, IS_MAC, resource_dir

STATE_COLORS = {"running": "#1a9e4b", "muted": "#d08700", "stopped": "#8a8a8a"}
_ICONS: dict[str, QIcon] = {}


def assets_dir() -> Path:
    """packaging/assets in a source checkout; bundled next to the executable when frozen."""
    if FROZEN:
        return resource_dir() / "assets"
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
