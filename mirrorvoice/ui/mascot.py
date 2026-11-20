"""The owl-eared girl at the top of the control panel. She dozes while translation is stopped, wakes
up and cheers when it starts, then stands by: types on her laptop while the other side talks, waves
while I talk, blinks now and then.

Six pose pictures per theme from packaging/assets/mascot/<set>/ (packaging/art/make_mascots.py cuts
them out): black hair for 静场, silver for 调音台, lilac for 对话. The breathing, the little hop and
cross-fade between poses, blinking and "z z z" are animated here. 赛博 has no figure: the same
states drawn as cyan code rain (coderain.py).

UpdateMascot is her update routine, shown while an update downloads and installs.
"""
from __future__ import annotations

import math
import random
import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPixmap, QRadialGradient
from PySide6.QtWidgets import QWidget

from . import theme
from .coderain import CodeRain
from .icons import assets_dir

POSES = ("hello", "smile", "wave", "laptop", "sleepy", "cheer")
OUTFITS = {"quiet": "quiet", "cyber": "quiet", "studio": "studio", "chat": "chat"}  # theme -> her set
RAIN = {"cyber"}  # themes where code rain stands in for her
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
        self.outfit = ""
        self.rain: CodeRain | None = None
        self.dress()
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

    def dress(self) -> None:
        """Her look for the theme in use (after a theme switch, too)."""
        key = theme.current().key
        if key in RAIN and self.rain is None:
            self.rain = CodeRain(rows=max(6, self.height() // 11), still=reduce_motion())
        elif key not in RAIN:
            self.rain = None
        self.update()
        outfit = OUTFITS.get(key, "quiet")
        if outfit != self.outfit:
            d = assets_dir() / "mascot" / outfit
            self.poses = {n: QPixmap(str(d / f"{n}.png")) for n in POSES}
            self.outfit = outfit
            self.update()

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

    def rain_mode(self) -> str:
        """What the code rain shows for the state (the poses' meaning, without her)."""
        if self.state == "stopped":
            return "asleep"
        if self.state == "busy":
            return "waking"
        if self.muted:
            return "muted"
        return {"cheer": "cheer", "laptop": "them", "wave": "me"}.get(self.pose(), "idle")

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
        x = 0 if self.width() > self.height() else (self.width() - w) / 2  # wide (a top bar): her z z z go beside her
        return QRectF(x, self.height() - h, w, h)

    def paintEvent(self, _e) -> None:
        now = time.monotonic()
        t = now - self._t0
        p = QPainter(self)
        if self.rain is not None:
            self.rain.set_mode(self.rain_mode())
            self.rain.paint(p, QRectF(self.rect()).adjusted(1, 1, -1, -1), self._level)
            self._level *= 0.85
            p.end()
            return
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
            f.setPixelSize(max(8, int(h * (0.08 + 0.06 * ph))))
            p.setFont(f)
            z = QColor(theme.current().text_2)  # (light on the dark console, grey on the light looks)
            z.setAlpha(int(230 * math.sin(math.pi * ph)))
            p.setPen(z)
            p.drawText(QPointF(w * (0.74 + 0.14 * ph), h * (0.30 - 0.18 * ph)), "z")


# ---------------------------------------------------------------------------- the update routine
UPDATE_POSES = ("lift", "cross", "curl", "link", "rise", "land")
INTRO = (("lift", 1.1), ("cross", 1.0), ("curl", 1.0))  # then "link" for as long as the update takes
LINK_MIN_S = 1.2
OUTRO = (("rise", 1.1), ("land", 1.3))
TRANS_S = 0.6        # each pose blends in from the one before over this long
UPDATE_FPS = 60
HOVER = 0.07         # how high she floats, of the widget height
BOB_PX, BOB_S = 3.0, 2.4    # the up-and-down drift while floating
SWAY_DEG, SWAY_S = 1.6, 3.6  # and a slow sway
FEET = (0.46, 0.975)  # under her boots, in the frames (x, y)
PORT = (0.702, 0.535)  # the cable port on her neck in the "link" frame (x, y)
# where her face is in each frame (x, y): poses blend with the faces kept together, so the face
# glides from one spot to the next instead of showing twice
FACES = {"lift": (0.445, 0.324), "cross": (0.443, 0.325), "curl": (0.405, 0.360),
         "link": (0.381, 0.343), "rise": (0.435, 0.301), "land": (0.443, 0.293)}
CURLED = ("curl", "link")
GLOW = (110, 232, 240)


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return x * x * x * (x * (6 * x - 15) + 10)  # starts and ends with no jerk


class UpdateMascot(QWidget):
    """While an update comes in she floats up, folds her arms, curls up and plugs a cable into the
    back of her neck; once it is installed (finish()) she rises, unfolds and lands with her eyes
    open. The frames come from packaging/assets/mascot/update; the blending between them, the
    floating and swaying, and the glow under her boots and at the cable port are drawn here."""

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
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self.update)
        self._timer.start(1000 // UPDATE_FPS)

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
        self._timer.start(1000 // UPDATE_FPS)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def _target(self) -> QRectF:
        pm = self.frames["lift"]  # all frames share one canvas
        room = self.height() * (1 - HOVER)
        s = min(self.width() / pm.width(), room / pm.height())
        w, h = pm.width() * s, pm.height() * s
        return QRectF((self.width() - w) / 2, room - h, w, h)

    def paintEvent(self, _e) -> None:
        now = self.clock()
        pose, prev, since, hover = self.at(now)
        t = now - self._t0
        r = self._target()
        e = _ease(since / TRANS_S) if prev != pose else 1.0  # how far the blend from prev has come
        # the face glides from where it was in prev to where it is now; both frames move along
        (fx0, fy0), (fx1, fy1) = FACES[prev], FACES[pose]
        dx, dy = (fx1 - fx0) * r.width(), (fy1 - fy0) * r.height()
        r_prev, r_now = r.translated(e * dx, e * dy), r.translated(-(1 - e) * dx, -(1 - e) * dy)

        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        show = min(1.0, t / 0.3)  # fade in with the window
        down = self.height() * HOVER * (1 - hover) + hover * BOB_PX * math.sin(t * 2 * math.pi / BOB_S)
        p.translate(r.center().x(), r.center().y() + down)
        p.rotate(hover * SWAY_DEG * math.sin(t * 2 * math.pi / SWAY_S))
        p.translate(-r.center().x(), -r.center().y())

        pulse = 0.5 + 0.5 * math.sin(t * 2 * math.pi / 1.1)
        curled = (1 - e) * (prev in CURLED) + e * (pose in CURLED)
        self._glow(p, QPointF(r.left() + FEET[0] * r.width(), r.top() + FEET[1] * r.height()),
                   r.width() * 0.26, show * hover * (1 - 0.5 * curled) * (0.65 + 0.35 * pulse), squash=0.22)
        if e < 1.0:  # a soft flare behind her while she changes pose
            self._glow(p, QPointF(r.center().x(), r.top() + 0.45 * r.height()), r.width() * 0.48,
                       show * 0.38 * math.sin(math.pi * e), squash=1.15)
            p.setOpacity(show * (1.0 - e * e))
            p.drawPixmap(r_prev, self.frames[prev], QRectF(self.frames[prev].rect()))
        p.setOpacity(show * (1.0 - (1.0 - e) ** 2))
        p.drawPixmap(r_now, self.frames[pose], QRectF(self.frames[pose].rect()))
        p.setOpacity(1.0)
        if pose == "link":
            self._glow(p, QPointF(r_now.left() + PORT[0] * r.width(), r_now.top() + PORT[1] * r.height()),
                       r.width() * 0.09, e * (0.4 + 0.6 * pulse))
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


# ---------------------------------------------------------------------------- the robot (no 虚拟形象)
ROBOT_STEP_MS = 100   # both runs are drawn at 10 frames a second
ROBOT_HOLD_S = 0.6    # the last frame of the wake stays this long before the app restarts
# the part of the drawing (270 x 640) shown, x / y / width: the head, the cables over it, both
# shoulders (the left one ends at y 490, the right one at the drawing's edge). The right edge and
# the bottom fade into the window.
ROBOT_VIEW = (0, 100, 270)
ROBOT_FADE_RIGHT, ROBOT_FADE_BOTTOM = 0.80, 0.86  # where the fades begin, of the width / height
ROBOT_PORT = (142, 235)  # where the cables plug into the back of its neck, in the drawing
ROBOT_LIT_END = 340       # how far down (in the drawing) its cables run: the progress lights them to here


def reduce_motion() -> bool:
    """The system asks for less movement on screen (accessibility)."""
    try:
        if sys.platform == "darwin":
            import AppKit

            return bool(AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceMotion())
        if sys.platform == "win32":
            import ctypes

            on = ctypes.c_int(1)
            ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
            return not on.value
    except Exception:  # noqa: BLE001 — unknown: move
        pass
    return False


class UpdateRobot(QWidget):
    """The update window's picture when 虚拟形象 is off: a robot hanging from cables, a light running
    down them to its neck while the update comes in (loop, 2.4 s), and on finish() every cable lit
    and its eye on (wake, 1.2 s, the last frame held). Head to chest only, the cables running in from
    the top edge, the bottom fading into the window.

    The frames are packaging/assets/mascot/robot: one base picture and, per frame, only what differs
    from it (offsets.txt) — 480 KB instead of 6 MB of whole frames."""

    def __init__(self, width: int = 176, height: int = 288, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(width, height)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        d = assets_dir() / "mascot" / "robot"
        self.base = QPixmap(str(d / "base.png"))
        self.layers: dict[str, list[tuple[QPixmap, int, int]]] = {"loop": [], "wake": []}
        for row in (d / "offsets.txt").read_text(encoding="utf-8").splitlines()[1:]:
            name, x, y, w, _h = row.split()
            run = name.split("_")[0]
            pm = QPixmap(str(d / f"{name}.png")) if int(w) else QPixmap()
            self.layers[run].append((pm, int(x), int(y)))
        self.still = reduce_motion()
        self.progress: float | None = None  # set: its own cables light up from the top this far (0…1)
        self.clock = time.monotonic
        self._t0 = self.clock()
        self._woke: float | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        if not self.still:
            self._timer.start(ROBOT_STEP_MS)

    def set_progress(self, done: float) -> None:
        """From now on its own cables are the progress bar (0…1): see _paint_progress."""
        self.progress = min(1.0, max(0.0, done))
        if not self.still and not self._timer.isActive() and self._woke is None and self.isVisible():
            self._timer.start(ROBOT_STEP_MS // 2)
        self.update()

    @staticmethod
    def _unlit(pm: QPixmap) -> QPixmap:
        """The picture with its cables dark: their blue turned to the grey of the other cables."""
        import numpy as np
        from PySide6.QtGui import QImage

        img = pm.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        a = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine() // 4, 4)[:, :img.width()].astype(np.float32)
        rgb = a[..., :3]
        blue = np.clip((rgb[..., 2] - rgb[..., 0] - 20) / 80, 0, 1)[..., None]
        grey = (rgb @ np.array([0.30, 0.59, 0.11], np.float32))[..., None] * 0.92
        a[..., :3] = rgb * (1 - blue) + grey * blue
        out = QImage(a.astype(np.uint8).tobytes(), img.width(), img.height(), img.width() * 4, QImage.Format.Format_RGBA8888)
        return QPixmap.fromImage(out.copy())

    def port(self) -> QPointF:
        """The neck port in this widget's coordinates (the progress pipe plugs in there)."""
        vx, vy, vw = ROBOT_VIEW
        s = self.width() / vw
        return QPointF((ROBOT_PORT[0] - vx) * s, (ROBOT_PORT[1] - vy) * s)

    def finish(self) -> float:
        """The update is in: the cables light up and the eye comes on. Seconds until that is done."""
        if self._woke is None:
            self._woke = self.clock()
            if not self.still:
                self._timer.start(ROBOT_STEP_MS)
        wake = len(self.layers["wake"]) * ROBOT_STEP_MS / 1000
        return max(0.0, self._woke + wake + ROBOT_HOLD_S - self.clock())

    def frame(self) -> tuple[str, int]:
        """(run, index) to draw now."""
        now = self.clock()
        if self._woke is not None:
            n = len(self.layers["wake"])
            i = n - 1 if self.still else min(n - 1, int((now - self._woke) * 1000 // ROBOT_STEP_MS))
            if i == n - 1 and self.progress is None:
                self._timer.stop()  # (held)
            elif i == n - 1 and now - self._woke > n * ROBOT_STEP_MS / 1000 + 0.2:
                self._timer.stop()  # (progress: the band stops once the eye is on)
            return "wake", i
        n = len(self.layers["loop"])
        return "loop", (n - 1 if self.still else int((now - self._t0) * 1000 // ROBOT_STEP_MS) % n)

    def showEvent(self, e) -> None:
        if not self.still and (self._woke is None or self.frame()[1] < len(self.layers["wake"]) - 1):
            self._timer.start(ROBOT_STEP_MS)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e) -> None:
        if self.base.isNull():
            return
        run, i = self.frame()
        dpr = self.devicePixelRatioF()
        buf = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        buf.setDevicePixelRatio(dpr)
        buf.fill(Qt.GlobalColor.transparent)
        p = QPainter(buf)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        vx, vy, vw = ROBOT_VIEW
        s = self.width() / vw
        p.scale(s, s)
        p.translate(-vx, -vy)
        if self.progress is None:  # the loop of the drawing as it was made
            p.drawPixmap(0, 0, self.base)
            pm, x, y = self.layers[run][i] if i < len(self.layers[run]) else (QPixmap(), 0, 0)
            if not pm.isNull():
                p.drawPixmap(x, y, pm)
        else:
            self._paint_progress(p, vy)
        p.resetTransform()
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        for x0, y0, x1, y1 in ((0, self.height() * ROBOT_FADE_BOTTOM, 0, self.height()),
                               (self.width() * ROBOT_FADE_RIGHT, 0, self.width(), 0)):
            fade = QLinearGradient(x0, y0, x1, y1)
            fade.setColorAt(0, QColor(0, 0, 0, 255))
            fade.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(QRectF(0, 0, self.width(), self.height()), fade)
        p.end()
        out = QPainter(self)
        out.drawPixmap(0, 0, buf)

    # ---- its own cables as the progress
    ROBOT_EYE, ROBOT_EYE_R = (78, 278), 14  # (in the drawing) its eye: lit only once the update is in
    BAND_S, BAND_H = 1.3, 18                # a brighter band running down the lit part, every this long

    def _lit_part(self, top: float, bottom: float, eye: float = 0.0) -> tuple[QPixmap, int, int]:
        """The drawing's cables, all lit, between top and bottom (drawing rows), soft-edged; the eye
        at `eye` (0 = dark … 1 = lit)."""
        lit, lx, ly = self.layers["wake"][-1]
        part = QPixmap(lit.size())
        part.fill(Qt.GlobalColor.transparent)
        q = QPainter(part)
        q.drawPixmap(0, 0, lit)
        q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        band = QLinearGradient(0, top - ly - 6, 0, bottom - ly + 4)
        a, b = 6 / max(1.0, bottom - top + 10), 1 - 4 / max(1.0, bottom - top + 10)
        band.setColorAt(0, QColor(0, 0, 0, 0))
        band.setColorAt(max(0.0, min(a, 0.49)), QColor(0, 0, 0, 255))
        band.setColorAt(min(1.0, max(b, 0.51)), QColor(0, 0, 0, 255))
        band.setColorAt(1, QColor(0, 0, 0, 0))
        q.fillRect(part.rect(), band)
        if eye < 1:  # (the eye's pixels taken out, then put back as far as it is lit)
            ex, ey = self.ROBOT_EYE[0] - lx, self.ROBOT_EYE[1] - ly
            dark = QRadialGradient(QPointF(ex, ey), self.ROBOT_EYE_R)
            dark.setColorAt(0, QColor(0, 0, 0, int(255 * (1 - eye))))
            dark.setColorAt(0.7, QColor(0, 0, 0, int(255 * (1 - eye))))
            dark.setColorAt(1, QColor(0, 0, 0, 0))
            q.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
            q.fillRect(QRectF(ex - self.ROBOT_EYE_R, ey - self.ROBOT_EYE_R, 2 * self.ROBOT_EYE_R, 2 * self.ROBOT_EYE_R), dark)
        q.end()
        return part, lx, ly

    def _paint_progress(self, p: QPainter, vy: int) -> None:
        """Its cables grey, lit from the top down as far as the update has come; a brighter band
        running down the lit part; when it is in (finish), the eye comes on."""
        if not hasattr(self, "_grey"):
            self._grey = self._unlit(self.base)
        p.drawPixmap(0, 0, self._grey)
        woke = self._woke is not None
        cut = vy + (1.0 if woke else self.progress) * (ROBOT_LIT_END - vy)
        eye = 0.0
        if woke:
            wake = len(self.layers["wake"]) * ROBOT_STEP_MS / 1000
            eye = 1.0 if self.still else min(1.0, (self.clock() - self._woke) / wake)
        part, lx, ly = self._lit_part(vy - 20, cut, eye)
        p.drawPixmap(lx, ly, part)
        if not self.still and cut > vy + 10:
            f = ((self.clock() - self._t0) / self.BAND_S) % 1.0
            y = vy + f * (cut - vy)
            glow, gx, gy = self._lit_part(y - self.BAND_H / 2, y + self.BAND_H / 2)
            p.setOpacity(0.75)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
            p.drawPixmap(gx, gy, glow)
            p.setOpacity(1.0)
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
