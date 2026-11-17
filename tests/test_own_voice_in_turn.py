"""My own voice in the other side's language is heard in its turn. 2026-11-17, the other side explained a bug: "…用户 /
Managing project, job and local recruiters, recruit leader / 他就说这个…都展示的是空的". The middle
piece was taken for English and went to the meeting at once, while the sentences before it were
4–8 s behind — it landed inside a spoken sentence, and I received "He said, This the this this was
fixed managing project." Also: with Qwen3-ASR for the subtitles nothing was shown ahead at all."""
import queue
import time

import numpy as np
import pytest

from mirrorvoice.asr import Utterance
from mirrorvoice.config import Settings


class _Rows:
    def __init__(self):
        self.rows = []

    def add(self, *a):
        self.rows.append(a)


class _Listener:
    talking = False

    def speaking(self):
        return self.talking


class _Out:
    def __init__(self):
        self.audio = []

    def put(self, audio, sr):
        self.audio.append((audio, sr))


class _Player:
    def __init__(self):
        self.outs = [_Out(), _Out()]  # the meeting, my headphones


def _engine(**settings):
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running, e._ready = Settings(my_lang="zh", their_lang="en", quiet_gate=False, passthrough=True, **settings), True, True
    e._transcript, e._them, e._me, e._player = _Rows(), _Listener(), _Listener(), _Player()
    e.TELL_AFTER = 99
    return e, events


T0 = 1_800_000_000.0


def _utt(text, lang="zh", sec=1.5, at=None):
    """`at`: seconds after T0 at which the piece was cut (it began `sec` before)."""
    t_end = time.time() if at is None else T0 + at
    return Utterance(text, lang, np.full(int(sec * 16000), 0.1, np.float32), t_end, 0.05)


def _jobs(e):
    out = []
    while not e._jobs.empty():
        out.append(e._jobs.get_nowait())
    return [(kind, item.text) for _, _, kind, item in out]


# ---- in its turn

def test_my_voice_in_their_language_waits_its_turn_behind_what_i_said_before():
    e, events = _engine()
    e._on_me(_utt("我先把这个问题说完。", at=0.0))
    said = _utt("Let me share my screen.", "en", 1.6, at=10.0)
    e._on_me(said)
    assert e._player.outs[0].audio == [] and events == []  # not at once, past the sentence before it
    first, second = (e._jobs.get_nowait() for _ in range(2))
    assert (first[2], second[2]) == ("me", "raw") and first[:2] < second[:2]  # the worker takes them in this order
    e._do_raw(second[3])
    assert e._voice_q.get_nowait() == ("raw", said) and e._waiting_s == len(first[3].audio) / 16000


def test_it_is_played_after_the_speech_that_is_still_coming_out_and_counted_as_such():
    e, events = _engine()
    said = _utt("Let me share my screen.", "en", 1.6)
    e._play_until = until = time.monotonic() + 60.0  # a minute of my translated speech is still to be heard
    e._play_raw(said)
    (audio, sr), = e._player.outs[0].audio
    assert sr == 16000 and audio is said.audio and e._player.outs[1].audio == []  # not into my own ears
    # what follows waits for it in turn: the end of what is queued moves on by its length. (Compared
    # with the value set above, never with the clock again: on Windows two readings of the clock
    # can be the same, and "<= 6.6 s from now" failed by a rounding error.)
    assert e._play_until == pytest.approx(until + 1.6)
    kind, d = events[-1]
    assert kind == "me" and d["passthrough"] is True and d["src"] == d["tgt"] == said.text
    assert 59.0 < d["latency"] < 62.0  # (the wait is in it; a second either way is the clocks' business)
    assert e._transcript.rows == [("me", "Let me share my screen.", "（原声透传）")]


def test_the_voice_thread_plays_it_between_the_sentences():
    e, _ = _engine()
    order = []
    e._speak = lambda job: order.append(("speak", job))
    e._player.outs[0].put = lambda audio, sr: order.append(("raw", len(audio)))
    vq = queue.Queue()
    for item in (("speak", "first"), ("raw", _utt("Let me share my screen.", "en", 1.0)), ("speak", "second"), None):
        vq.put(item)
    e._voice_loop(vq)
    assert order == [("speak", "first"), ("raw", 16000), ("speak", "second")]


def test_nothing_of_it_is_heard_while_muted_or_after_stop():
    e, events = _engine()
    said = _utt("Let me share my screen.", "en", 1.6)
    e.muted = True
    e._do_raw(said)
    e._play_raw(said)
    e.muted, e.running = False, False
    e._do_raw(said)
    assert e._voice_q.empty() and e._player.outs[0].audio == [] and events == []


# ---- names of theirs in a sentence of mine

def test_names_of_theirs_in_the_middle_of_my_sentence_are_my_voice_in_their_place():
    """(Translating that piece with the sentence was tried and was worse: see Engine._on_me.)"""
    e, _ = _engine()
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._on_me(_utt("记录的有问题。", sec=1.6, at=10.0))
    e._on_me(_utt("用户。", sec=0.9, at=11.2))  # short: it waits to see whether I go on
    assert e._held_me is not None
    e._on_me(_utt("Managing project, job and local recruiters, recruit leader.", "en", 4.0, at=15.5))
    e._on_me(_utt("他就说这个已经修好了，但是后面那些都展示的是空的。", sec=4.0, at=19.8))
    assert _jobs(e) == [("me", "记录的有问题。"), ("me", "用户。"),  # (what waited goes first: it was said first)
                        ("raw", "Managing project, job and local recruiters, recruit leader."),
                        ("me", "他就说这个已经修好了，但是后面那些都展示的是空的。")]
    assert e._player.outs[0].audio == []  # nothing went ahead of its turn


# ---- previews when a second recogniser writes the final text

def test_guesses_reach_the_subtitles_with_a_second_recogniser_and_never_my_voice(monkeypatch):
    from mirrorvoice import asr

    made = []

    class Seg:
        count = 0

        def __init__(self, *a, **k):
            made.append(k)

    class Cap:
        label = "line"

        def __init__(self, *a):
            pass

    monkeypatch.setattr(asr, "Segmenter", Seg)
    monkeypatch.setattr(asr, "Capture", Cap)
    guess, words = (lambda utt: None), object()
    asr.Listener("them", "line", "en", 0.7, lambda u: None, words=words, on_guess=guess, guess_with_words=True)
    asr.Listener("me", "mic", "zh", 0.35, lambda u: None, words=words, on_guess=guess)
    asr.Listener("me", "mic", "zh", 0.35, lambda u: None, on_guess=guess)
    assert [k["on_guess"] for k in made] == [guess, None, guess]


def test_the_subtitle_listener_asks_for_them(monkeypatch):
    from mirrorvoice import engine

    seen = {}

    class Fake:
        def __init__(self, *a, **k):
            seen.update(k)

        def start(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(engine, "Listener", Fake)
    e, _ = _engine()
    e._words, e._qwen_sides = object(), {"them"}
    e._start_them("BlackHole 16ch")
    assert seen["guess_with_words"] is True and seen["words"] is e._words and seen["on_guess"] == e._on_them_guess
