"""Sentence splitting: early cuts move back to punctuation, the rest is carried; language fix-ups;
the translator gets the conversation so far. Uses fakes; the real-model check runs when the
models are downloaded (not in CI)."""
import shutil
import subprocess
import sys
import time

import numpy as np
import pytest

from mirrorvoice import asr
from mirrorvoice.asr import Decoded, Segmenter, Utterance, punctuation_cut


def test_cut_prefers_the_last_sentence_end_then_a_comma():
    d = Decoded("", "zh", list("今天，我们讨论预算。然后，看时间，"), [0.2, 0.5, 1, 1.5, 2, 2.2, 2.5, 3.0, 3.4, 3.6,
                                                         3.8, 4.0, 4.2, 4.4, 4.6, 4.8])
    assert punctuation_cut(d, 5.0) == 3.6  # "。" beats the later commas
    commas = Decoded("", "zh", list("今天，我们讨论，预算"), [0.2, 0.5, 2.5, 3, 3.5, 3.8, 4.1, 4.3, 4.6, 4.9])
    assert punctuation_cut(commas, 5.0) == 4.3
    assert punctuation_cut(Decoded("", "zh", list("你好，"), [0.1, 0.3, 0.5]), 5.0) == 0.0  # head too short
    assert punctuation_cut(Decoded("", "zh", list("今天讨论预算。"), [1, 1.5, 2, 2.5, 3, 3.5, 4.9]), 5.0) == 0.0  # no tail


class _Rec:
    def __init__(self, lang, text="识别结果"):
        self.lang, self.text, self.calls = lang, text, 0

    def decode(self, seg):
        self.calls += 1
        return Decoded(self.text, self.lang, list(self.text), [0.1 * i for i in range(len(self.text))])


def _seg(rec, lang="zh", alt="en"):
    return Segmenter("me", 0.5, lambda u: None, lang=lang, alt=alt, recognizer=rec, vad=object())


def test_misheard_language_is_decoded_again_in_the_expected_one(monkeypatch):
    fixed = _Rec("zh", "我们先讨论预算")
    monkeypatch.setattr(asr, "Recognizer", lambda lang: fixed)
    seg = _seg(_Rec("yue", "面下看的应下周"))  # Mandarin heard as Cantonese
    d = seg.recognize(np.zeros(1600, np.float32))
    assert (d.text, d.lang) == ("我们先讨论预算", "zh") and fixed.calls == 1


def test_the_other_sides_language_is_kept_for_passthrough(monkeypatch):
    monkeypatch.setattr(asr, "Recognizer", lambda lang: pytest.fail("no second decode"))
    d = _seg(_Rec("en", "Sounds good.")).recognize(np.zeros(1600, np.float32))
    assert (d.text, d.lang) == ("Sounds good.", "en")
    d = _seg(_Rec("ja", "はい"), lang="").recognize(np.zeros(1600, np.float32))  # "auto": anything goes
    assert d.lang == "ja"


class _Vad:
    """Scripted VAD: segments are handed out when the test says so."""

    def __init__(self):
        self.ready, self.speech = [], False

    def accept_waveform(self, chunk):
        pass

    def is_speech_detected(self):
        return self.speech

    def flush(self):
        pass

    def empty(self):
        return not self.ready

    @property
    def front(self):
        start, n = self.ready[0]
        return type("F", (), {"start": start, "samples": np.zeros(n, np.float32)})()

    def pop(self):
        self.ready.pop(0)


class _Timed:
    """Recognizer whose text depends on which stretch of the recording it is given."""

    def __init__(self, audio):
        self.audio = audio

    def decode(self, seg):
        sec = len(seg) / 16000
        marks = int(round(float(seg[0]) * 10)) if len(seg) else 0  # first sample encodes where we are
        if marks == 1 and sec > 5:  # the early-cut segment: "。" at 4.2 s, then more words
            return Decoded("今天先讨论预算。然后看", "zh", list("今天先讨论预算。然后看"),
                           [0.5, 0.9, 1.3, 1.7, 2.1, 2.6, 3.1, 4.2, 4.6, 5.0, 5.3])
        return Decoded(f"段{marks}·{sec:.1f}s", "zh", ["段"], [0.1])


def test_early_cut_moves_to_punctuation_and_carries_the_rest():
    audio = np.zeros(16000 * 12, np.float32)
    audio[:16000 * 6] = 0.1   # first stretch "1"
    audio[16000 * 6:] = 0.2   # second stretch "2"
    out = []
    vad = _Vad()
    seg = Segmenter("me", 0.5, out.append, lang="zh", alt="en", recognizer=_Timed(audio), vad=vad)
    seg.feed(audio[:16000 * 6])
    seg.early_end = seg.fed  # as if the long-speech rule had just flushed the VAD
    vad.ready.append((int(0.3 * 16000), 16000 * 6 - int(0.3 * 16000)))
    seg._pop()
    assert out[-1].text == "今天先讨论预算。然后看" or out[-1].text.startswith("段")  # head re-decoded
    head = out[-1]
    assert abs(len(head.audio) / 16000 - 4.25) < 0.05  # cut just after the "。" at 4.2 s
    assert seg.carry and seg.carry[0] == int(4.25 * 16000)
    vad.speech = True
    seg.feed(audio[16000 * 6:16000 * 9])
    vad.ready.append((16000 * 6, 16000 * 3))  # the speaker went on: the carried words lead the next one
    seg._pop()
    nxt = out[-1]
    assert nxt.audio[0] == pytest.approx(0.1) and abs(len(nxt.audio) / 16000 - (9 - 4.25)) < 0.05
    assert seg.carry is None


