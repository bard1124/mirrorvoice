"""GUI entry: decides between control panel, CLI terminal window, or stopping a running CLI."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..config import Settings


def run_gui(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    mode = argv[1] if len(argv) > 1 else ""
    s = Settings.load()

    if mode == "cli":  # we ARE the terminal front end
        from .cli import run_cli

        app = QApplication(argv)
        app.setQuitOnLastWindowClosed(False)
        return run_cli(app)

    if mode != "gui" and s.setup_done and s.display == "cli":
        # launched from the app icon in CLI mode: toggle the terminal window on/off
        from .launch import cli_running, open_cli_terminal, stop_cli

        pid = cli_running()
        if pid:
            stop_cli(pid)
        else:
            open_cli_terminal()
        return 0

    app = QApplication(argv)
    app.setApplicationName("同传助手")
    from .main_window import MainWindow
    from .wizard import Wizard

    win = MainWindow(s)
    win.show()
    if not s.setup_done:
        Wizard(s, win).exec()
        win._load_devices()
    return app.exec()
