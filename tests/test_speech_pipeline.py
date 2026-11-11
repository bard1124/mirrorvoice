"""Speech pipeline: first clause spoken while the translation is still being written, voice on its
own thread (in order, skipped when muted), shorter translations when speech piles up."""
import queue
import threading
import time

import numpy as np

from mirrorvoice.asr import Utterance
from mirrorvoice.config import Settings
from mirrorvoice.translate import Translator, first_clause, system_prompt


def test_first_clause_needs_more_text_after_it_and_enough_words():
    assert first_clause("Today's meeting, we will", "en") == ""  # 2 words: too short to say alone
    assert first_clause("Today's meeting, we will first discuss the budget, and", "en") == \
        "Today's meeting, we will first discuss the budget,"
    assert first_clause("We will first discuss the budget.", "en") == ""  # the end: wait for the rest
    assert first_clause("It is 3.5 percent of the budget, so", "en") == "It is 3.5 percent of the budget,"
    assert first_clause("Sure, here it is:\nToday's meeting, we", "en") == ""  # several lines: unsafe
    assert first_clause("今天的会议，我们先讨论预算，然后", "zh") == "今天的会议，我们先讨论预算，"


class _Streaming:
    no_think = False

    def __init__(self, pieces):
        self.pieces = pieces

    def chat(self, msgs):
        return "".join(self.pieces)

    def stream_chat(self, msgs):
        for p in self.pieces:
            time.sleep(0.01)
            yield p


def _tr(backend):
    tr = Translator.__new__(Translator)
    tr.glossary, tr.meeting, tr.backend = [], "", backend
    return tr


def test_translator_hands_over_the_first_clause_early_and_returns_the_whole_text():
    pieces = ["Today's", " meeting,", " we will", " first discuss", " the budget,", " and then", " the date."]
    got = []
    out = _tr(_Streaming(pieces)).translate("…", "zh", "en", on_clause=got.append)
    assert got == ["Today's meeting, we will first discuss the budget,"]
    assert out == "Today's meeting, we will first discuss the budget, and then the date."


def test_length_budget_reaches_the_prompt():
    assert "at most 7 words" in system_prompt("zh", "en", False, concise=7)
    assert "at most 12 characters" in system_prompt("en", "zh", False, concise=12)
    assert "at most" not in system_prompt("zh", "en", False)


class _Voice:
    clone, native_speed, speed = True, False, 1.0

    def __init__(self):
        self.said = []

    def stream(self, text, lang):
        self.said.append(text)
        yield np.zeros(2400, np.float32), 24000  # 0.1 s


class _Player:
    def __init__(self):
        self.chunks = 0

    def put(self, chunk, sr):
        self.chunks += 1


class _Rows:
    def add(self, *a):
        pass


def _engine():
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en"), True
    e._voice, e._player, e._transcript = _Voice(), _Player(), _Rows()
    threading.Thread(target=e._voicer, args=(e._voice_q,), daemon=True).start()
    return e, events


def _wait(events, n, kind="me", timeout=5):
    deadline = time.monotonic() + timeout
    while sum(k == kind for k, _ in events) < n and time.monotonic() < deadline:
        time.sleep(0.01)


def _utt(text, sec=2.0):
    return Utterance(text, "zh", np.zeros(int(sec * 16000), np.float32), time.time(), 0.1)


def test_voice_thread_speaks_first_clause_then_the_rest_in_order():
    e, events = _engine()
    pieces = ["Today's", " meeting,", " we will", " first discuss", " the budget,", " and then", " the date."]
    e._do_me(_tr(_Streaming(pieces)), _utt("今天先讨论预算，然后再定日期。"))
    e._do_me(_tr(_Streaming(["Can this", " be done", " by Wednesday?"])), _utt("周三能完成吗？"))
    _wait(events, 2)
    assert e._voice.said == ["Today's meeting, we will first discuss the budget,", "and then the date.",
                             "Can this be done by Wednesday?"]
    me = [d for k, d in events if k == "me"]
    assert [d["tgt"] for d in me] == ["Today's meeting, we will first discuss the budget, and then the date.",
                                      "Can this be done by Wednesday?"]
    assert all(d["voice_s"] is not None and d["translate_s"] >= 0 for d in me)
    e._voice_q.put(None)


