"""Fixes from a meeting where both sides spoke through the app (2026-11-16): a pause no longer
makes a sentence of every piece — mine waits to see whether I go on, theirs is shown as not final
and joined by what follows — a subtitle with English or a note of the model's left in it is done
again, and a sound that is no speech is not sent into the meeting as my own voice."""
import time

import numpy as np
import pytest

from mirrorvoice.asr import Utterance
from mirrorvoice.config import Settings
from mirrorvoice.translate import Translator, goes_on, last_line, left_untranslated, off_task, own_voice


# ---------------------------------------------------------------- what the words say

@pytest.mark.parametrize("text", ["然后那个。", "你看那个。", "你听不到声音，那你。", "Because.", "We moved it and."])
def test_a_piece_that_stops_on_a_word_asking_for_more_is_open(text):
    assert goes_on(text) == "open"


@pytest.mark.parametrize("text", ["你扬声器。", "那个麦风.", "取消。", "I gave up."])
def test_a_few_words_that_are_no_reply_may_be_half_a_sentence(text):
    assert goes_on(text) == "short"


@pytest.mark.parametrize("text", ["可以。", "对。", "好的。", "没有。", "Okay.", "Thank you.", "什么助手？", "16。",
                                  "就是外置耳机。", "我听到的是你说的中文。", "I think it works now.",
                                  "In fact, it should not be called a word."])
def test_replies_questions_and_sentences_are_whole(text):
    assert goes_on(text) == "" and goes_on(text, loose=True) == ""


def test_the_other_sides_pieces_are_taken_for_open_more_readily_than_mine():
    for text in ("And the delivery status is for risk, so.", "Wearing headphones after I put them on, I just.",
                 "He that."):
        assert goes_on(text, loose=True) == "open" and goes_on(text) != "open"


@pytest.mark.parametrize("out, left", [("我在找 something random。", 2), ("不 quite。", 1), ("戴上耳机后，我 just", 1),
                                       ("在评论下。yeah，please check.", 3)])
def test_one_everyday_english_word_in_a_chinese_subtitle_is_left_untranslated(out, left):
    assert left_untranslated(out, "zh") == left


@pytest.mark.parametrize("out", ["看看这个bug。", "OneTake 的 sourcing 进度", "这个 handshake 还没做。", "好的"])
def test_a_term_kept_on_purpose_is_not(out):
    assert left_untranslated(out, "zh") == 0


def test_a_note_of_the_models_own_is_cut_off_and_a_remark_on_the_recognition_is_off_task():
    assert last_line("戴上耳机后，我 just（注：此处为语音识别错误，应为“就”）") == "戴上耳机后，我 just"
    assert last_line('"好的 (Note: informal)"') == "好的"
    assert last_line("戴上耳机后，我 just（此处可能为填词）在这一个里。") == "戴上耳机后，我 just在这一个里。"
    assert last_line("我们（团队）会处理。") == "我们（团队）会处理。"  # brackets that are part of the sentence stay
    assert off_task("这里可能是语音识别错误。", "I just.", "zh")
    assert not off_task("语音识别还不错。", "The speech recognition is fine.", "zh")


class _Twice:
    """A backend whose first answer leaves English in, and whose second does not."""
    no_think = False

    def __init__(self, *answers):
        self.answers, self.asked = list(answers), []

    def chat(self, msgs):
        self.asked.append(msgs[-1]["content"])
        return self.answers[min(len(self.asked), len(self.answers)) - 1]


def test_a_subtitle_with_an_everyday_english_word_in_it_is_translated_once_more():
    backend = _Twice("我在找 something random。", "我在随便找个东西。")
    tr = Translator(backend)
    tr.glossary = []
    assert tr.translate("I'm looking for something random.", "en", "zh", subtitle=True) == "我在随便找个东西。"
    assert len(backend.asked) == 2 and "translate every word" in backend.asked[1]
    kept = _Twice("看看这个bug。")
    tr = Translator(kept)
    tr.glossary = []
    assert tr.translate("Well look at this bug.", "en", "zh", subtitle=True) == "看看这个bug。" and len(kept.asked) == 1


