"""Thread-safe bridge: engine events (any thread) -> Qt signal (GUI thread)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    event = Signal(str, dict)
    command = Signal(str)  # typed CLI commands, emitted from the stdin thread
    update = Signal(object, str)  # (Release | None, error) from the update checker
    progress = Signal(object, object)  # download progress (bytes; >2 GB overflowed int)

    def emit_event(self, kind: str, **data) -> None:
        self.event.emit(kind, data)
