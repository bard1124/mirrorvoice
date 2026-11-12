"""开始 then 停止 while the models are still loading: the start-up gives up, closes what it made and
says nothing more — no "运行中" after 停止, no microphone left open."""
import threading
import time

import pytest

from mirrorvoice import engine as eng
from mirrorvoice.config import Settings


class _Voice:
    clone, speed = True, 1.0

    def __init__(self, gate):
        gate.wait(5)  # loading the voice: takes a while, cannot be interrupted
        self.closed = False

    def stream(self, text, lang):
        return iter(())

    def close(self):
        self.closed = True


class _Closable:
    label = "fake"

    def __init__(self, *a, **k):
        self.closed = False
        made.append(self)

    def start(self):
        pass

    def close(self):
        self.closed = True


class _Tr:
    glossary, label, backend = {}, "fake", None

    def translate(self, *a, **k):
        return "hi"


made: list = []


@pytest.fixture
def fake(home, monkeypatch):
    made.clear()
    gate = threading.Event()
    voices, loads = [], []

    def make_voice(backend, path):
        loads.append(1)
        v = _Voice(gate)
        voices.append(v)
        return v

    monkeypatch.setattr(eng.hotkeys, "prime_layout", lambda: None)
    monkeypatch.setattr(eng, "check_models", lambda req: "")
    monkeypatch.setattr(eng.models, "required", lambda q, s=None: [])
    monkeypatch.setattr(eng.models, "qwen_asr_sides", lambda s: set())
    monkeypatch.setattr(eng, "make_voice", make_voice)
    monkeypatch.setattr(eng, "find_device", lambda name, kind: name)
    monkeypatch.setattr(eng, "Player", _Closable)
    monkeypatch.setattr(eng, "Listener", _Closable)
    monkeypatch.setattr(eng.Engine, "_translators", lambda self, s: (_Tr(), _Tr()))
    monkeypatch.setattr(eng.Engine, "_start_hotkeys", lambda self: None)
    events = []
    e = eng.Engine(lambda kind, **d: events.append((kind, d)))
    s = Settings(my_lang="zh", their_lang="en", speak=True, subtitles=False, call_out="out")
    return e, s, events, gate, voices, loads


def _settle(thread, timeout=5):
    thread.join(timeout)
    assert not thread.is_alive()


def test_stop_while_the_voice_loads_gives_up_the_start(fake):
    e, s, events, gate, voices, loads = fake
    e.start(s)
    time.sleep(0.1)  # loading the voice now
    e.stop()
    gate.set()  # the voice finishes loading after 停止
    _settle(e._boot_thread)
    kinds = [k for k, _ in events]
    assert kinds[-1] == "stopped" and "ready" not in kinds  # nothing after 停止, no 运行中
    assert voices[0].closed and not made  # the voice is let go; no microphone or device opened
    assert e._closers == []


def test_start_stop_start_runs_one_session(fake):
    e, s, events, gate, voices, loads = fake
    e.start(s)
    time.sleep(0.1)
    e.stop()
    first = e._boot_thread
    e.start(s)  # again, while the first start-up is still loading
    time.sleep(0.1)
    assert len(loads) == 1  # the second waits for the first to wind down instead of loading alongside
    gate.set()
    _settle(first)
    deadline = time.monotonic() + 5
    while not any(k == "ready" for k, _ in events) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert [k for k, _ in events].count("ready") == 1 and e.running
    assert voices[0].closed and not voices[1].closed
    assert all(not m.closed for m in made) and len(made) == 2  # this session's audio device and microphone
    e.stop()
    assert voices[1].closed and all(m.closed for m in made)
