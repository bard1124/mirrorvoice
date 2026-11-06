"""App icon (window title bar, taskbar / Dock) and the tray / menu-bar icon with a status dot."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from ..config import FROZEN, resource_dir

STATE_COLORS = {"running": "#1a9e4b", "muted": "#d08700", "stopped": "#8a8a8a"}
_ICON: QIcon | None = None


def assets_dir() -> Path:
    """packaging/assets in a source checkout; bundled next to the executable when frozen."""
    if FROZEN:
        return resource_dir() / "assets"
    return Path(__file__).resolve().parents[2] / "packaging" / "assets"


def app_icon() -> QIcon:
    """The 同传 icon; empty QIcon (Qt default) if the asset is missing."""
    global _ICON
    if _ICON is None:
        p = assets_dir() / "icon.png"
        _ICON = QIcon(str(p)) if p.exists() else QIcon()
    return _ICON


def tray_icon(state: str) -> QIcon:
    """App icon with a coloured dot (running / muted / stopped) in the bottom-right corner."""
    pm = QPixmap(app_icon().pixmap(64, 64))
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
