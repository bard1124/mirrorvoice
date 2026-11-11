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