@pytest.mark.parametrize("text, sec, what", [
    ("H.", 0.71, "noise"), ("S more dear.", 0.87, "noise"), (".", 0.68, "noise"),
    ("え.", 0.71, "mine"), ("对.", 0.71, "mine"),
    ("Yeah.", 0.74, "pass"), ("Um.", 0.7, "pass"), ("I don't know.", 1.1, "pass"), ("Let me share my screen.", 1.6, "pass"),
])
def test_what_was_heard_as_their_language_at_my_microphone(text, sec, what):
    assert own_voice(text, "en", sec) == what


def test_the_same_when_the_other_side_speaks_chinese():
    assert own_voice("好的。", "zh", 0.6) == "pass" and own_voice("我来说一下。", "zh", 1.2) == "pass"
    assert own_voice("Hello there.", "zh", 1.2) == "mine" and own_voice("这。", "zh", 0.6) == "noise"


# ---------------------------------------------------------------- the engine

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
        self.audio.append(audio)


class _Player:
    def __init__(self):
        self.outs = [_Out()]


def _engine(**settings):
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en", quiet_gate=False, **settings), True
    e._transcript, e._them, e._me, e._player = _Rows(), _Listener(), _Listener(), _Player()
    e.TELL_AFTER = 99  # (these pieces come one right after the other: the tests of that say so themselves)
    return e, events


def _utt(text, lang="zh", sec=1.5, guess=0):
    return Utterance(text, lang, np.full(int(sec * 16000), 0.1, np.float32), time.time(), 0.05, guess)


def _jobs(e):
    out = []
    while not e._jobs.empty():
        out.append(e._jobs.get_nowait())
    return [(kind, getattr(item, "text", None) or item.utt.text) for _, _, kind, item in out]


def _until(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.005)
    return cond()


class _Says:
    """A translator that answers with what it is told to, and counts."""
    glossary: list = []
    backend = None

    def __init__(self, out):
        self.out, self.asked = out, []

    def translate(self, text, src, tgt, **kw):
        self.asked.append(text)
        return self.out


# ---- my own speech

def test_a_sentence_or_a_reply_of_mine_does_not_wait():
    e, _ = _engine()
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._on_me(_utt("我听到的是你说的中文。"))
    e._on_me(_utt("可以。"))
    assert _jobs(e) == [("me", "我听到的是你说的中文。"), ("me", "可以。")] and e._held_me is None


def test_a_short_piece_of_mine_is_spoken_when_i_do_not_go_on():
    e, _ = _engine()
    e.MORE_S = {"open": 0.05, "short": 0.05}
    e._on_me(_utt("取消。"))
    assert _until(lambda: not e._jobs.empty())
    assert _jobs(e) == [("me", "取消。")] and e._held_me is None and e._held_me_timer is None


def test_a_piece_of_mine_waits_while_i_go_on_and_is_translated_with_the_rest():
    e, _ = _engine()
    e.MORE_S = {"open": 0.05, "short": 0.05}
    e._me.talking = True  # I am talking again when the wait is over
    e._on_me(_utt("你扬声器。"))
    assert _until(lambda: e._held_me_more) and e._jobs.empty()
    e._me.talking = False
    e._on_me(_utt("就是外置耳机。"))
    assert _jobs(e) == [("me", "你扬声器，就是外置耳机。")] and e._held_me is None
    e._me.talking = True
    e._on_me(_utt("然后那个。"))  # stops on a word that asks for more: no comma where the sentence simply goes on
    assert _until(lambda: e._held_me_more)
    e._on_me(_utt("会议里面的扬声器要选对。"))
    assert _jobs(e) == [("me", "然后那个会议里面的扬声器要选对。")]


def test_the_rest_that_never_comes_lets_the_piece_go_alone():
    e, _ = _engine(pause=0.05)
    e.MORE_S, e.HOLD_ME_POLL_S = {"open": 0.05, "short": 0.05}, 0.02
    e._me.talking = True
    e._on_me(_utt("然后那个。"))
    assert _until(lambda: e._held_me_more) and e._jobs.empty()
    e._me.talking = False  # a sound that was no sentence: nothing arrives
    assert _until(lambda: not e._jobs.empty())
    assert _jobs(e) == [("me", "然后那个。")] and e._held_me is None


