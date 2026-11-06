"""Terminal-style front end: coloured log lines + subtitle overlay + typed commands.

Commands (type then Enter):  m = mute/unmute   q = quit   ui = switch to the control panel
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from ..config import PID_FILE, Settings
from ..engine import Engine
from ..languages import label
from .bridge import Bridge
from .overlay import Overlay

C = {"dim": "\033[2m", "cyan": "\033[36m", "green": "\033[32m", "yellow": "\033[33m",
     "red": "\033[31m", "mag": "\033[35m", "bold": "\033[1m", "off": "\033[0m"}
if os.name == "nt":
    os.system("")  # enable ANSI colours in the Windows console


def out(tag: str, msg: str, color: str = "") -> None:
    print(f"{C['dim']}{time.strftime('%H:%M:%S')}{C['off']} {color}[{tag}]{C['off']} {msg}", flush=True)


def run_cli(app: QApplication) -> int:
    s = Settings.load()
    PID_FILE.write_text(str(os.getpid()))
    bridge = Bridge()
    engine = Engine(bridge.emit_event)
    overlay = Overlay(s) if s.subtitles else None
    state = {"switch_ui": False}

    print(f"{C['bold']}同传助手 · 命令行模式{C['off']}  我说 {label(s.my_lang)} → 对方听 {label(s.their_lang)}"
          f"   （输入 m 静音 / q 退出 / ui 切换图形界面）", flush=True)

    def on_event(kind: str, d: dict) -> None:
        if kind == "status":
            out("init", d["text"], C["dim"])
        elif kind == "ready":
            out("ready", f"mic={d['mic']} → 会议={d['call']}  音色={d['voice']}"
                + (f"  对方声音={d['them']}" if d["them"] else ""), C["green"])
            out("ready", f"会议记录：{d['transcript']}", C["dim"])
            out("ready", "开始说话吧", C["green"])
            if overlay:
                overlay.show()
        elif kind == "me":
            if d["passthrough"]:
                out("我·原声", d["src"], C["cyan"])
            else:
                out("我", d["src"], C["cyan"])
                out("→", f"{d['tgt']}   {C['dim']}({d['latency']:.1f}s){C['off']}", C["bold"])
        elif kind == "them":
            out("对方", d["src"], C["mag"])
            if d["tgt"] != d["src"]:
                out("→", f"{d['tgt']}   {C['dim']}({d['latency']:.1f}s){C['off']}", C["bold"])
            if overlay:
                overlay.show_text(d["tgt"], d["src"] if d["tgt"] != d["src"] else "")
        elif kind == "mute":
            out("mute", "已静音（对方听不到翻译）" if d["muted"] else "取消静音", C["yellow"])
        elif kind == "ptt":
            out("ptt", "说话中…" if d["down"] else "松开", C["yellow"])
        elif kind == "error":
            out("error", d["message"], C["red"])
            app.quit()
        elif kind == "stopped":
            out("bye", "已停止", C["dim"])

    bridge.event.connect(on_event)

    def on_command(cmd: str) -> None:  # runs on the Qt thread (queued signal)
        if cmd == "m":
            engine.set_muted(not engine.muted)
        elif cmd in ("q", "quit", "exit"):
            app.quit()
        elif cmd == "ui":
            state["switch_ui"] = True
            app.quit()
        elif cmd:
            out("help", "m 静音/取消静音   q 退出   ui 切换图形界面", C["dim"])

    bridge.command.connect(on_command)

    def stdin_loop() -> None:
        for line in sys.stdin:
            bridge.command.emit(line.strip().lower())

    threading.Thread(target=stdin_loop, daemon=True).start()
    # Ctrl+C / kill / closing the window -> clean shutdown (audio streams closed properly)
    for sig in (signal.SIGINT, signal.SIGTERM, getattr(signal, "SIGHUP", None)):
        if sig is not None:
            signal.signal(sig, lambda *_: QTimer.singleShot(0, app.quit))
    keep_alive = QTimer(interval=200)  # lets Python handle signals while Qt runs
    keep_alive.timeout.connect(lambda: None)
    keep_alive.start()

    engine.start(s)
    rc = app.exec()
    engine.stop()
    PID_FILE.unlink(missing_ok=True)
    if state["switch_ui"]:
        s = Settings.load()
        s.display = "ui"
        s.save()
        from .launch import relaunch_gui

        relaunch_gui()
    return rc
