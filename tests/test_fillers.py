"""Sentences that are nothing but hesitation are not translated or spoken; answers are."""
import time

import numpy as np
import pytest

from mirrorvoice.asr import Utterance
from mirrorvoice.config import Settings
from mirrorvoice.translate import is_filler


@pytest.mark.parametrize("text", ["嗯。", "嗯嗯。", "呃。", "哦。", "呃，嗯", "哦呃嗯.", "啊", "Um.", "uh, um", "Hmm.", "ugh.",
                                  "Mm-hmm.", "う。", "うう。", "は。", "呃."])
def test_hesitation_alone_is_filler(text):
    assert is_filler(text)


@pytest.mark.parametrize("text", ["好。", "对。", "不是。", "知道。", "行行行行。", "对对对。", "Yeah.", "Okay.", "Oh.", "Yes.",
                                  "Hello.", "Right.", "嗯，因为。", "啊？", "Huh?", "嗯，可以。", "Um, yes.", "2.", "。", "",
                                  "因为。", "并且。", "はい"])
def test_answers_questions_and_words_are_kept(text):
    assert not is_filler(text)


class _Out:
    def __init__(self):
        self.audio = []

    def put(self, chunk, sr):
        self.audio.append(chunk)


class _Player:
    def __init__(self):
        self.outs = [_Out()]


class _Rows:
    def __init__(self):
        self.rows = []

    def add(self, *a):
        self.rows.append(a)


def _engine():
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en", passthrough=True), True
    e._player, e._transcript = _Player(), _Rows()
    return e, events


def _utt(text, lang="zh", guess=0):
    return Utterance(text, lang, np.zeros(16000, np.float32), time.time(), 0.05, guess)


def test_my_hesitation_is_not_queued_shown_or_recorded():
    e, events = _engine()
    e._on_me(_utt("嗯。"))
    e._on_me(_utt("呃，嗯。"))
    assert e._jobs.empty() and events == [] and e._transcript.rows == [] and e._waiting_s == 0.0
    e._on_me(_utt("好。"))  # an answer: translated as before
    assert e._jobs.get_nowait()[2] == "me"


def test_a_guess_that_is_only_hesitation_is_not_started_and_one_that_ends_so_is_dropped():
    e, _ = _engine()
    e._on_me_guess(_utt("嗯。", guess=1))
    assert e._jobs.empty() and e._guess_job is None
    e._on_me_guess(_utt("我们先讨论预算", guess=2))
    job = e._jobs.get_nowait()[3]
    e._on_me(_utt("呃。"))  # what the sentence turned out to be
    assert job.state == "void" and e._jobs.empty()


def test_their_hesitation_gets_no_subtitle_but_words_do():
    e, events = _engine()
    e._on_them(_utt("Um.", "en"))
    e._on_them(_utt("う。", "ja"))
    assert e._jobs.empty() and events == [] and e._transcript.rows == []
    e._on_them(_utt("Yeah.", "en"))
    assert e._jobs.get_nowait()[2] == "them"


def test_my_own_voice_still_passes_through_when_i_speak_their_language():
    e, events = _engine()
    e._on_me(_utt("Um.", "en"))  # passthrough is my real voice: nothing is made up, nothing is held back
    assert len(e._player.outs[0].audio) == 1 and events[0][1]["passthrough"] is True
