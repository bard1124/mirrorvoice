"""Fixes from reviewing a real meeting: faint sounds at my microphone are not spoken into the meeting,
short pieces of the other side's sentences wait for the rest, and a lone "O" is "OK"."""
import time

import numpy as np
import pytest

from mirrorvoice.asr import Utterance
from mirrorvoice.audio import speech_level
from mirrorvoice.config import Settings
from mirrorvoice.translate import is_fragment, tidy_asr


def test_a_lone_capital_o_is_ok():
    assert tidy_asr("It should be O.") == "It should be OK."
    assert tidy_asr("O, just one second.") == "OK, just one second."
    assert tidy_asr("OO.") == "OK." and tidy_asr("行O") == "行OK"
    for kept in ("Okay, good.", "O'Brien is here.", "On hold.", "OKR review", "Oh.", "so"):
        assert tidy_asr(kept) == kept


@pytest.mark.parametrize("text", ["And.", "So.", "His question is.", "Into a start.", "As got.", "So thing.",
                                  "Stakeholder alignment and.", "Together.", "那。", "因为。"])
def test_short_pieces_of_a_sentence_are_fragments(text):
    assert is_fragment(text)


@pytest.mark.parametrize("text", ["Okay.", "OK.", "Yeah, yeah.", "Thank you.", "No problem.", "Sure.", "Okay, thanks.",
                                  "Good.", "Why first?", "Anything else?", "好的。", "可以。", "谢谢。", "对对对。",
                                  "Let me share my screen.", "The testing is done yesterday.", "我们先讨论预算。", "", "。"])
def test_replies_questions_and_whole_sentences_are_not(text):
    assert not is_fragment(text)


def test_speech_level_is_the_level_of_the_loud_parts():
    loud = np.concatenate([np.zeros(16000, np.float32), np.full(16000, 0.2, np.float32), np.zeros(8000, np.float32)])
    assert speech_level(loud) == pytest.approx(0.2, rel=0.01)  # the silence around it does not pull it down
    assert speech_level(loud * 0.1) == pytest.approx(0.02, rel=0.01)
    assert speech_level(np.zeros(0, np.float32)) == 0.0


class _Rows:
    def __init__(self):
        self.rows = []

    def add(self, *a):
        self.rows.append(a)


class _Listener:
    talking = False

    def speaking(self):
        return self.talking


def _engine(**settings):
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en", **settings), True
    e.s.save = lambda: events.append(("saved", {}))
    e._transcript, e._them = _Rows(), _Listener()
    return e, events


def _utt(text, lang="zh", level=0.1, sec=1.5, guess=0):
    return Utterance(text, lang, np.full(int(sec * 16000), level, np.float32), time.time(), 0.05, guess)


def _jobs(e):
    out = []
    while not e._jobs.empty():
        out.append(e._jobs.get_nowait())
    return [(kind, getattr(item, "text", None) or item.utt.text) for _, _, kind, item in out]


# ---------------------------------------------------------------- my microphone

def test_a_sound_far_quieter_than_my_speech_is_not_translated():
    e, events = _engine()
    for text in ("我们先讨论预算。", "然后再定日期。", "这个功能下周能完成吗？"):
        e._on_me(_utt(text, level=0.1))
    assert len(_jobs(e)) == 3  # (nothing is known about my level yet: everything goes through)
    e._on_me(_utt("所有证只要使用法律最后。", level=0.01))  # 20 dB below: someone nearby, a murmur
    assert _jobs(e) == [] and [d["text"] for k, d in events if k == "notice"][0].startswith("有一段声音比你平时说话轻很多")
    e._on_me(_utt("声音小一点也算。", level=0.04))  # 8 dB below: still me
    assert _jobs(e) == [("me", "声音小一点也算。")]
    e._on_me(_utt("又一段很轻的。", level=0.01))
    assert _jobs(e) == [] and sum(k == "notice" for k, _ in events) == 1  # told once, not every time


def test_a_quiet_guess_is_not_started():
    e, _ = _engine()
    for _i in range(3):
        e._on_me(_utt("我们先讨论预算。", level=0.1))
    _jobs(e)
    e._on_me_guess(_utt("所有证只要使用法律", level=0.01, guess=1))
    assert _jobs(e) == [] and e._guess_job is None


