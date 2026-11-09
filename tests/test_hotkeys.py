import sys
import threading

import pytest

from mirrorvoice import hotkeys

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS input-source API")


@pytest.fixture
def pynput_darwin(monkeypatch):
    """pynput's macOS modules with every patch undone afterwards; records which threads read the layout."""
    from pynput._util import darwin as util
    from pynput.keyboard import _darwin as kb

    monkeypatch.setattr(util, "keycode_context", util.keycode_context)
    monkeypatch.setattr(kb, "keycode_context", kb.keycode_context)
    monkeypatch.setattr(util, "_mirrorvoice_read_layout", util.keycode_context, raising=False)
    monkeypatch.setattr(hotkeys, "_layout_ready", False)
    readers: list[str] = []
    for name in ("TISCopyCurrentKeyboardInputSource", "TISCopyCurrentASCIICapableKeyboardLayoutInputSource"):
        real = getattr(util.CarbonExtra, name)

        def spy(*a, _real=real):
            readers.append(threading.current_thread().name)
            return _real(*a)
        monkeypatch.setattr(util.CarbonExtra, name, spy)
    return util, kb, readers


def test_listener_threads_reuse_the_layout_read_on_the_main_thread(pynput_darwin):
    """Reading the input source off the main thread killed the app when Pinyin was active (SIGTRAP)."""
    util, kb, readers = pynput_darwin
    hotkeys.prime_layout()
    assert readers and set(readers) == {threading.main_thread().name}
    readers.clear()
    seen = []

    def listener_like():
        with kb.keycode_context() as ctx:
            seen.append(ctx)
        seen.append(util.get_unicode_to_keycode_map())
    t = threading.Thread(target=listener_like, name="listener")
    t.start()
    t.join()
    assert readers == []  # nothing touched the input-source API off the main thread
    assert seen[0][1] and "a" in seen[1]  # real layout data: keycodes still translate


def test_prime_layout_off_main_thread_does_nothing(pynput_darwin):
    _, _, readers = pynput_darwin
    t = threading.Thread(target=hotkeys.prime_layout)
    t.start()
    t.join()
    assert readers == [] and not hotkeys._layout_ready


def test_hotkeys_refuse_to_start_without_a_primed_layout(pynput_darwin):
    with pytest.raises(RuntimeError, match="键盘布局"):
        hotkeys.Hotkeys("alt_r", "<ctrl>+<alt>+m", lambda _: None, lambda: None).start()


def test_prime_layout_rereads_each_time(pynput_darwin):
    _, _, readers = pynput_darwin
    hotkeys.prime_layout()
    n = len(readers)
    hotkeys.prime_layout()  # a second start reads the (possibly switched) layout again, not the cache
    assert len(readers) > n
