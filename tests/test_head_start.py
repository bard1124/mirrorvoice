"""Lower latency for my speech: the silence a voice model wraps around a sentence is cut, speech that
is running late is spoken faster, and translation starts at the first short pause — spoken only if
the sentence does end there. Fakes only."""
import threading
import time

import numpy as np
import pytest

from mirrorvoice.asr import Decoded, Segmenter, Utterance
from mirrorvoice.audio import trim_silence
from mirrorvoice.config import Settings
from mirrorvoice.translate import Translator

SR = 24000


def _tone(sec, level=0.3):
    return (level * np.sin(np.arange(int(sec * SR)) * 0.05)).astype(np.float32)


def _quiet(sec):
    return np.zeros(int(sec * SR), np.float32)


def test_silence_before_the_first_sound_and_after_the_last_is_cut_pauses_inside_stay():
    chunks = [np.concatenate([_quiet(0.2), _tone(0.2)]),             # 0.2 s of silence first
              _tone(0.4),
              np.concatenate([_tone(0.1), _quiet(0.3)]),             # a pause inside …
              np.concatenate([_quiet(0.1), _tone(0.3)]),             # … 0.4 s long, then more speech
              np.concatenate([_tone(0.1), _quiet(0.3)]), _quiet(0.4)]  # 0.7 s of silence at the end
    out = [c for c, _ in trim_silence((c, SR) for c in chunks)]
    total = sum(len(c) for c in out) / SR
    said = 0.2 + 0.4 + 0.1 + 0.4 + 0.3 + 0.1  # every sound and the pause inside
    assert total == pytest.approx(said + 0.02 + 0.12, abs=0.01)  # + the short lead-in and tail kept
    assert abs(out[0][int(0.03 * SR)]) > 0.001 and len(out[0]) / SR == pytest.approx(0.22, abs=0.01)
    assert len(out[1]) == len(chunks[1])  # sound is passed on at once, never held back
    assert [c for c, _ in trim_silence([(_quiet(0.5), SR)])] == []  # nothing but silence: nothing


# ---------------------------------------------------------------- the segmenter's guesses

class _Vad:
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


class _Rec:
    """Hears more words the more audio it is given."""

    def __init__(self, words="今天先讨论预算然后再定日期"):
        self.words = words

    def decode(self, seg):
        n = min(len(self.words), int(len(seg) / 16000 / 0.25))
        text = self.words[:n] + ("。" if n else "")
        return Decoded(text, "zh", list(text), [0.25 * i for i in range(len(text))])


def _feed(seg, sec, level):
    block = np.full(512, level, np.float32)
    for _ in range(int(sec * 16000 / 512)):
        seg.feed(block)


def _segmenter(rec=None):
    out, guesses, vad = [], [], _Vad()
    seg = Segmenter("me", 0.35, out.append, lang="zh", alt="en", recognizer=rec or _Rec(), vad=vad,
                    on_guess=guesses.append)
    return seg, vad, out, guesses


def test_a_short_pause_is_guessed_to_be_the_end_and_withdrawn_when_the_speaker_goes_on():
    seg, vad, out, guesses = _segmenter()
    vad.speech = True
    _feed(seg, 1.5, 0.2)
    assert guesses == []  # talking: nothing to guess
    _feed(seg, 0.1, 0.0)
    assert guesses == []  # 0.1 s: not a pause yet
    _feed(seg, 0.1, 0.0)
    assert len(guesses) == 1 and guesses[0].guess == 1 and guesses[0].text.startswith("今天先")
    _feed(seg, 0.1, 0.0)
    assert len(guesses) == 1  # one guess per pause
    _feed(seg, 0.5, 0.2)  # the speaker goes on
    assert guesses[1] is None and out == []
    _feed(seg, 0.2, 0.0)  # the next pause: a new guess, with the words said since
    assert guesses[2].guess == 2 and len(guesses[2].text) > len(guesses[0].text)


