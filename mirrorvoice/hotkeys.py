"""Global push-to-talk key + mute toggle hotkey (pynput).

macOS needs Accessibility / Input Monitoring permission for global keys; without it the
listeners simply never fire and the app keeps working in continuous mode.
"""
from __future__ import annotations

import contextlib
import sys
import threading
from typing import Callable

_layout_ready = False


def prime_layout() -> None:
    """macOS: read the keyboard layout on the main thread and hand it to pynput.

    pynput reads it (TISCopyCurrentKeyboardInputSource) inside every listener thread. When the
    current input source is an input method such as Pinyin, macOS asserts that this runs on the
    main thread and kills the app (SIGTRAP) — "the app quits when I press Start". Call this from
    the main thread before starting the listeners; again on each start, the layout may change.
    """
    global _layout_ready
    if sys.platform != "darwin" or threading.current_thread() is not threading.main_thread():
        return
    from pynput._util import darwin as util
    from pynput.keyboard import _darwin as kb

    read = getattr(util, "_mirrorvoice_read_layout", util.keycode_context)
    with read() as ctx:
        layout = ctx

    @contextlib.contextmanager
    def cached():
        yield layout

    util._mirrorvoice_read_layout = read
    util.keycode_context = cached  # used by get_unicode_to_keycode_map
    kb.keycode_context = cached    # used by Listener._run in its own thread
    _layout_ready = True


class Hotkeys:
    def __init__(self, ptt_key: str, mute_combo: str,
                 on_ptt: Callable[[bool], None], on_mute_toggle: Callable[[], None]) -> None:
        self.ptt_key, self.mute_combo = ptt_key, mute_combo
        self.on_ptt, self.on_mute_toggle = on_ptt, on_mute_toggle
        self._listeners: list = []

    def start(self) -> None:
        from pynput import keyboard

        if sys.platform == "darwin":
            if not _layout_ready:
                # reading it in the listener thread would crash the app (see prime_layout)
                raise RuntimeError("键盘布局未在主线程读取")
            # Two listener threads racing on PyObjC's lazy import raised
            # KeyError('AXIsProcessTrusted'); resolve it once here first.
            try:
                import HIServices

                HIServices.AXIsProcessTrusted()
            except Exception:  # noqa: BLE001
                pass

        target = getattr(keyboard.Key, self.ptt_key, None) or keyboard.KeyCode.from_char(self.ptt_key)

        def press(k):
            if k == target:
                self.on_ptt(True)

        def release(k):
            if k == target:
                self.on_ptt(False)

        kl = keyboard.Listener(on_press=press, on_release=release)
        kl.daemon = True
        kl.start()
        self._listeners.append(kl)
        if self.mute_combo:
            hk = keyboard.GlobalHotKeys({self.mute_combo: self.on_mute_toggle})
            hk.daemon = True
            hk.start()
            self._listeners.append(hk)

    def stop(self) -> None:
        for listener in self._listeners:
            try:
                listener.stop()
            except Exception:  # noqa: BLE001
                pass
        self._listeners.clear()


PTT_KEYS = {
    "alt_r": "右 Option/Alt",
    "ctrl_r": "右 Control",
    "cmd_r": "右 Command (Mac)",
    "f13": "F13",
    "caps_lock": "Caps Lock",
}