def test_it_does_not_wait_for_a_long_sentence_of_mine_to_end():
    e, _ = _engine()
    e.MORE_S, e.HOLD_ME_POLL_S, e.HOLD_ME_MAX_S = {"open": 0.05, "short": 0.05}, 0.02, 0.3
    e._me.talking = True  # … and I keep talking
    e._on_me(_utt("你扬声器。"))
    assert _until(lambda: not e._jobs.empty())
    assert _jobs(e) == [("me", "你扬声器。")]


def test_the_head_start_on_a_piece_stands_when_i_stop_there_and_is_void_when_i_go_on():
    e, _ = _engine()
    e.MORE_S = {"open": 0.05, "short": 0.05}
    e._on_me_guess(_utt("取消。", guess=1))
    job = e._jobs.get_nowait()[3]
    e._on_me(_utt("取消。", guess=1))
    assert job.state == "open"  # not decided while it waits
    assert _until(lambda: job.state == "go") and e._jobs.empty()  # the work done on it is used: nothing twice

    e._me.talking = True
    e._on_me_guess(_utt("你扬声器。", guess=2))
    first = e._jobs.get_nowait()[3]
    e._on_me(_utt("你扬声器。", guess=2))
    assert _until(lambda: first.state == "void")  # I went on
    e._on_me_guess(_utt("就是外置耳机。", guess=3))  # the next pause: both pieces, as one sentence
    both = e._jobs.get_nowait()[3]
    assert both.utt.text == "你扬声器，就是外置耳机。" and both.state == "open"
    e._me.talking = False
    e._on_me(_utt("就是外置耳机。", guess=3))
    assert both.state == "go" and e._jobs.empty() and e._held_me is None


def test_push_to_talk_ends_a_sentence_where_i_let_go():
    e, _ = _engine(mode="ptt")
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._on_me(_utt("然后那个。"))
    assert _jobs(e) == [("me", "然后那个。")]


def test_nothing_of_mine_is_left_waiting_at_stop():
    e, _ = _engine()
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._on_me(_utt("然后那个。"))
    assert e._held_me is not None
    e.stop()
    assert e._held_me is None and e._held_me_timer is None


# ---- my own voice in their language

def test_a_sound_that_is_no_speech_is_not_sent_into_the_meeting_as_my_voice():
    e, events = _engine(passthrough=True)
    for text, sec in (("H.", 0.71), ("S more dear.", 0.87), (".", 0.68)):
        e._on_me(_utt(text, "en", sec))
    assert e._player.outs[0].audio == [] and events == [] and e._transcript.rows == [] and e._jobs.empty()
    e._on_me(_utt("Let me share my screen.", "en", 1.6))
    assert len(e._player.outs[0].audio) == 1 and events[-1][1]["passthrough"] is True
    e._on_me(_utt("Yeah.", "en", 0.74))  # a reply is mine however short
    assert len(e._player.outs[0].audio) == 2


def test_my_own_language_under_the_wrong_label_is_translated_as_usual():
    e, _ = _engine(passthrough=True)
    e._on_me(_utt("对.", "en", 0.71))
    assert _jobs(e) == [("me", "对.")] and e._player.outs[0].audio == []


def test_a_piece_of_mine_that_waits_is_heard_before_what_i_say_in_their_language():
    e, _ = _engine(passthrough=True)
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._on_me(_utt("然后那个。"))
    e._on_me(_utt("Let me share my screen.", "en", 1.6))
    assert _jobs(e) == [("me", "然后那个。")] and len(e._player.outs[0].audio) == 1


# ---- the other side

