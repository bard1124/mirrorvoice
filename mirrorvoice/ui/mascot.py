"""The owl at the top of the control panel: asleep while stopped, puts its headphones on when
translation starts, takes them off and dozes off again when it stops.

Three layers drawn from packaging/assets/mascot (made from the app icon's artwork): the owl without
headphones, its closed eyes, and the headphones. Everything else (breathing, blinking, the
headphones dropping on / lifting off, the light, "z z z") is animated here.
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from .icons import assets_dir

FPS = 30
PUT_ON_S, TAKE_OFF_S = 0.6, 0.45
LED = (0.869, 0.403)  # the headphones' light, as a fraction of the layer size


def _ease_out_back(x: float) -> float:
    c = 1.4
    return 1 + (c + 1) * (x - 1) ** 3 + c * (x - 1) ** 2


class Mascot(QWidget):
    def __init__(self, size: int = 84, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        d = assets_dir() / "mascot"
        self.layers = {n: QPixmap(str(d / f"{n}.png")) for n in ("body", "lids", "headset")}
        self.state = "stopped"  # stopped | busy | running
        self.muted = False
        self._on = 0.0  # 0 = headphones off … 1 = on
        self._on_from, self._on_to, self._on_t0 = 0.0, 0.0, 0.0
        self._t0 = time.monotonic()
        self._blink_at = self._t0 + 2.0
        self._level = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(1000 // FPS)
        self._tip()

    # ---------------------------------------------------------------- state
    def set_state(self, state: str) -> None:
        """'stopped' | 'busy' (starting: awake, no headphones yet) | 'running'."""
        if state == self.state:
            return
        self.state = state
        self._move_headset(1.0 if state == "running" else 0.0)
        self._blink_at = time.monotonic() + 0.15  # wakes up with a blink
        self._tip()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self._tip()

    def set_level(self, rms: float) -> None:
        """Speech level (0 … ~0.3): the owl bobs along while someone talks."""
        self._level = max(self._level, min(1.0, rms * 6))

    def _move_headset(self, to: float) -> None:
        self._on_from, self._on_to, self._on_t0 = self._headset_pos(), to, time.monotonic()

    def _headset_pos(self) -> float:
        dur = PUT_ON_S if self._on_to > self._on_from else TAKE_OFF_S
        x = min(1.0, (time.monotonic() - self._on_t0) / dur) if dur else 1.0
        if self._on_to > self._on_from:
            return self._on_from + (self._on_to - self._on_from) * _ease_out_back(x)
        return self._on_from + (self._on_to - self._on_from) * (x * x)

    def _tip(self) -> None:
        self.setToolTip({"running": "运行中，正在帮你翻译" + ("（已静音）" if self.muted else ""),
                         "busy": "正在启动…"}.get(self.state, "已停止，点“开始”叫醒我"))

    def asleep(self) -> bool:
        return self.state == "stopped"

    # ---------------------------------------------------------------- drawing
    def showEvent(self, e) -> None:
        self._timer.start(1000 // FPS)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()  # nothing to animate while the window is hidden
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:
        now = time.monotonic()
        t = now - self._t0
        on = self._headset_pos()
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        side = min(self.width(), self.height())
        owl = side * 0.86  # room above for the headphones dropping in and the "z"s
        x0, y0 = (self.width() - owl) / 2, self.height() - owl
        # breathing (slow and deep asleep), plus a little bob with speech
        self._level *= 0.85
        if self.asleep():
            k = 0.028 * math.sin(t * 2 * math.pi / 4.2)
        else:
            k = 0.012 * math.sin(t * 2 * math.pi / 2.6) + 0.03 * self._level
        p.save()
        p.translate(x0 + owl / 2, y0 + owl)  # scale from the feet
        p.scale(1 - k * 0.4, 1 + k)
        p.translate(-owl / 2, -owl)
        r = QRectF(0, 0, owl, owl)
        p.drawPixmap(r, self.layers["body"], QRectF(self.layers["body"].rect()))
        blinking = now >= self._blink_at
        if blinking and now - self._blink_at > 0.13:
            self._blink_at = now + random.uniform(2.5, 5.5)
            blinking = False
        if self.asleep() or blinking:
            p.drawPixmap(r, self.layers["lids"], QRectF(self.layers["lids"].rect()))
        if on > 0.01:
            p.setOpacity(min(1.0, on * 1.6))
            dy = -(1 - on) * owl * 0.45
            p.drawPixmap(r.translated(0, dy), self.layers["headset"], QRectF(self.layers["headset"].rect()))
            if self.state == "running" and not self.muted and on > 0.9:  # the light glows
                glow = 0.55 + 0.45 * math.sin(t * 2 * math.pi / 1.6)
                c = QPointF(LED[0] * owl, LED[1] * owl + dy)
                g = QRadialGradient(c, owl * 0.07)
                g.setColorAt(0, QColor(255, 190, 80, int(200 * glow)))
                g.setColorAt(1, QColor(255, 150, 40, 0))
                p.setOpacity(1.0)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(c, owl * 0.07, owl * 0.07)
        p.restore()
        if self.asleep() and on < 0.05:
            self._zzz(p, t, side)
        p.end()

    def _zzz(self, p: QPainter, t: float, side: float) -> None:
        """Three z's rising from the top right, one after another."""
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(3):
            ph = ((t / 2.7) + i / 3) % 1.0
            f = QFont()
            f.setBold(True)
            f.setPixelSize(max(7, int(side * (0.11 + 0.07 * ph))))
            p.setFont(f)
            alpha = int(220 * math.sin(math.pi * ph))
            p.setPen(QColor(130, 112, 190, alpha))
            x = side * (0.74 + 0.14 * ph)
            y = side * (0.36 - 0.18 * ph)  # baseline: the biggest "z" still fits under the top edge
            p.drawText(QPointF(x, y), "z")
