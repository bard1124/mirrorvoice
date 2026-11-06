"""Thread-safe bridge: engine events (any thread) -> Qt signal (GUI thread)."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class Bridge(QObject):
    event = Signal(str, dict)
    command = Signal(str)  # typed CLI commands, emitted from the stdin thread

    def emit_event(self, kind: str, **data) -> None:
        self.event.emit(kind, data)
