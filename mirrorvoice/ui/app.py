"""GUI entry: decides between control panel, CLI terminal window, or stopping a running CLI."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ..config import Settings


def _taskbar_identity() -> None:
    """Windows groups windows by AppUserModelID; without our own, a source run shows up as
    "python" with the Python icon in the taskbar."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("io.github.mirrorvoice")
        except Exception:  # noqa: BLE001
            pass


def _taskbar_window_identity(win) -> None:
    """Source runs on Windows: the taskbar would otherwise call the window "Python" (pythonw.exe)."""
    from ..config import FROZEN

    if sys.platform != "win32" or FROZEN:
        return
    from .icons import assets_dir
    from .winshell import set_window_identity

    exe = sys.executable.replace("python.exe", "pythonw.exe")
    set_window_identity(int(win.winId()), "镜声", f"{assets_dir() / 'icon.ico'},0", f'"{exe}" -m mirrorvoice')


def run_gui(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    mode = argv[1] if len(argv) > 1 else ""
    s = Settings.load()
    _taskbar_identity()
    from .icons import app_icon
    from . import theme

    from ..patches import confirm_boot

    if mode == "cli":  # we ARE the terminal front end
        from .cli import run_cli

        confirm_boot()

        app = QApplication(argv)
        theme.apply(app)
        app.setWindowIcon(app_icon())
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
        confirm_boot()
        return 0

    app = QApplication(argv)
    app.setApplicationName("镜声")
    theme.apply(app)
    app.setWindowIcon(app_icon())
    from .main_window import MainWindow
    from .wizard import Wizard

    win = MainWindow(s)
    win.show()
    from PySide6.QtCore import QTimer

    QTimer.singleShot(3000, confirm_boot)  # a code patch that got the window up and running is good
    _taskbar_window_identity(win)
    if not s.setup_done:
        Wizard(s, win).exec()
        win._load_devices()
    return app.exec()