def test_carried_tail_goes_out_alone_when_the_speaker_stops():
    audio = np.full(16000 * 6, 0.1, np.float32)
    out, vad = [], _Vad()
    seg = Segmenter("me", 0.5, out.append, lang="zh", recognizer=_Timed(audio), vad=vad)
    seg.feed(audio)
    seg.early_end = seg.fed
    vad.ready.append((int(0.3 * 16000), 16000 * 6 - int(0.3 * 16000)))
    seg._pop()
    assert seg.carry
    seg.feed(np.zeros(16000, np.float32))  # one second of silence, no new speech
    assert seg.carry is None and len(out) == 2


# ---------------------------------------------------------------- translator context


class _Echo:
    def __init__(self):
        self.seen = []

    def chat(self, msgs):
        self.seen.append(msgs)
        return "OK."


def test_history_is_given_as_earlier_turns():
    from mirrorvoice.translate import Translator

    tr = Translator.__new__(Translator)
    tr.glossary, tr.meeting, tr.backend = [], "", _Echo()
    tr.translate("他们希望补一份报告。", "zh", "en", history=[("风控规则这边我们开了会。", "We met about the risk rules.")])
    msgs = tr.backend.seen[-1]
    assert [m["role"] for m in msgs] == ["system", "user", "assistant", "user"]
    assert "风控规则" in msgs[1]["content"] and msgs[2]["content"] == "We met about the risk rules."


def test_engine_continues_only_unfinished_sentences_and_keeps_recent_history():
    from mirrorvoice.engine import Engine

    e = Engine(lambda *a, **k: None)
    u = lambda text, t_end, dur: Utterance(text, "zh", np.zeros(int(dur * 16000), np.float32), t_end, 0.1)
    e._remember("me", "我们这个季度的目标是", "Our goal this quarter is", 100.0)
    assert e._context("me", u("自动化对账。", 102.5, 2.0)) == ("我们这个季度的目标是", "Our goal this quarter is")
    e._remember("me", "自动化对账。", "to automate reconciliation.", 102.5)
    nxt = u("剩下的是清结算。", 104.0, 1.5)
    assert e._context("me", nxt) is None  # the previous part ended a sentence: no "continues" prompt
    assert e._history("me", nxt, None)[-1] == ("自动化对账。", "to automate reconciliation.")
    later = u("下一个话题。", 400.0, 1.0)
    assert e._history("me", later, None) == []  # minutes later: no stale context


# ---------------------------------------------------------------- real models (local only)


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("say"), reason="macOS speech synthesis")
def test_real_speech_is_cut_at_sentence_ends(tmp_path, monkeypatch):
    import os
    from pathlib import Path

    from mirrorvoice import models

    real = Path(os.environ.get("MIRRORVOICE_TEST_MODELS",
                               Path.home() / "Library/Application Support/TongChuan/models"))
    monkeypatch.setattr(models, "MODELS", real)  # the tests' own data folder has no models
    if not (models.ASR.ready() and models.VAD.ready()):
        pytest.skip("speech models not downloaded")
    voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    if "Tingting" not in voices:
        pytest.skip("no Mandarin voice")
    text = ("我们这个季度的目标是把支付系统的对账流程全部自动化，目前已经完成了大约百分之六十，剩下的部分主要是"
            "跨境交易的清结算，因为涉及多个币种和不同银行的接口，所以进度会慢一些。另外风控规则这边，我们上周和"
            "法务部门开了一次会，他们希望在上线之前补充一份数据合规的评估报告。")
    aiff, wav = tmp_path / "s.aiff", tmp_path / "s.wav"
    subprocess.run(["say", "-v", "Tingting", "-r", "230", "-o", str(aiff), text], check=True)
    subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), "-o", str(wav)], check=True)
    import soundfile as sf

    audio, _ = sf.read(wav, dtype="float32")
    out = []
    seg = Segmenter("me", 0.5, out.append, lang="zh", alt="en")
    for i in range(0, len(audio), 1600):
        seg.feed(audio[i:i + 1600])
    seg.feed(np.zeros(32000, np.float32))
    assert len(out) >= 3
    assert all(u.text[-1] in "。！？，,.!?" for u in out), [u.text for u in out]
    joined = "".join(u.text for u in out)
    assert "风控规则" in joined and "剩下的部分" in joined