def test_their_piece_is_shown_as_not_final_and_joined_by_what_follows():
    e, events = _engine()
    e.HOLD_S, e.JOIN_S = 0.02, 60.0
    piece = _utt("But, I don't.", "en")
    e._on_them(piece)
    assert _until(lambda: not e._jobs.empty())
    assert _jobs(e) == [("them_part", "But, I don't.")]
    tr = _Says("但是，我不")
    e._do_them_part(tr, piece)
    assert events[-1] == ("partial", {"who": "them", "src": "But, I don't.", "tgt": "但是，我不 …", "live": True})
    assert e._transcript.rows == [] and e._prev["them"] is None  # not a sentence yet
    e._on_them(_utt("Understand that what you are saying.", "en"))
    assert _jobs(e) == [("them", "But, I don't Understand that what you are saying.")]
    assert e._held is None and e._held_out is None and e._held_timer is None


def test_their_piece_stands_with_the_translation_it_has_when_nothing_joins_it():
    e, events = _engine()
    e.HOLD_S, e.JOIN_S = 0.02, 60.0
    piece = _utt("I gave up.", "en")
    e._on_them(piece)
    assert _until(lambda: not e._jobs.empty())
    assert _jobs(e) == [("them_part", "I gave up.")]
    tr = _Says("我放弃了。")
    e._do_them_part(tr, piece)
    shown = e._held_out[1]["latency"]
    e._settle_held(piece)  # (what the timer does JOIN_S after the piece was said)
    final = [d for k, d in events if k == "them"]
    assert len(final) == 1 and final[0]["src"] == "I gave up." and final[0]["tgt"] == "我放弃了。"
    assert final[0]["latency"] == shown  # how long it took to be on screen, not to be settled
    assert e._transcript.rows == [("them", "I gave up.", "我放弃了。")] and tr.asked == ["I gave up."]
    assert e._jobs.empty() and e._held is None and e._held_timer is None  # nothing is translated twice


def test_with_online_recognition_which_cannot_tell_that_i_go_on_nothing_waits():
    e, _ = _engine()
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._me.tells_speaking = False
    e._on_me(_utt("然后那个。"))
    assert _jobs(e) == [("me", "然后那个。")]


def test_their_piece_not_translated_in_time_goes_the_ordinary_way():
    e, _ = _engine()
    e.HOLD_S, e.JOIN_S = 0.02, 0.1
    e._on_them(_utt("I gave up.", "en"))
    assert _until(lambda: e._jobs.qsize() == 2)
    assert _jobs(e) == [("them_part", "I gave up."), ("them", "I gave up.")]
    late, events = _Says("我放弃了。"), []
    e.emit = lambda kind, **d: events.append(kind)
    e._do_them_part(late, _utt("I gave up.", "en"))  # its turn comes after it was settled: nothing to do
    assert late.asked == [] and events == []


def test_while_they_talk_their_piece_waits_for_the_sentence_they_are_saying():
    e, events = _engine()
    e.HOLD_S, e.HOLD_MAX_S, e.JOIN_S = 0.02, 0.0, 0.05
    piece = _utt("Because.", "en")
    e._on_them(piece)
    assert _until(lambda: not e._jobs.empty())
    _jobs(e)
    e._do_them_part(_Says("因为"), piece)
    e._them.talking = True
    time.sleep(0.4)  # well past JOIN_S
    assert e._held is piece and not [k for k, _ in events if k == "them"]
    e._them.talking = False
    e._on_them(_utt("he pressed the button again.", "en"))
    assert _jobs(e) == [("them", "Because he pressed the button again.")]


def test_a_sentence_of_theirs_that_stops_on_an_open_word_waits_for_the_rest():
    e, _ = _engine()
    e.HOLD_S = 60.0
    e._on_them(_utt("And the delivery status is for risk, so.", "en"))
    assert e._jobs.empty() and e._held is not None
    e._on_them(_utt("If the user is overrun on that day.", "en"))
    assert _jobs(e) == [("them", "And the delivery status is for risk, so If the user is overrun on that day.")]


def test_what_they_say_next_is_previewed_together_with_the_piece_that_waits():
    e, events = _engine()
    e.HOLD_S = 60.0
    e._on_them(_utt("His question is.", "en"))
    e._on_them_guess(_utt("why did we move production", "en"))
    assert events[-1] == ("partial", {"who": "them", "src": "His question is why did we move production", "tgt": ""})
