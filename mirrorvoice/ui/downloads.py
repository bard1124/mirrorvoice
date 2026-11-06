"""App-wide model download manager: one background download shared by the wizard, the control
panel banner and the voice recorder, so progress stays visible after the wizard is closed."""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Signal

from .. import models


def fmt_bytes(n: float) -> str:
    return f"{n / (1 << 30):.1f} GB" if n >= 1 << 30 else f"{n / (1 << 20):.0f} MB"


def fmt_eta(sec: float) -> str:
    if sec <= 0 or sec > 48 * 3600:
        return ""
    if sec < 90:
        return f"约 {int(sec)} 秒"
    if sec < 3600:
        return f"约 {int(sec / 60)} 分钟"
    return f"约 {sec / 3600:.1f} 小时"


class DownloadManager(QObject):
    # per model: (title, done, total); overall: (done, total, bytes_per_sec); finished: error ("" = ok)
    model_progress = Signal(str, object, object)  # object: byte counts exceed 32-bit int
    overall = Signal(object, object, float)
    finished = Signal(str)

    _instance: "DownloadManager | None" = None

    @classmethod
    def get(cls) -> "DownloadManager":
        if cls._instance is None:
            cls._instance = DownloadManager()
        return cls._instance

    def __init__(self) -> None:
        super().__init__()
        self.running = False
        self.error = ""
        self._cancel = threading.Event()

    @staticmethod
    def missing(quality: str) -> list[models.Model]:
        return [m for m in models.required(quality) if not m.ready()]

    def start(self, quality: str) -> None:
        if self.running:
            return
        todo = self.missing(quality)
        if not todo:
            self.finished.emit("")
            return
        self.running, self.error = True, ""
        self._cancel.clear()
        threading.Thread(target=self._work, args=(quality, todo), daemon=True, name="model-download").start()

    def cancel(self) -> None:
        self._cancel.set()

    def _work(self, quality: str, todo: list[models.Model]) -> None:
        sizes = {m.title: m.approx_mb << 20 for m in models.required(quality)}
        done = {m.title: (sizes[m.title] if m.ready() else 0) for m in models.required(quality)}
        t0, base = time.time(), sum(done.values())
        last_emit = [0.0]

        def on_progress(m: models.Model, d: int, t: int) -> None:
            done[m.title], sizes[m.title] = min(d, t), max(t, 1)
            self.model_progress.emit(m.title, d, t)
            now = time.time()
            if now - last_emit[0] > 0.3:
                last_emit[0] = now
                got = sum(done.values())
                speed = (got - base) / max(now - t0, 0.5)
                self.overall.emit(got, sum(sizes.values()), speed)

        try:
            for m in todo:
                models.download(m, lambda title, d, t, m=m: on_progress(m, d, t), self._cancel)
            self.overall.emit(sum(sizes.values()), sum(sizes.values()), 0.0)
            self.running = False
            self.finished.emit("")
        except Exception as e:  # noqa: BLE001 — shown to the user with a retry button
            self.running = False
            self.error = "已暂停" if isinstance(e, models.Cancelled) else str(e)
            self.finished.emit(self.error)
