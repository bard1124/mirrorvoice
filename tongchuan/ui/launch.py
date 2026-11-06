"""How the app opens: control panel, or a terminal window running the CLI front end.

Clicking the app icon while the CLI is running stops it (same toggle as the old desktop icon).
"""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
from pathlib import Path

from ..config import FROZEN, IS_MAC, IS_WIN, PID_FILE


def self_cmd(*args: str, console: bool = False) -> list[str]:
    if FROZEN:
        exe = Path(sys.executable)
        if IS_WIN and console:
            exe = exe.with_name("TongChuanCLI.exe")  # console build (the GUI exe has no console)
        return [str(exe), *args]
    return [sys.executable, "-m", "tongchuan", *args]


def cli_running() -> int | None:
    try:
        pid = int(PID_FILE.read_text())
    except (OSError, ValueError):
        return None
    try:
        if IS_WIN:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return pid if str(pid) in out else None
        os.kill(pid, 0)
        return pid
    except OSError:
        return None


def stop_cli(pid: int) -> None:
    if IS_WIN:
        subprocess.run(["taskkill", "/PID", str(pid)], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    else:
        os.kill(pid, signal.SIGTERM)
    notify("已关闭")


def open_cli_terminal() -> None:
    cmd = self_cmd("cli", console=True)
    if IS_MAC:
        line = "clear && exec " + " ".join(shlex.quote(c) for c in cmd)
        script = ('tell application "Terminal"\nactivate\n'
                  f'set w to do script {applescript_str(line)}\n'
                  'set custom title of w to "同传助手"\nend tell')
        subprocess.run(["osascript", "-e", script])
    elif IS_WIN:
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
    else:
        subprocess.Popen(["x-terminal-emulator", "-e", *cmd])


def relaunch_gui() -> None:
    kw = {"creationflags": subprocess.DETACHED_PROCESS} if IS_WIN else {"start_new_session": True}
    subprocess.Popen(self_cmd("gui"), **kw)


def applescript_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def notify(text: str) -> None:
    if IS_MAC:
        subprocess.run(["osascript", "-e", f'display notification {applescript_str(text)} with title "同传助手"'])