def test_muted_sentences_are_not_spoken():
    e, events = _engine()
    e.muted = True
    e._do_me(_tr(_Streaming(["Hello there, how are", " you today?"])), _utt("你好"))
    _wait(events, 1)
    assert e._voice.said == [] and events[-1][0] == "me"
    e._voice_q.put(None)


def test_backlog_asks_for_a_budget_and_speeds_up(monkeypatch):
    e, events = _engine()
    e._play_until = time.monotonic() + 8  # 8 s of my speech still to be heard
    seen = {}

    class _Budget(_Streaming):
        def stream_chat(self, msgs):
            seen["prompt"] = msgs[0]["content"]
            yield from super().stream_chat(msgs)
    e._do_me(_tr(_Budget(["Short version", " of it."])), _utt("一段说了三秒钟的话。", sec=3.0))
    _wait(events, 1)
    assert "at most 7 words" in seen["prompt"]  # ~2.4 words per second I spoke
    assert any(k == "notice" and "积压" in d["text"] for k, d in events)
    e._play_until = 0.0
    e._pending_s = 0.0
    assert e._update_compact() is False  # caught up: back to normal
    e._voice_q.put(None)


class _Rambling(_Streaming):
    """The model talks about the task: generation must stop early and nothing gets spoken."""

    def __init__(self, pieces, retry):
        super().__init__(pieces)
        self.retry, self.pulled, self.max_tokens = retry, 0, 200

    def chat(self, msgs):
        return self.retry

    def stream_chat(self, msgs):
        for p in self.pieces:
            self.pulled += 1
            yield p


def test_talking_about_the_task_is_never_spoken_and_stops_early():
    pieces = ["Okay,", " let's", " take", " this", " sentence:", " the", " user", " specified", " that"] + [" x"] * 50
    b = _Rambling(pieces, "Let's leave it here, get some rest.")
    got = []
    src = "先这样吧，你们休息吧，等我下午优化一波，我们下午再试。"
    out = _tr(b).translate(src, "zh", "en", on_clause=got.append)
    assert got == [] and b.pulled < 10  # no "Okay, let's take this sentence:" sent to the meeting
    assert out == "Let's leave it here, get some rest."
    assert b.max_tokens == 32 + 2 * len(src)  # a runaway answer is capped by the source length
    b = _Rambling(["Also, the user specified numbers."], "The user specified numbers.")
    assert _tr(b).translate(src, "zh", "en") == ""  # still off task after the retry: dropped


def test_task_words_are_fine_when_the_source_says_them():
    from mirrorvoice.translate import off_task

    assert not off_task("Please translate this sentence for the user.", "请把这句话翻译给用户。", "en")
    assert off_task("Okay, let's take this sentence:", "你能听到我说话吗？", "en")
    assert not off_task("Okay, let's get started.", "好，我们开始吧。", "en")


def test_online_translation_does_not_wait_for_speech_synthesis(home):
    from mirrorvoice.online_llm import OnlineChat

    assert OnlineChat("deepseek", "deepseek-chat", "k", "https://api.deepseek.com").uses_gpu is False
    assert OnlineChat("custom", "qwen3", "", "http://127.0.0.1:8765/v1").uses_gpu is True  # same machine


def test_their_translation_shows_while_it_is_written(home):
    from mirrorvoice.config import Settings
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s = Settings()
    e.LIVE_EVERY_S = 0  # every update, for the test
    show = e._live_them(type("U", (), {"text": "Let's discuss the budget first."})())
    for t in ("我们", "我们先讨论", "我们先讨论预算。"):
        show(t)
    live = [d for k, d in events if k == "partial"]
    assert [d["tgt"] for d in live] == ["我们先讨论 …", "我们先讨论预算。 …"]  # too short at first
    assert all(d["who"] == "them" and d["src"] == "Let's discuss the budget first." for d in live)
