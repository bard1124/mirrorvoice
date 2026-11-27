"""赛博's 虚拟形象: no figure, a little screen of cyan code rain at the top of the sidebar that shows
what the interpreter is doing, the way the owl girl does in the other themes.

    asleep  (stopped)       a few faint columns, falling slowly
    waking  (starting)      a band of light sweeps down, lighting the columns above it
    cheer   (just started)  a burst: every column, fast
    idle    (running)       steady rain; the head of each column brightest, its trail fading
    me / them (talking)     denser and faster with the voice; bright cyan for me, deep cyan for them
    muted                   the rain stands still, grey

Drawn with QPainter in a monospace face (nothing to ship). Still (no falling) when the system asks
for less motion.
"""
from __future__ import annotations

import math
import random
import sys
import time

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen

GLYPHS = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ0123456789"
ACCENTS = "翻译听说"
ACCENT_P = 0.035   # how often a glyph is one of the accents
COLS = 10           # a cell as wide as a CJK accent, so none overlaps its neighbour
MUTATE_PER_S = 2.5  # glyph changes per column per second
WAKE_S = 1.0        # one sweep of the waking band
# mode: share of columns falling, rows a second, ink, colour
MODES = {
    "asleep": (0.8, 1.8, 0.85, "#3E95C8"),
    "waking": (1.0, 6.0, 0.85, "#1786C2"),
    "cheer": (1.0, 14.0, 1.0, "#1786C2"),
    "idle": (0.75, 5.0, 1.0, "#1786C2"),
    "me": (0.9, 7.0, 1.0, "#22AEEF"),
    "them": (0.9, 7.0, 1.0, "#0A4F78"),
    "muted": (0.6, 0.0, 0.55, "#9AA6B2"),
}
# once translation is under way some columns fall in the window buttons' red, yellow and green
# (decoration only: they say nothing about the state)
TRAFFIC = ("#FF5F57", "#FEBC2E", "#28C840")
TRAFFIC_P = 0.25
TRAFFIC_MODES = {"cheer", "idle", "me", "them"}
# a faint still screen of glyphs under the rain, so a stopped (or muted) app still shows a screen,
# not an empty corner (2026-11-27: "没点开始时效果不明显")
FIELD = {"asleep": 0.16, "muted": 0.12}
FONT = ["Menlo", "PingFang SC"] if sys.platform == "darwin" else ["Consolas", "Microsoft YaHei UI"]


def _glyph(rng: random.Random) -> str:
    return rng.choice(ACCENTS) if rng.random() < ACCENT_P else rng.choice(GLYPHS)


class _Column:
    __slots__ = ("head", "trail", "pace", "falling", "glyphs", "tint")

    def __init__(self, rng: random.Random, rows: int) -> None:
        self.glyphs = [_glyph(rng) for _ in range(rows)]
        self.head, self.trail, self.pace, self.falling, self.tint = 0.0, 6, 1.0, False, ""

    def restart(self, rng: random.Random, rows: int, share: float, spread: float = 0.6) -> None:
        self.head = -rng.uniform(0, rows * spread)
        self.trail = rng.randint(4, max(5, rows - 2))
        self.pace = rng.uniform(0.7, 1.3)
        self.falling = rng.random() < share
        self.tint = rng.choice(TRAFFIC) if rng.random() < TRAFFIC_P else ""


