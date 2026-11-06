"""Global push-to-talk key + mute toggle hotkey (pynput).

macOS needs Accessibility / Input Monitoring permission for global keys; without it the
listeners simply never fire and the app keeps working in continuous mode.
"""
from __future__ import annotations

import sys
from typing import Callable


class Hotkeys:
    def __init__(self, ptt_key: str, mute_combo: str,
                 on_ptt: Callable[[bool], None], on_mute_toggle: Callable[[], None]) -> None:
        self.ptt_key, self.mute_combo = ptt_key, mute_combo
        self.on_ptt, self.on_mute_toggle = on_ptt, on_mute_toggle
        self._listeners: list = []

    def start(self) -> None:
        from pynput import keyboard

        if sys.platform == "darwin":
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