def test_the_finished_sentence_confirms_the_guess_when_the_words_are_the_same():
    seg, vad, out, guesses = _segmenter(_Rec("今天先讨论"))  # (5 words: the last 0.2 s add none)
    vad.speech = True
    _feed(seg, 1.5, 0.2)
    _feed(seg, 0.2, 0.0)
    assert guesses[-1].guess == 1
    _feed(seg, 0.2, 0.0)  # the pause runs its full length: the VAD closes the sentence
    vad.speech = False
    vad.ready.append((int(0.3 * 16000), int(1.6 * 16000)))
    _feed(seg, 0.05, 0.0)
    assert len(out) == 1 and out[0].guess == 1 and out[0].text == guesses[-1].text
    assert guesses[-1] is not None and seg.guess is None  # confirmed, not withdrawn


def test_a_sentence_that_reads_differently_in_the_end_voids_the_guess():
    seg, vad, out, guesses = _segmenter()
    vad.speech = True
    _feed(seg, 1.0, 0.2)
    _feed(seg, 0.2, 0.0)
    assert guesses[-1].guess == 1
    vad.speech = False
    vad.ready.append((int(0.3 * 16000), int(2.5 * 16000)))  # the VAD's sentence holds more words
    seg.hist = np.concatenate([seg.hist, np.zeros(16000 * 2, np.float32)])
    seg.fed += 16000 * 2
    _feed(seg, 0.05, 0.0)
    assert guesses[-1] is None and out[0].guess == 0


def test_no_guesses_unless_asked_for():
    out, vad = [], _Vad()
    seg = Segmenter("them", 0.35, out.append, lang="en", recognizer=_Rec(), vad=vad)
    vad.speech = True
    _feed(seg, 1.5, 0.2)
    _feed(seg, 0.3, 0.0)
    assert seg.guess is None and out == []


# ---------------------------------------------------------------- the engine

class _Slow:
    no_think = False

    def __init__(self, pieces, delay=0.03):
        self.pieces, self.delay, self.given = pieces, delay, 0

    def chat(self, msgs):
        return "".join(self.pieces)

    def stream_chat(self, msgs):
        for p in self.pieces:
            time.sleep(self.delay)
            self.given += 1
            yield p


class _Voice:
    clone, native_speed, speed = True, False, 1.0

    def __init__(self, sec=0.1):
        self.said, self.sec = [], sec

    def stream(self, text, lang):
        self.said.append(text)
        yield _tone(self.sec), SR


class _Player:
    def __init__(self):
        self.samples = 0

    def put(self, chunk, sr):
        self.samples += len(chunk)


class _Rows:
    def __init__(self):
        self.rows = []

    def add(self, *a):
        self.rows.append(a)


def _engine(voice=None):
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en"), True
    e._voice, e._player, e._transcript = voice or _Voice(), _Player(), _Rows()
    threading.Thread(target=e._voicer, args=(e._voice_q,), daemon=True).start()
    return e, events


def _tr(backend):
    tr = Translator.__new__(Translator)
    tr.glossary, tr.meeting, tr.backend = [], "", backend
    return tr


def _utt(text, guess=0, sec=2.0):
    return Utterance(text, "zh", np.zeros(int(sec * 16000), np.float32), time.time(), 0.05, guess)


def _wait(events, n, kind="me", timeout=5):
    deadline = time.monotonic() + timeout
    while sum(k == kind for k, _ in events) < n and time.monotonic() < deadline:
        time.sleep(0.01)


def _until(cond, timeout=10):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.005)


PIECES = ["We will", " first discuss", " the budget,", " and then", " we will", " set", " the date."]


def _start_guess(e, backend, text="我们先讨论预算，然后再定日期。"):
    e._on_me_guess(_utt(text, guess=1))
    _, _, kind, job = e._jobs.get_nowait()
    assert kind == "guess" and job.state == "open"
    t = threading.Thread(target=e._do_me, args=(_tr(backend), job.utt, job), daemon=True)
    t.start()
    return job, t


