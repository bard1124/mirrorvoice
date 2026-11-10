"""Online streaming recognition against a fake 阿里云百炼 server (tests/fake_dashscope.py)."""
import time

import numpy as np
import pytest

from mirrorvoice import online_asr
from mirrorvoice.asr import Decoded
from mirrorvoice.online_asr import OnlineSegmenter

from .fake_dashscope import FakeDashScope

SR = 16000


class _Vad:
    """Speech = loud samples (the real VAD needs real speech)."""

    def __init__(self):
        self.last = 0.0

    def accept_waveform(self, chunk):
        self.last = float(np.abs(chunk).max()) if len(chunk) else 0.0

    def is_speech_detected(self):
        return self.last > 0.05

    def empty(self):
        return True

    def pop(self):
        pass


class _Detector:
    def __init__(self, lang="zh"):
        self.lang = lang

    def decode(self, seg):
        return Decoded("", self.lang, [], [])


def tone(sec, level=0.2):
    return np.full(int(sec * SR), level, np.float32)


def silence(sec):
    return np.zeros(int(sec * SR), np.float32)


@pytest.fixture
def server():
    srv = FakeDashScope()
    yield srv
    srv.close()


def _seg(server, out, partial=None, notices=None, key="sk-test", detector=None):
    return OnlineSegmenter("them", 0.5, out.append, key, lang="en", alt="zh", on_partial=(partial if partial is not None else []).append,
                           on_notice=(notices if notices is not None else []).append, vad=_Vad(),
                           detector=detector or _Detector("en"), url=server.url)


def _feed(seg, audio, step=1600, settle=0.0):
    for i in range(0, len(audio), step):
        seg.feed(audio[i:i + step])
    deadline = time.monotonic() + settle
    while time.monotonic() < deadline:  # let the reader thread deliver results
        seg.feed(np.zeros(0, np.float32))
        time.sleep(0.02)


def test_no_audio_leaves_while_nobody_speaks(server):
    out = []
    seg = _seg(server, out)
    _feed(seg, silence(3))
    assert server.connections == 0 and out == []


def test_speech_is_streamed_with_lead_in_and_sentences_come_back_with_their_audio(server):
    server.script = [(800, "partial", "Could you", 0, 700), (1900, "final", "Could you share your screen?", 100, 1800)]
    out, partial = [], []
    seg = _seg(server, out, partial)
    _feed(seg, np.concatenate([silence(1), tone(2.0), silence(2)]), settle=0.5)
    task = server.tasks[0]
    assert task["model"] == "fun-asr-realtime" and task["parameters"]["format"] == "pcm"
    assert task["parameters"]["sample_rate"] == 16000 and task["parameters"]["language_hints"] == ["en"]
    assert "Authorization: Bearer sk-test" in server.headers[0]
    assert partial == ["Could you"]
    assert [u.text for u in out] == ["Could you share your screen?"] and out[0].lang == "en"
    sent_ms = len(next(iter(server.audio.values()))) // 32
    assert 2400 <= sent_ms <= 3700  # 0.5 s lead-in + 2 s speech + ≤1 s tail, not the silences
    # the service's 100–1800 ms map back onto the recording: lead-in started 0.5 s before the speech
    assert abs(len(out[0].audio) / SR - (1.7 + 0.15)) < 0.05
    assert float(out[0].audio[-1]) == pytest.approx(0.2)  # inside the speech, not the silence before it


def test_their_language_detected_locally_is_passed_on(server):
    server.script = [(1500, "final", "我们先讨论预算", 0, 1400)]
    out = []
    seg = _seg(server, out, detector=_Detector("zh"))  # the other side switched to my language
    _feed(seg, np.concatenate([tone(1.6), silence(1.2)]), settle=0.5)
    assert out and out[0].lang == "zh"


def test_a_pause_inside_one_task_keeps_the_timestamps_aligned(server):
    # 2 s speech, 3 s silence (not sent), 2 s speech: the service hears them back to back
    server.script = [(2400, "final", "first", 500, 2400), (4400, "final", "second", 3600, 4400)]
    out = []
    seg = _seg(server, out)
    first = tone(2.0, 0.2)
    second = tone(2.0, 0.4)
    _feed(seg, np.concatenate([silence(0.5), first, silence(3.0), second, silence(2.0)]), settle=0.6)
    assert [u.text for u in out] == ["first", "second"]
    assert len(server.tasks) == 1  # same task: the pause was shorter than the idle limit
    assert float(np.median(out[1].audio)) == pytest.approx(0.4)  # the second sentence's own audio


def test_idle_task_is_finished_before_the_services_timeout(server, monkeypatch):
    monkeypatch.setattr(online_asr, "IDLE_FINISH_S", 0.3)
    out = []
    seg = _seg(server, out)
    _feed(seg, np.concatenate([tone(1.0), silence(1.5)]))
    time.sleep(0.4)
    _feed(seg, silence(0.2), settle=0.3)
    assert server.finished  # finish-task sent; the next speech opens a new task on the same socket
    _feed(seg, np.concatenate([tone(1.0), silence(1.2)]), settle=0.3)
    assert len(server.tasks) == 2 and server.connections == 1


