"""The owl girl at the top of the control panel. She dozes while translation is stopped, wakes up
and cheers when it starts, then stands by with the headset on: types on her laptop while the
other side talks, waves while I talk, blinks now and then.

Six pose pictures from packaging/assets/mascot (packaging/art/make_girl.py lines them up); the
breathing, the little hop and cross-fade between poses, blinking and "z z z" are animated here.

UpdateMascot is her update routine, shown while an update downloads and installs.
"""
from __future__ import annotations

import math
import random
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from .icons import assets_dir

POSES = ("hello", "smile", "wave", "laptop", "sleepy", "cheer")
FPS = 30
FADE_S = 0.22      # cross-fade between poses
CHEER_S = 1.4      # the cheer when translation has started
VOICE_HOLD_S = 2.0  # keep the talking / typing pose this long after the voice stops
VOICE_LEVEL = 0.02  # speech level that counts as someone talking


class Mascot(QWidget):
    def __init__(self, width: int = 100, height: int = 120, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        d = assets_dir() / "mascot"
        self.poses = {n: QPixmap(str(d / f"{n}.png")) for n in POSES}
        self.state = "stopped"  # stopped | busy | running
        self.muted = False
        now = time.monotonic()
        self._t0 = now
        self._pose, self._prev, self._changed = "sleepy", "sleepy", now - 10
        self._cheer_until = 0.0
        self._voice = {"me": 0.0, "them": 0.0}  # when each side last spoke
        self._blink_at = now + 3.0
        self._level = 0.0
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(1000 // FPS)
        self._tip()

    # ---------------------------------------------------------------- state
    def set_state(self, state: str) -> None:
        """'stopped' | 'busy' (starting) | 'running'."""
        if state == self.state:
            return
        if state == "running":
            self._cheer_until = time.monotonic() + CHEER_S
        self.state = state
        self._tip()
        self._tick()

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self._tip()

    def set_level(self, rms: float, who: str = "me") -> None:
        """Speech level of my microphone or of the other side (0 … ~0.3)."""
        self._level = max(self._level, min(1.0, rms * 6))
        if rms >= VOICE_LEVEL and who in self._voice:
            self._voice[who] = time.monotonic()

    def asleep(self) -> bool:
        return self.state == "stopped"

    def pose(self) -> str:
        """The pose the state calls for right now."""
        now = time.monotonic()
        if self.state == "stopped":
            return "sleepy"
        if self.state == "busy":
            return "smile"  # waking up
        if now < self._cheer_until:
            return "cheer"
        if not self.muted:
            if now - self._voice["them"] < VOICE_HOLD_S:
                return "laptop"  # writing down the other side's words
            if now - self._voice["me"] < VOICE_HOLD_S:
                return "wave"
        if now >= self._blink_at:
            if now - self._blink_at < 0.16:
                return "smile"  # a blink
            self._blink_at = now + random.uniform(2.5, 6.0)
        return "hello"

    def _tick(self) -> None:
        p = self.pose()
        if p != self._pose:
            self._prev, self._pose, self._changed = self._pose, p, time.monotonic()
        self.update()

    def _tip(self) -> None:
        self.setToolTip({"running": "运行中，正在帮你翻译" + ("（已静音）" if self.muted else ""),
                         "busy": "正在启动…"}.get(self.state, "已停止，点“开始”叫醒我"))

    # ---------------------------------------------------------------- drawing
    def showEvent(self, e) -> None:
        self._timer.start(1000 // FPS)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()  # nothing to animate while the window is hidden
        super().hideEvent(e)

    def _target(self, pm: QPixmap) -> QRectF:
        s = min(self.width() / pm.width(), self.height() / pm.height())
        w, h = pm.width() * s, pm.height() * s
        return QRectF((self.width() - w) / 2, self.height() - h, w, h)

    def paintEvent(self, _e) -> None:
        now = time.monotonic()
        t = now - self._t0
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        self._level *= 0.85
        if self.asleep():
            k = 0.022 * math.sin(t * 2 * math.pi / 4.2)
        else:
            k = 0.010 * math.sin(t * 2 * math.pi / 2.6) + 0.025 * self._level
        since = now - self._changed
        hop = 0.0 if self._pose == "smile" else 4.0 * max(0.0, math.sin(min(1.0, since / 0.3) * math.pi))
        fade = min(1.0, since / FADE_S)
        p.translate(self.width() / 2, self.height() - hop)
        p.scale(1 - k * 0.4, 1 + k)  # breathing, from the feet
        p.translate(-self.width() / 2, -self.height())
        if fade < 1.0 and self._prev in self.poses:
            pm = self.poses[self._prev]
            p.setOpacity(1.0 - fade)
            p.drawPixmap(self._target(pm), pm, QRectF(pm.rect()))
        pm = self.poses[self._pose]
        p.setOpacity(fade)
        p.drawPixmap(self._target(pm), pm, QRectF(pm.rect()))
        p.resetTransform()
        p.setOpacity(1.0)
        if self.asleep():
            self._zzz(p, t)
        p.end()

    def _zzz(self, p: QPainter, t: float) -> None:
        """Three z's rising beside her head, one after another."""
        w, h = self.width(), self.height()
        for i in range(3):
            ph = ((t / 2.7) + i / 3) % 1.0
            f = QFont()
            f.setBold(True)
            f.setPixelSize(max(7, int(h * (0.08 + 0.06 * ph))))
            p.setFont(f)
            p.setPen(QColor(130, 112, 190, int(220 * math.sin(math.pi * ph))))
            p.drawText(QPointF(w * (0.74 + 0.14 * ph), h * (0.30 - 0.18 * ph)), "z")


# ---------------------------------------------------------------------------- the update routine
UPDATE_POSES = ("lift", "cross", "curl", "link", "rise", "land")
INTRO = (("lift", 0.8), ("cross", 0.8), ("curl", 0.8))  # then "link" for as long as the update takes
LINK_MIN_S = 1.0
UPDATE_FADE_S = 0.3
OUTRO = (("rise", 0.9), ("land", 1.1))
HOVER = 0.07         # how high she floats, of the widget height
FEET = (0.46, 0.975)  # under her boots, in the frames (x, y)
PORT = (0.702, 0.535)  # the cable port on her neck in the "link" frame (x, y)
GLOW = (110, 232, 240)


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


class UpdateMascot(QWidget):
    """While an update comes in she floats up, folds her arms, curls up and plugs a cable into the
    back of her neck; once it is installed (finish()) she rises, unfolds and lands with her eyes
    open. The frames come from packaging/assets/mascot/update; floating, the glow under her boots
    and at the cable port are drawn here."""

    def __init__(self, width: int = 132, height: int = 170, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        d = assets_dir() / "mascot" / "update"
        self.frames = {n: QPixmap(str(d / f"{n}.png")) for n in UPDATE_POSES}
        self.clock = time.monotonic
        self._t0 = self.clock()
        self._done: float | None = None  # when she starts rising
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        self._timer.start(1000 // FPS)

    def finish(self) -> float:
        """The update is in. Returns the seconds until she has landed (she first finishes curling
        up and plugging in, if the download was quicker than that)."""
        now = self.clock()
        if self._done is None:
            self._done = max(now, self._t0 + sum(d for _, d in INTRO) + LINK_MIN_S)
        return self._done + sum(d for _, d in OUTRO) - now

    def landed(self) -> bool:
        return self.finish_time() <= self.clock()

    def finish_time(self) -> float:
        return math.inf if self._done is None else self._done + sum(d for _, d in OUTRO)

    def _steps(self) -> list[tuple[str, float, float]]:
        t, steps = self._t0, []
        for n, d in INTRO:
            steps.append((n, t, t + d))
            t += d
        end = math.inf if self._done is None else self._done
        steps.append(("link", t, end))
        for n, d in OUTRO:
            steps.append((n, end, end + d))
            end += d
        return steps

    def at(self, now: float | None = None) -> tuple[str, str, float, float]:
        """(pose, the pose before it, seconds into the pose, how high she floats 0…1)."""
        now = self.clock() if now is None else now
        steps = self._steps()
        for i, (n, a, b) in enumerate(steps):
            if now < b or i == len(steps) - 1:
                since = max(0.0, now - a)
                prev = steps[i - 1][0] if i else n
                hover = 1.0
                if n == "lift":
                    hover = _ease(since / (b - a))
                elif n == "land":
                    hover = 1.0 - _ease(since / (b - a))
                return n, prev, since, hover
        raise AssertionError("unreachable")

    def pose(self) -> str:
        return self.at()[0]

    def showEvent(self, e) -> None:
        self._timer.start(1000 // FPS)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _target(self, pm: QPixmap) -> QRectF:
        room = self.height() * (1 - HOVER)
        s = min(self.width() / pm.width(), room / pm.height())
        w, h = pm.width() * s, pm.height() * s
        return QRectF((self.width() - w) / 2, room - h, w, h)

    def paintEvent(self, _e) -> None:
        now = self.clock()
        pose, prev, since, hover = self.at(now)
        t = now - self._t0
        pm = self.frames[pose]
        r = self._target(pm)
        lift = self.height() * HOVER * (1 - hover) + hover * 2.0 * math.sin(t * 2 * math.pi / 1.8)
        r.translate(0, lift)  # 0 = up in the air, HOVER of the height = on the ground
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.1)
        curled = pose in ("curl", "link")
        self._glow(p, QPointF(r.left() + FEET[0] * r.width(), r.top() + FEET[1] * r.height()),
                   r.width() * 0.26, hover * (0.5 if curled else 1.0) * (0.65 + 0.35 * pulse), squash=0.22)
        fade = min(1.0, since / UPDATE_FADE_S) if prev != pose else 1.0
        if fade < 1.0:  # both stay mostly opaque mid-way, so she does not look see-through
            p.setOpacity(1.0 - fade * fade)
            p.drawPixmap(r, self.frames[prev], QRectF(self.frames[prev].rect()))
        p.setOpacity(1.0 - (1.0 - fade) ** 2)
        p.drawPixmap(r, pm, QRectF(pm.rect()))
        p.setOpacity(1.0)
        if pose == "link":
            self._glow(p, QPointF(r.left() + PORT[0] * r.width(), r.top() + PORT[1] * r.height()),
                       r.width() * 0.09, min(1.0, since / 0.4) * (0.4 + 0.6 * pulse))
        p.end()

    @staticmethod
    def _glow(p: QPainter, at: QPointF, radius: float, strength: float, squash: float = 1.0) -> None:
        if strength <= 0.01 or radius <= 0:
            return
        g = QRadialGradient(QPointF(0, 0), radius)
        g.setColorAt(0.0, QColor(*GLOW, int(230 * strength)))
        g.setColorAt(0.45, QColor(*GLOW, int(120 * strength)))
        g.setColorAt(1.0, QColor(*GLOW, 0))
        p.save()
        p.translate(at)
        p.scale(1.0, squash)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0), radius, radius)
        p.restore()