def test_a_guess_is_translated_at_once_but_spoken_only_when_the_sentence_ends_there():
    e, events = _engine()
    backend = _Slow(PIECES)
    job, t = _start_guess(e, backend)
    _until(lambda: backend.given >= 3)  # (no fixed sleep: slow machines take longer to get there)
    assert backend.given >= 3 and e._voice.said == []  # translating already; not a word spoken yet
    final = _utt("我们先讨论预算，然后再定日期。", guess=1)
    e._on_me(final)  # the sentence did end there
    assert e._jobs.empty()  # nothing to do twice
    t.join(5)
    _wait(events, 1)
    assert e._voice.said == ["We will first discuss the budget,", "and then we will set the date."]
    me = [d for k, d in events if k == "me"]
    assert len(me) == 1 and me[0]["tgt"] == "We will first discuss the budget, and then we will set the date."
    assert len(e._transcript.rows) == 1 and e._prev["me"][2] == final.t_end  # recorded once, at the real end
    e._voice_q.put(None)


def test_a_guess_i_talk_past_is_dropped_unheard_and_stops_being_translated():
    e, events = _engine()
    backend = _Slow(PIECES * 4)
    job, t = _start_guess(e, backend)
    _until(lambda: backend.given >= 2)
    e._on_me_guess(None)  # I went on
    t.join(5)
    assert not t.is_alive() and backend.given < len(backend.pieces)  # the translation was cut short
    time.sleep(0.1)
    assert e._voice.said == [] and not [k for k, _ in events if k == "me"]
    assert e._transcript.rows == [] and e._prev["me"] is None and e._waiting_s == 0.0
    e._voice_q.put(None)


def test_a_sentence_that_ended_differently_is_translated_afresh():
    e, events = _engine()
    job, t = _start_guess(e, _Slow(PIECES * 4))
    e._on_me(_utt("我们先讨论预算，然后再定日期和地点。"))  # more words than the guess: not a confirmation
    t.join(5)
    assert job.state == "void" and e._voice.said == []
    _, _, kind, utt = e._jobs.get_nowait()
    assert kind == "me" and utt.text.endswith("和地点。")
    e._voice_q.put(None)


def test_a_guess_left_unanswered_is_not_spoken():
    e, events = _engine()
    e.GUESS_WAIT_S = 0.1
    job, t = _start_guess(e, _Slow(["Hello", " there."], delay=0.01))
    t.join(5)
    assert job.state == "void" and e._voice.said == [] and not [k for k, _ in events if k == "me"]
    e._on_me(_utt("我们先讨论预算，然后再定日期。", guess=1))  # the verdict comes too late: a normal job
    assert e._jobs.get_nowait()[2] == "me"
    e._voice_q.put(None)


def test_guesses_are_not_made_into_jobs_for_push_to_talk_or_passthrough():
    e, _ = _engine()
    e.s.mode = "ptt"
    e._on_me_guess(_utt("你好。", guess=1))
    assert e._jobs.empty()
    e.s.mode, e.s.passthrough = "continuous", True
    g = _utt("Hello there.", guess=2)
    g.lang = "en"
    e._on_me_guess(g)
    assert e._jobs.empty()
    e._voice_q.put(None)


def test_speech_that_runs_late_is_spoken_faster_the_later_it_is():
    e, events = _engine(_Voice(sec=1.0))
    assert e._catch_up(0.0) == e._catch_up(e.CATCH_UP_FROM_S) == 1.0
    assert 1.0 < e._catch_up(2.0) < e._catch_up(e.CATCH_UP_FULL_S) == e._catch_up(9.0) == e.CATCH_UP_SPEED
    e._do_me(_tr(_Slow(["On time."], delay=0)), _utt("准时。"))
    _wait(events, 1)
    on_time = e._player.samples
    assert on_time == pytest.approx(SR * 1.0, rel=0.02)  # nothing waiting: natural pace
    e._play_until = time.monotonic() + 30.0  # earlier speech still has a long time to play
    e._do_me(_tr(_Slow(["Running late."], delay=0)), _utt("晚了。"))
    _wait(events, 2)
    assert e._player.samples - on_time == pytest.approx(SR * 1.0 / e.CATCH_UP_SPEED, rel=0.05)
    e._voice_q.put(None)