class CodeRain:
    def __init__(self, rows: int = 11, still: bool = False, seed: int | None = None) -> None:
        self.rows, self.still = rows, still
        self.rng = random.Random(seed)
        self.cols = [_Column(self.rng, rows) for _ in range(COLS)]
        for c in self.cols:  # mid-fall from the first frame: a screen, not an empty box
            c.restart(self.rng, rows, MODES["asleep"][0])
            c.head = self.rng.uniform(0, rows + c.trail)
        self.mode, self._since, self._last = "asleep", time.monotonic(), None

    def set_mode(self, mode: str) -> None:
        if mode == self.mode or mode not in MODES:
            return
        self.mode, self._since = mode, time.monotonic()
        if mode == "cheer" and not self.still:  # the burst: every column comes down at once
            for c in self.cols:
                c.restart(self.rng, self.rows, 1.0, spread=0.3)

    def step(self, dt: float, level: float = 0.0) -> None:
        """Move the rain on by dt seconds; level (0 … 1) is how loudly someone is talking."""
        if self.still:
            return
        share, speed, _ink, _colour = MODES[self.mode]
        if self.mode in ("me", "them"):
            speed *= 1 + 1.5 * level
            share = min(1.0, share + 0.3 * level)
        for c in self.cols:
            if speed:
                c.head += speed * c.pace * dt
            if c.head - c.trail > self.rows:  # gone past the bottom: wait above for the next fall
                c.restart(self.rng, self.rows, share)
            if self.rng.random() < MUTATE_PER_S * dt:
                c.glyphs[self.rng.randrange(self.rows)] = _glyph(self.rng)

    def paint(self, p: QPainter, box: QRectF, level: float = 0.0) -> None:
        now = time.monotonic()
        if self._last is not None:
            self.step(min(0.1, now - self._last), level)
        self._last = now
        _share, _speed, ink, colour = MODES[self.mode]
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setClipRect(box)  # no frame and no ground: the rain falls straight on the window
        screen = box.adjusted(4, 3, -4, -3)
        cell_w, cell_h = screen.width() / COLS, screen.height() / self.rows
        font = QFont()
        font.setFamilies(FONT)
        font.setPixelSize(max(6, int(cell_h * 0.9)))
        wide = QFont(font)  # the CJK accents: a full square, made to fit the cell's width
        wide.setPixelSize(max(6, int(min(cell_w, cell_h) * 0.92)))
        band = ((now - self._since) / WAKE_S % 1.0) * self.rows if self.mode == "waking" else None
        base = QColor(colour)
        field = FIELD.get(self.mode, 0.0)
        if field:
            faint = QColor(base)
            for i, c in enumerate(self.cols):
                for row in range(self.rows):
                    faint.setAlphaF(field * min(1.0, (row + 0.6) / 1.6, (self.rows - row - 0.4) / 1.6))
                    p.setPen(faint)
                    p.setFont(wide if c.glyphs[row] in ACCENTS else font)
                    p.drawText(QRectF(screen.x() + i * cell_w, screen.y() + row * cell_h, cell_w + 4, cell_h),
                               int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), c.glyphs[row])
        lit = self.mode in TRAFFIC_MODES
        for i, c in enumerate(self.cols):
            if not c.falling:
                continue
            colour_of = QColor(c.tint) if lit and c.tint else base
            x = screen.x() + i * cell_w
            top = int(math.floor(c.head))
            for k in range(c.trail):
                row = top - k
                if not 0 <= row < self.rows:
                    continue
                a = ink * (1 - k / c.trail) ** 1.1
                a *= min(1.0, (row + 0.6) / 1.6, (self.rows - row - 0.4) / 1.6)  # fade in at the top, out at the bottom
                if band is not None and row > band:
                    a *= 0.18  # not reached by the sweep yet
                ch = QColor(colour_of.darker(135) if k == 0 else colour_of)  # the head: darkest on a light screen
                ch.setAlphaF(max(0.0, min(1.0, a if k else ink * min(1.0, (self.rows - row - 0.4) / 1.6))))
                p.setPen(ch)
                p.setFont(wide if c.glyphs[row] in ACCENTS else font)
                p.drawText(QRectF(x, screen.y() + row * cell_h, cell_w + 4, cell_h),
                           int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), c.glyphs[row])
        if band is not None:
            y = screen.y() + band * cell_h
            glow = QColor("#56BEF2")
            glow.setAlphaF(0.55)
            p.setPen(QPen(glow, 1.5))
            p.drawLine(int(box.x()), int(y), int(box.right()), int(y))
        p.restore()