def test_several_quiet_sentences_in_a_row_mean_i_moved_away_and_are_heard_again():
    e, _ = _engine()
    for _i in range(3):
        e._on_me(_utt("我们先讨论预算。", level=0.1))
    _jobs(e)
    for _i in range(e.QUIET_STRIKES - 1):
        e._on_me(_utt("我离麦克风远了。", level=0.012))
    assert _jobs(e) == []
    e._on_me(_utt("我离麦克风远了。", level=0.012))  # the fourth: this is my level now
    e._on_me(_utt("继续说。", level=0.012))
    assert _jobs(e) == [("me", "我离麦克风远了。"), ("me", "继续说。")]


def test_the_check_can_be_switched_off_and_my_level_is_remembered_per_microphone():
    e, events = _engine(quiet_gate=False)
    for _i in range(5):
        e._on_me(_utt("我们先讨论预算。", level=0.1))
    e._on_me(_utt("很轻的一句。", level=0.005))
    assert len(_jobs(e)) == 6  # off: nothing is dropped
    e.s.mic = "外置麦克风"
    e.stop()
    assert e.s.speech_level == pytest.approx(0.1, rel=0.01) and e.s.speech_level_mic == "外置麦克风"
    assert ("saved", {}) in events

    e2, _ = _engine(speech_level=0.1, speech_level_mic="外置麦克风", mic="外置麦克风")
    e2._on_me(_utt("很轻的一句。", level=0.005))  # known from last time: caught from the first sentence on
    assert _jobs(e2) == []
    e3, _ = _engine(speech_level=0.1, speech_level_mic="外置麦克风", mic="内置麦克风")
    e3._on_me(_utt("很轻的一句。", level=0.005))  # another microphone: nothing is assumed
    assert len(_jobs(e3)) == 1


# ---------------------------------------------------------------- the other side

def test_a_short_piece_waits_and_joins_the_next_sentence():
    e, _ = _engine()
    e.HOLD_S = 5.0
    e._on_them(_utt("His question is.", "en"))
    assert _jobs(e) == []  # held back
    e._on_them(_utt("why did we move production to the 30th of November.", "en"))
    assert _jobs(e) == [("them", "His question is why did we move production to the 30th of November.")]
    assert e._held is None


def test_a_short_piece_goes_out_alone_when_nothing_follows_but_waits_while_they_talk():
    e, _ = _engine()
    e.HOLD_S = 0.05
    e._them.talking = True
    e._on_them(_utt("Testing done.", "en"))
    time.sleep(0.5)
    assert _jobs(e) == []  # they are still talking: the rest is on its way
    e._them.talking = False
    deadline = time.monotonic() + 5
    while e._jobs.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert _jobs(e) == [("them", "Testing done.")]


def test_replies_fillers_and_my_own_language_do_not_wait_or_get_joined():
    e, events = _engine()
    e.HOLD_S = 5.0
    e._on_them(_utt("O.", "en"))
    assert _jobs(e) == [("them", "OK.")]  # a reply, and the lone O is OK
    e._on_them(_utt("So for the.", "en"))
    e._on_them(_utt("这个你可能要帮忙协调一下。", "zh"))  # my language: shown as it is, the piece goes out first
    assert _jobs(e) == [("them", "So for the.")] and events[-1][1]["src"] == "这个你可能要帮忙协调一下。"
    e._on_them(_utt("And.", "en"))
    e.stop()
    assert e._held is None and e._held_timer is None  # nothing left behind at stop


def test_terms_written_the_same_on_both_sides_are_to_be_copied_not_translated():
    from mirrorvoice.translate import user_prompt

    p = user_prompt("Danish will unblock Siena.", [("user story", "用户故事"), ("Siena", "Siena"), ("QA", "QA")])
    assert "Use these fixed translations: user story -> 用户故事" in p and "Siena -> Siena" not in p
    assert "copy them exactly as written, do not translate them: Siena, QA" in p
    assert user_prompt("Hello.", []) == "Translate ONLY this sentence: Hello."
