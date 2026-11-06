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

from .. import __version__, updater
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
          f"   （输入 m 静音 / u 更新 / q 退出 / ui 切换图形界面）", flush=True)

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

    pending: dict = {}

    def on_update(rel, err: str) -> None:
        if rel is not None and rel.version != s.skip_version:
            pending["rel"] = rel
            out("update", f"发现新版本 v{rel.version}（当前 v{__version__}）— 输入 u 一键更新", C["yellow"])
        elif pending.get("manual"):
            out("update", f"无法检查更新：{err[:100]}" if err else f"已是最新版本 v{__version__}", C["dim"])

    def on_progress(done: int, total: int) -> None:
        if done >= 0:
            pct = int(100 * done / max(total, 1))
            if pct >= pending.get("pct", -10) + 10 or done == total:
                pending["pct"] = pct
                out("update", f"下载中 {pct}%  ({done >> 20}/{total >> 20} MB)", C["dim"])
        elif done == -2:
            out("update", f"更新失败：{pending.get('err')}", C["red"])
        else:
            out("update", "下载完成，正在安装并重启…", C["green"])
            engine.stop()
            try:
                updater.install(pending["path"])
            except Exception as e:  # noqa: BLE001
                out("update", f"更新失败：{e}", C["red"])
                return
            app.quit()

    bridge.update.connect(on_update)
    bridge.progress.connect(on_progress)

    def start_update() -> None:
        rel = pending.get("rel")
        if rel is None:
            pending["manual"] = True
            out("update", "正在检查更新…", C["dim"])
            updater.check_async(lambda r, e: bridge.update.emit(r, e))
            return
        if not updater.can_self_install():
            out("update", f"从源码运行，请手动更新：{rel.page}", C["yellow"])
            return

        def work():
            try:
                pending["path"] = updater.download(rel, lambda d, t: bridge.progress.emit(d, t))
                bridge.progress.emit(-1, 0)
            except Exception as e:  # noqa: BLE001
                pending["err"] = str(e)
                bridge.progress.emit(-2, 0)
        threading.Thread(target=work, daemon=True).start()

    def on_command(cmd: str) -> None:  # runs on the Qt thread (queued signal)
        if cmd == "u":
            start_update()
        elif cmd == "m":
            engine.set_muted(not engine.muted)
        elif cmd in ("q", "quit", "exit"):
            app.quit()
        elif cmd == "ui":
            state["switch_ui"] = True
            app.quit()
        elif cmd:
            out("help", "m 静音/取消静音   u 检查/一键更新   q 退出   ui 切换图形界面", C["dim"])

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

    from .downloads import DownloadManager, fmt_bytes, fmt_eta

    dm = DownloadManager.get()
    missing = dm.missing(s.quality)
    if missing:
        out("init", "缺少模型：" + "、".join(m.title for m in missing) + "，开始下载（可断点续传）…", C["yellow"])
        last = {"t": 0.0, "pct": -1}

        def dl_progress(done: int, total: int, speed: float) -> None:
            pct = 100 * done // max(total, 1)
            if pct != last["pct"] and (time.time() - last["t"] > 5 or pct == 100):
                last.update(t=time.time(), pct=pct)
                eta = fmt_eta((total - done) / speed) if speed > 0 else ""
                out("下载", f"{pct}%  {fmt_bytes(done)} / {fmt_bytes(total)}  {speed / (1 << 20):.1f} MB/s  {eta}",
                    C["dim"])

        def dl_done(err: str) -> None:
            if err:
                out("下载", f"中断：{err[:160]}（已下载部分会保留，重新打开即可继续）", C["red"])
                return
            out("下载", "模型全部就绪", C["green"])
            engine.start(s)

        dm.overall.connect(dl_progress)
        dm.finished.connect(dl_done)
        dm.start(s.quality)
    else:
        engine.start(s)
    if s.auto_check_update:
        updater.check_async(lambda r, e: bridge.update.emit(r, e))
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
