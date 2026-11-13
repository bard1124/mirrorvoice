"""The other side's subtitles with local models — what the meeting of 2026-11-13 showed (23 minutes
of listening, 392 lines): sentences cut into pieces, English shown untranslated because it was
labelled Chinese, lines left in English, short sentences waiting seconds for one to join."""
import time

import numpy as np
import pytest

from mirrorvoice import asr
from mirrorvoice.asr import Decoded, Segmenter, Utterance, reads_as_latin
from mirrorvoice.config import Settings
from mirrorvoice.translate import Translator, left_untranslated


# ---------------------------------------------------------------- whole sentences
def test_their_listener_waits_for_whole_sentences_and_guesses_at_short_pauses(monkeypatch):
    from mirrorvoice import engine as eng

    made = {}

    class _L:
        def __init__(self, name, source, lang, pause, on_utt, *a, **k):
            made.update(k, name=name, pause=pause)
            self.label = source

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(eng, "Listener", _L)
    e = eng.Engine(lambda *a, **k: None)
    e.s = Settings(my_lang="zh", their_lang="en", pause=0.35, stream=True)  # 边说边译 is on — for my voice
    e._words, e._qwen_sides = None, set()
    e._start_them("BlackHole 16ch")
    assert made["name"] == "them" and made["pause"] == pytest.approx(0.7) and made["stream"] is False
    assert made["patient"] is True and made["on_guess"] == e._on_them_guess
    e.s.pause = 1.0  # a longer pause chosen by hand is kept
    e._start_them("BlackHole 16ch")
    assert made["pause"] == 1.0


def test_a_patient_segmenter_cuts_long_speech_later_and_at_a_clearer_pause():
    quick = Segmenter("me", 0.35, lambda u: None, recognizer=object(), vad=object())
    patient = Segmenter("them", 0.7, lambda u: None, recognizer=object(), vad=object(), patient=True)
    assert (quick.LONG_S, quick.DIP_S) == (5.0, 0.30)  # mine: unchanged
    assert patient.LONG_S > quick.LONG_S and patient.DIP_S > quick.DIP_S
    assert patient.HARD_S > quick.HARD_S and patient.MAX_S > quick.MAX_S and patient.MAX_S < 15  # (the VAD's own limit)


# ---------------------------------------------------------------- English labelled Chinese
@pytest.mark.parametrize("text", ["0686. Yeah, one second.", "80686yes this what.", "using the。", "me.", "VATT。",
                                  "呃 for eightight one one呃sevenf.", "唉，sorryok哎it I。",
                                  "呃，永超 andM ask them to change the or."])
def test_western_speech_labelled_chinese_is_recognised_by_its_words(text):
    assert reads_as_latin(text)


@pytest.mark.parametrize("text", ["你说哪个bug呃，张全这个。", "bug他这个bug跟我跟他。", "但这个现在叫doopunity document。",
                                  "他就是说他只要看到这边有这个job document。", "是QA环境还是UAT环境？", "嗯。", "要有人修的是吧？",
                                  "so梦雨，你可以再看一下开发出据不是个bu，看看有没有什么不一样的解。", "ready for位了是验的过程中的问题。"])
def test_chinese_with_a_term_or_two_of_english_stays_chinese(text):
    assert not reads_as_latin(text)


class _Rec:
    def __init__(self, lang, text):
        self.lang, self.text, self.calls = lang, text, 0

    def decode(self, seg):
        self.calls += 1
        return Decoded(self.text, self.lang, list(self.text), [0.1 * i for i in range(len(self.text))])


def test_their_english_heard_as_chinese_is_decoded_again_as_english(monkeypatch):
    """It was accepted as "they spoke my language", shown as it was and never translated."""
    fixed = _Rec("en", "For eight one one seven.")
    monkeypatch.setattr(asr, "Recognizer", lambda lang: fixed)
    seg = Segmenter("them", 0.7, lambda u: None, lang="en", alt="zh", recognizer=_Rec("zh", "呃 for eightight one one呃sevenf."),
                    vad=object())
    d = seg.recognize(np.zeros(1600, np.float32))
    assert (d.text, d.lang) == ("For eight one one seven.", "en") and fixed.calls == 1
    # a colleague who does speak Chinese in the meeting is shown as said
    seg = Segmenter("them", 0.7, lambda u: None, lang="en", alt="zh", recognizer=_Rec("zh", "你说哪个bug呃，张全这个。"), vad=object())
    assert seg.recognize(np.zeros(1600, np.float32)).lang == "zh" and fixed.calls == 1
    # and my own side is as before: English at my microphone is mine to pass through
    seg = Segmenter("me", 0.35, lambda u: None, lang="zh", alt="en", recognizer=_Rec("en", "Sounds good."), vad=object())
    assert seg.recognize(np.zeros(1600, np.float32)).lang == "en" and fixed.calls == 1


# ---------------------------------------------------------------- lines left in English
@pytest.mark.parametrize("out,left", [
    ("just set it back to the reporter.", True), ("在评论下。 yeah， please check.", True), ("所以 just 想在那上。 yeah，yeah，yeah。", True),
    ("你好，Gabriel，Gabriel Sas团队。", False), ("这个 bug 在 QA 环境里。", False), ("让我们把 feature flag 关掉。", False),
    ("OK", False), ("好的。", False), ("UAT 和 QA 都已经部署了 OneForma。", False)])
