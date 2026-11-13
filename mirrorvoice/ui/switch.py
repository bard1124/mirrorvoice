"""A sliding on/off switch (the knob glides across; click or Space toggles)."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton, QWidget

from . import theme


class Switch(QAbstractButton):
    W, H, PAD = 36, 20, 2

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._pos = 1.0 if checked else 0.0  # 0 = knob left (off) … 1 = right (on)
        self._slide = QVariantAnimation(self, duration=140)
        self._slide.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self._slide.valueChanged.connect(self._moved)
        self.setChecked(checked)
        self.toggled.connect(self._glide)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.W, self.H)

    def position(self) -> float:
        return self._pos

    def _moved(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _glide(self, on: bool) -> None:
        self._slide.stop()
        if not self.isVisible():  # set from code before the window shows: no need to animate
            self._moved(1.0 if on else 0.0)
            return
        self._slide.setStartValue(self._pos)
        self._slide.setEndValue(1.0 if on else 0.0)
        self._slide.start()

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(0, (self.height() - self.H) / 2, self.W, self.H)
        t = theme.current()
        off, on = QColor(t.switch_off), QColor(t.accent)
        if self.underMouse():
            off = off.darker(112)
        k = self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(*(round(a + (b - a) * k) for a, b in zip(off.getRgb()[:3], on.getRgb()[:3]))))
        p.setOpacity(1.0 if self.isEnabled() else 0.5)
        p.drawRoundedRect(track, self.H / 2, self.H / 2)
        d = self.H - 2 * self.PAD
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(track.x() + self.PAD + k * (self.W - d - 2 * self.PAD), track.y() + self.PAD, d, d))
        p.end()
