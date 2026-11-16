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


PAUSED, CANCELLED = "已暂停", "已取消"


class DownloadManager(QObject):
    """One download at a time. 暂停 keeps what was fetched (继续 resumes it, byte for byte); 取消
    deletes what is not complete. Neither waits for the network: the connection that is being
    read may take up to a minute to notice (a stalled one), so the state changes at once and the
    old worker winds down behind it — the next one starts only when that one is gone."""
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
        self.paused = False  # stopped by the user: nothing starts it again but the user
        self.error = ""
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    @staticmethod
    def missing(quality: str) -> list[models.Model]:
        return [m for m in models.required(quality) if not m.ready()]

    @staticmethod
    def partial_bytes(quality: str) -> int:
        """On disk of models that are not complete — what 取消下载 would delete."""
        return sum(models.partial_bytes(m) for m in models.required(quality))

    def start(self, quality: str) -> None:
        if self.running:
            return
        todo = self.missing(quality)
        if not todo:
            self.finished.emit("")
            return
        self.running, self.paused, self.error = True, False, ""
        self._cancel = cancel = threading.Event()  # (its own: the worker before it stays cancelled)
        self._thread = threading.Thread(target=self._work, args=(quality, todo, cancel, self._thread), daemon=True,
                                        name="model-download")
        self._thread.start()

    def pause(self) -> None:
        if not self.running:
            return
        self._cancel.set()
        self.running, self.paused, self.error = False, True, PAUSED
        self.finished.emit(PAUSED)

    cancel = pause  # (the old name)

    def discard(self, quality: str) -> None:
        """取消下载: stop, and delete what was fetched of the models that are not complete."""
        self._cancel.set()
        self.running, self.paused, self.error = False, True, ""
        prev = self._thread

        def work() -> None:
            if prev is not None:
                prev.join()  # (it still writes until it notices)
            for m in models.required(quality):
                models.discard_partial(m)
            self.finished.emit(CANCELLED)
        self._thread = threading.Thread(target=work, daemon=True, name="model-discard")
        self._thread.start()

    def _work(self, quality: str, todo: list[models.Model], cancel: threading.Event,
              prev: threading.Thread | None) -> None:
        if prev is not None:
            prev.join()  # the files are one writer's at a time
        sizes = {m.title: m.approx_mb << 20 for m in models.required(quality)}
        done = {m.title: (sizes[m.title] if m.ready() else 0) for m in models.required(quality)}
        t0, base = time.time(), sum(done.values())
        last_emit = [0.0]

        def on_progress(m: models.Model, d: int, t: int, title: str = "") -> None:
            if cancel.is_set():
                return  # (paused: the bar stays where the user stopped it)
            done[m.title], sizes[m.title] = min(d, t), max(t, 1)
            self.model_progress.emit(title or m.title, d, t)  # title may carry （来源 …）/（解压中）
            now = time.time()
            if now - last_emit[0] > 0.3:
                last_emit[0] = now
                got = sum(done.values())
                speed = (got - base) / max(now - t0, 0.5)
                self.overall.emit(got, sum(sizes.values()), speed)

        try:
            for m in todo:
                if cancel.is_set():
                    raise models.Cancelled()
                models.download(m, lambda title, d, t, m=m: on_progress(m, d, t, title), cancel)
        except Exception as e:  # noqa: BLE001 — shown to the user with a retry button
            if cancel.is_set():
                return  # pause() / discard() said so already
            self.running = False
            self.error = PAUSED if isinstance(e, models.Cancelled) else str(e)
            self.finished.emit(self.error)
            return
        if cancel.is_set():
            return
        self.overall.emit(sum(sizes.values()), sum(sizes.values()), 0.0)
        self.running = False
        self.finished.emit("")