def test_a_subtitle_still_in_the_speakers_language_is_noticed(out, left):
    assert bool(left_untranslated(out, "zh")) is left
    assert left_untranslated(out, "en") == 0  # (only targets written in Chinese characters are judged)


class _Backend:
    no_think = False

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def chat(self, msgs):
        self.asked.append(msgs)
        return self.answers.pop(0)


def test_an_untranslated_subtitle_is_asked_for_once_more_and_the_better_answer_stays(monkeypatch):
    monkeypatch.setattr("mirrorvoice.translate.load_glossary", lambda: [])
    b = _Backend("just set it back to the reporter.", "把它退回给报告人。")
    tr = Translator(b)
    out = tr.translate("Just set it back to the reporter.", "en", "zh", subtitle=True, history=[("Okay.", "okay.")])
    assert out == "把它退回给报告人。" and len(b.asked) == 2
    again = b.asked[1]
    assert len(again) == 2 and "Chinese" in again[1]["content"] and "Okay." not in str(again)  # alone, and told so

    b = _Backend("在评论下。 yeah， please check.", "yeah please check under the comment okay")  # the retry is worse
    assert Translator(b).translate("Under the comment. Yeah, please check.", "en", "zh", subtitle=True) == "在评论下。 yeah， please check."
    b = _Backend("把它退回给报告人。")  # nothing left over: one call
    assert Translator(b).translate("Just set it back.", "en", "zh", subtitle=True) and len(b.asked) == 1
    b = _Backend("Set it back to the reporter.")  # my own voice into English is not judged by this
    assert Translator(b).translate("把它退回给报告人。", "zh", "en") and len(b.asked) == 1


# ---------------------------------------------------------------- shown while they speak
class _Tr:
    glossary = []
    backend = object()

    def __init__(self, out="他提到我们需要做一个总结。"):
        self.out, self.asked = out, []

    def translate(self, text, src, tgt, subtitle=False, on_text=None, **k):
        self.asked.append(text)
        if on_text:
            on_text(self.out[:6])
        return self.out


class _Side:
    talking = False

    def speaking(self):
        return self.talking


def _engine():
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en"), True

    class _Rows:
        def add(self, *a):
            pass
    e._transcript, e._them, e._me = _Rows(), _Side(), _Side()
    return e, events


def _utt(text, lang="en"):
    return Utterance(text, lang, np.full(24000, 0.1, np.float32), time.time(), 0.05)


def test_what_they_have_said_so_far_is_shown_at_a_short_pause_and_translated_when_free():
    e, events = _engine()
    tr = _Tr()
    e._on_them_guess(_utt("He mentioned that we needed to do a summary."))
    assert events[-1] == ("partial", {"who": "them", "src": "He mentioned that we needed to do a summary.", "tgt": ""})
    e._on_them_guess(_utt("He mentioned that we needed to do a summary of the recent changes."))  # they went on
    jobs = []
    while not e._jobs.empty():
        jobs.append(e._jobs.get_nowait())
    assert [(j[0], j[2]) for j in jobs] == [(2, "them_guess")]  # one job, behind my speech and their sentences
    e._do_them_guess(tr)
    assert tr.asked == ["He mentioned that we needed to do a summary of the recent changes."]  # the newest only
    assert events[-1][0] == "partial" and events[-1][1]["tgt"] == tr.out + " …"

    # the sentence ends right there: the translation made at the pause is the sentence's
    e._on_them(_utt("He mentioned that we needed to do a summary of the recent changes."))
    _, _, kind, utt = e._jobs.get_nowait()
    e._do_them(tr, utt)
    assert len(tr.asked) == 1 and events[-1][0] == "them" and events[-1][1]["tgt"] == tr.out


def test_a_guess_never_holds_up_the_sentence_or_my_voice():
    e, events = _engine()
    tr = _Tr()
    e._on_them_guess(_utt("He mentioned that we needed to."))
    e._on_them(_utt("He mentioned that we needed to do a summary."))  # the sentence is finished first
    e._do_them_guess(tr)
    assert tr.asked == []  # out of date: not translated at all

    e._on_them_guess(_utt("So the next point is about the release."))
    e._me.talking = True  # I begin to speak while it is being written
    n = len(events)
    e._do_them_guess(tr)
    assert tr.asked == ["So the next point is about the release."] and len(events) == n  # given up, nothing shown
    assert getattr(e, "_them_guess_done", None) is None

    for text, lang in (("Okay.", "en"), ("Yeah, yeah.", "en"), ("好的，我看一下。", "zh")):  # nothing to preview
        n = len(events)
        e._on_them_guess(_utt(text, lang))
        assert len(events) == n


def test_a_line_left_in_english_is_not_given_to_the_next_one_as_an_example():
    e, events = _engine()
    e._do_them(_Tr("just set it back to the reporter."), _utt("Just set it back to the reporter."))
    assert events[-1][0] == "them" and not e._hist["them"]  # shown, not remembered
    e._do_them(_Tr("把它退回给报告人。"), _utt("Just set it back to the reporter."))
    assert len(e._hist["them"]) == 1


def test_a_short_sentence_waits_two_seconds_at_most_for_one_to_join():
    """While they kept talking, "Everything is ok." waited 4-6 s (9 lines of the meeting)."""
    from mirrorvoice.engine import Engine

    assert Engine.HOLD_MAX_S <= 2.0
