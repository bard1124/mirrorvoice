"""Devices switched while running: microphone, call output, the other side's source."""
import numpy as np
import pytest

from mirrorvoice import asr, audio
from mirrorvoice.config import Settings


class _Cap:
    made = []

    def __init__(self, name, on_audio, block=512):
        if name == "坏设备":
            raise LookupError("找不到设备：坏设备")
        self.name, self.label, self.started, self.closed = name, f"{name}（已打开）", False, False
        _Cap.made.append(self)

    def start(self):
        self.started = True

    def close(self):
        self.closed = True

    def stalled(self):
        return False


class _Seg:
    count = 0

    def __init__(self, *a, **k):
        pass


@pytest.fixture
def listener(monkeypatch):
    monkeypatch.setattr(asr, "Capture", _Cap)
    monkeypatch.setattr(asr, "Segmenter", _Seg)
    _Cap.made = []
    return asr.Listener("me", "外置麦克风", "zh", 0.5, lambda u: None)


def test_microphone_switches_new_first_then_old_closes(listener):
    old = listener.capture
    label = listener.switch_source("MacBook Pro麦克风")
    new = listener.capture
    assert label == "MacBook Pro麦克风（已打开）" and new.started and old.closed and not new.closed
    assert listener.source == "MacBook Pro麦克风" and listener._flush.is_set()  # the sentence in progress ends


def test_a_device_that_cannot_open_leaves_the_old_one_running(listener):
    old = listener.capture
    with pytest.raises(LookupError):
        listener.switch_source("坏设备")
    assert listener.capture is old and not old.closed and listener.source == "外置麦克风"


class _Out:
    def __init__(self, device, src_sr):
        self.name, self.sr, self.closed = f"out{device}", 48000, False
        self.buf = np.zeros(0, np.float32)
        import threading
        self.lock = threading.Lock()

    def put(self, chunk, sr):
        self.buf = np.concatenate([self.buf, chunk])

    def close(self):
        self.closed = True


def test_call_output_switch_keeps_unplayed_speech(monkeypatch):
    monkeypatch.setattr(audio, "DeviceOut", _Out)
    p = audio.Player(1, 2)
    p.outs[0].buf = np.ones(4800, np.float32)  # 0.1 s not played yet
    old, mon = p.outs[0], p.outs[1]
    p.set_call(3)
    assert p.outs[0].name == "out3" and len(p.outs[0].buf) == 4800 and old.closed
    assert p.outs[1] is mon and not mon.closed  # the headphone monitor is untouched


class _Listener:
    def __init__(self, name, source, *a, **k):
        self.source, self.label, self.closed = source, f"{source}（字幕）", False

    def start(self):
        pass

    def close(self):
        self.closed = True

    def switch_source(self, source):
        self.source = source
        return f"{source}（字幕）"


def test_their_source_starts_switches_and_stops_subtitles(monkeypatch, home):
    from mirrorvoice import engine as eng

    monkeypatch.setattr(eng, "Listener", _Listener)
    e = eng.Engine(lambda *a, **k: None)
    e.s, e.running = Settings(subtitles=True), True
    assert e.set_their_source("BlackHole 16ch") == "BlackHole 16ch（字幕）"  # was "不显示字幕"
    them = e._them
    assert e.set_their_source("loopback:default") == "loopback:default（字幕）" and e._them is them
    assert e.set_their_source("") == "" and them.closed and e._them is None
    assert them.close not in e._closers


def test_switching_needs_a_running_engine(home):
    from mirrorvoice.engine import Engine

    e = Engine(lambda *a, **k: None)
    assert e.set_mic("外置麦克风") == "" and e.set_call_out("BlackHole 2ch") == "" and e.set_their_source("x") == ""


class _Seg2:
    def __init__(self):
        self.lang, self.alt, self._fixed = "zh", "en", object()


class _L2:
    def __init__(self):
        self.seg = _Seg2()


def _running(events, **kw):
    from mirrorvoice.engine import Engine

    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running, e._ready = Settings(**kw), True, True
    return e


def test_apply_language_pair_retargets_listeners_and_forgets_earlier_turns(home):
    e = _running([], my_lang="en", their_lang="zh")
    e._me, e._them = _L2(), _L2()
    e._prev = {"me": "old", "them": "old"}
    e._voice = None
    e.s.speak = False
    e.apply({"my_lang", "their_lang"})
    assert (e._me.seg.lang, e._me.seg.alt, e._me.seg._fixed) == ("en", "zh", None)
    assert (e._them.seg.lang, e._them.seg.alt) == ("zh", "en")
    assert e._prev == {"me": None, "them": None}


def test_apply_models_goes_to_the_worker_queue(home):
    e = _running([])
    e.apply({"llm"})
    prio, _, kind, _ = e._jobs.get_nowait()
    assert (prio, kind) == (0, "models")


def test_apply_does_nothing_when_stopped(home):
    from mirrorvoice.engine import Engine

    e = Engine(lambda *a, **k: None)
    e.apply({"llm", "subtitles", "ptt_key"})  # no queue, no threads, no error


def test_apply_new_push_to_talk_key_restarts_hotkeys(monkeypatch, home):
    from mirrorvoice import engine as eng

    made = []

    class _HK:
        def __init__(self, key, *a):
            self.key, self.stopped = key, False
            made.append(self)

        def start(self):
            pass

        def stop(self):
            self.stopped = True

    monkeypatch.setattr(eng, "Hotkeys", _HK)
    monkeypatch.setattr(eng.hotkeys, "prime_layout", lambda: None)
    e = _running([], ptt_key="right_option")
    e._start_hotkeys()
    e.s.ptt_key = "right_cmd"
    e.apply({"ptt_key"})
    assert [h.key for h in made] == ["right_option", "right_cmd"] and made[0].stopped and not made[1].stopped
    assert made[0].stop not in e._closers and made[1].stop in e._closers


def test_apply_subtitles_off_closes_their_listener(monkeypatch, home):
    from mirrorvoice import engine as eng

    monkeypatch.setattr(eng, "Listener", _Listener)
    events = []
    e = _running(events, subtitles=True, their_source="BlackHole 16ch")
    e._apply_subtitles()
    them = e._them
    assert them is not None
    e.s.subtitles = False
    e._apply_subtitles()
    assert them.closed and e._them is None and ("notice", {"text": "对方字幕已关闭"}) in events


def test_switch_translators_keeps_old_when_model_missing(monkeypatch, home):
    from mirrorvoice import engine as eng

    class _M:
        key, title = "llm", "Qwen3-8B"

        def ready(self):
            return False

    monkeypatch.setattr(eng.models, "required", lambda q, s: [_M()])
    events = []
    e = _running(events)
    old, old_them = object(), object()
    assert e._switch_translators(old, old_them) == (old, old_them)
    assert "还没下载" in events[-1][1]["text"]