class _LocalSeg:
    def __init__(self, *a, **k):
        self.count, self.fed = 0, 0

    def feed(self, block, gate=True):
        self.fed += len(block)

    def flush(self):
        pass


def test_bad_key_falls_back_to_local_recognition(server, monkeypatch):
    monkeypatch.setattr(online_asr, "Segmenter", _LocalSeg)
    out, notices = [], []
    seg = _seg(server, out, notices=notices, key="bad")
    _feed(seg, np.concatenate([tone(1.0), silence(0.5)]))
    assert isinstance(seg.local, _LocalSeg) and "API key" in notices[0] and "本机识别" in notices[0]
    _feed(seg, tone(0.5))
    assert seg.local.fed > 0  # the rest of the session runs on the local recognizer


def test_repeated_server_failures_fall_back_too(server, monkeypatch):
    monkeypatch.setattr(online_asr, "Segmenter", _LocalSeg)
    server.fail_after_ms = 300
    out, notices = [], []
    seg = _seg(server, out, notices=notices)
    for _ in range(4):
        _feed(seg, np.concatenate([tone(0.8), silence(1.5)]), settle=0.3)
    assert seg.local is not None and notices


def test_engine_requires_a_key_for_online_recognition(home):
    from mirrorvoice import keystore
    from mirrorvoice.config import Settings
    from mirrorvoice.engine import Engine

    keystore.forget_cache()
    keystore.save("dashscope", "")
    e = Engine(lambda *a, **k: None)
    s = Settings(asr_mode="online", asr_me=False, asr_them=True)
    assert e._asr_key(s, "me") == ""
    with pytest.raises(RuntimeError, match="阿里云百炼"):
        e._asr_key(s, "them")
    keystore.save("dashscope", "sk-x")
    assert e._asr_key(s, "them") == "sk-x"


def test_text_helpers():
    from mirrorvoice.online_asr import last_sentence_end, normalize, strip_chars, words_end

    assert normalize("大约60%，剩下的。") == "大约60%剩下的"
    assert strip_chars("我们先讨论。然后看时间，", 5) == "然后看时间，"
    assert strip_chars("Hello there. How are", 10) == "How are"
    assert last_sentence_end("完成了。剩下的") == 4 and last_sentence_end("完成了。") == 0
    assert last_sentence_end("It is 3.5 percent. Next") == len("It is 3.5 percent.")
    assert words_end([("我们", 300), ("先讨论。", 900), ("然后", 1200)], 5) == 900


def test_long_speech_without_a_pause_is_split_at_stable_sentence_ends(server):
    # the service never ends the sentence (no pause); partials grow and keep "…预算。" stable
    a = "我们这个季度先讨论预算。"
    b = a + "然后再看上线时间。"
    server.script = [(1000, "partial", "我们这个季度先讨论", 0, 1000),
                     (4500, "partial", a + "然后", 0, 4500),
                     (5000, "partial", a + "然后再", 0, 5000),       # "…预算。" stable twice, >4 s: send it
                     (9000, "partial", b + "最后", 0, 9000),
                     (9500, "partial", b + "最后定", 0, 9500),       # "…时间。" stable: send that too
                     (11500, "final", b + "最后定一下负责人。", 0, 11500)]
    out, partial = [], []
    seg = _seg(server, out, partial)
    _feed(seg, np.concatenate([tone(11.6), silence(1.2)]), step=800, settle=0.8)
    assert [u.text for u in out] == [a, "然后再看上线时间。", "最后定一下负责人。"]
    assert partial[-1] == "最后定"  # subtitles show only what was not sent on yet
    assert abs(sum(len(u.audio) for u in out) / SR - 11.5) < 0.6  # the parts cover the audio once


def test_long_unfinished_subtitles_get_a_provisional_translation(monkeypatch):
    from mirrorvoice.config import Settings
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(their_lang="en", my_lang="zh"), True

    class _Tr:
        def translate(self, text, *a, **k):
            return "临时译文"
    e._interim_tr = _Tr()
    e._on_partial("them", "short")  # too short for a provisional translation
    e._on_partial("them", "Could you share your screen so that")
    deadline = time.monotonic() + 2
    while not any(d.get("tgt") for _k, d in events) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ("partial", {"who": "them", "src": "Could you share your screen so that", "tgt": "临时译文"}) in events
    e._interim_at = 0
    e._them_gen += 1  # the sentence ended while the provisional translation was running: dropped
    gen_before = e._them_gen

    class _Slow:
        def translate(self, text, *a, **k):
            e._them_gen = gen_before + 1
            return "过时"
    e._interim_tr = _Slow()
    e._on_partial("them", "Could you share your screen so that we can")
    time.sleep(0.3)
    assert not any(d.get("tgt") == "过时" for _k, d in events)
