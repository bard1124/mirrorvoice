"""Entry point: GUI by default; `python -m tongchuan <cmd>` for headless use.

  gui                      control panel (default)
  cli                      terminal-style front end (coloured log + subtitle bar)
  run [--key value ...]    headless engine using saved settings (overrides by flag)
  devices                  list audio devices
  download                 download models for the current settings
"""
from __future__ import annotations

import argparse
import signal
import sys
import time


def _cmd_run(args) -> None:
    from .config import Settings
    from .engine import Engine

    s = Settings.load()
    for kv in args.set or []:
        k, v = kv.split("=", 1)
        cur = getattr(s, k)
        setattr(s, k, type(cur)(v) if not isinstance(cur, bool) else v.lower() in ("1", "true", "yes"))

    def emit(kind, **d):
        if kind != "level":
            print(time.strftime("%H:%M:%S"), kind, d, flush=True)

    eng = Engine(emit)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    eng.start(s)
    try:
        while eng.running:
            time.sleep(0.3)
    except KeyboardInterrupt:
        pass
    finally:
        eng.stop()


def _cmd_devices(_args) -> None:
    from .audio import list_devices

    print("输入:", list_devices("input"))
    print("输出:", list_devices("output"))


def _cmd_download(_args) -> None:
    from . import models
    from .config import Settings

    for m in models.required(Settings.load().quality):
        last = [0]

        def p(title, done, total):
            if done - last[0] > 50 << 20 or done >= total:
                print(f"{title}: {done >> 20}/{total >> 20} MB", flush=True)
                last[0] = done
        models.download(m, p)
    print("done")


def _utf8_console() -> None:
    """Windows consoles default to a legacy code page (cp1252/cp936): printing Chinese crashed."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:  # noqa: BLE001 — no console (windowed exe)
            pass
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass


def main() -> None:
    _utf8_console()
    if len(sys.argv) == 1 or sys.argv[1] in ("gui", "cli") or sys.argv[1].startswith("-psn"):
        from .ui.app import run_gui

        sys.exit(run_gui())
    p = argparse.ArgumentParser(prog="tongchuan")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--set", action="append", help="override a setting, e.g. --set mic=外置麦克风")
    r.set_defaults(fn=_cmd_run)
    sub.add_parser("devices").set_defaults(fn=_cmd_devices)
    sub.add_parser("download").set_defaults(fn=_cmd_download)
    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
