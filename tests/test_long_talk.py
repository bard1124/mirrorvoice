"""Explaining something at length: one piece after another with hardly a pause (the meeting of
2026-11-16, 13:22 — 45 s about a bug arrived as ten sentences that had nothing to do with each
other). My side waits a little longer for the next piece; the other side's pieces are translated
once more as a whole when they stop."""
import time

import numpy as np
import pytest

from mirrorvoice.asr import Utterance
from mirrorvoice.config import Settings
from mirrorvoice.transcript import Transcript
from mirrorvoice.translate import Translator, own_voice, user_prompt, without_repeat


# ---------------------------------------------------------------- words

@pytest.mark.parametrize("first, rest, left", [
    ("What.", "What was it two days ago.", ""),
    ("And then we, we.", "We need to check it.", "And then we, "),
    ("然后那个。", "那个你打开。", "然后"),
    ("你打开。", "开始吧。", "你打开。"),          # one letter of Chinese is no repeat
    ("I think so.", "What do you think.", "I think so."),
])
def test_what_a_piece_begins_with_again_is_said_once(first, rest, left):
    assert without_repeat(first, rest) == left


def test_pieces_are_joined_without_the_words_said_twice():
    from mirrorvoice.engine import _join

    assert _join(_utt("What.", "en"), _utt("What was it two days ago.", "en")).text == "What was it two days ago."
    assert _join(_utt("然后那个。"), _utt("那个你打开。")).text == "然后那个你打开。"
    assert _join(_utt("But, I don't.", "en"), _utt("Understand it.", "en")).text == "But, I don't Understand it."


@pytest.mark.parametrize("text, sec, what", [
    ("因我.", 0.90, "noise"),          # two letters of mine under their label: was spoken as "Because I."
    ("O, youll外.", 1.38, "noise"),    # a few of each: went into the meeting as my voice
    ("对.", 0.71, "mine"),             # a reply is a reply
    ("这个bug是什么意思.", 1.6, "mine"),   # my sentence with a word of theirs in it
    ("我说完了.", 0.8, "mine"),
    ("Let me share my screen.", 1.6, "pass"),
])
def test_a_sound_the_recogniser_could_not_place_is_no_sentence(text, sec, what):
    assert own_voice(text, "en", sec) == what


def test_the_passage_prompt_says_that_a_full_stop_may_be_a_pause():
    p = user_prompt("And this bug is the user. Who applied for it.", [], passage=True)
    assert "pieces" in p and "one coherent passage" in p and "ONLY this sentence" not in p


def test_a_passage_gets_room_for_more_than_a_sentence():
    class Backend:
        max_tokens = 0

        def chat(self, msgs):
            self.msgs = msgs
            return "这个 bug 是申请了它的那个用户。"

    b = Backend()
    text = "And this bug is the user. Who applied for it."
    out = Translator(b).translate(text, "en", "zh", subtitle=True, passage=True)
    alone = Backend()
    Translator(alone).translate(text, "en", "zh", subtitle=True)
    assert out and "one coherent passage" in b.msgs[-1]["content"] and b.max_tokens == 2 * alone.max_tokens


def test_the_record_tells_a_passage_from_the_pieces():
    t = Transcript("zh", "en")
    t.add("them", "And this bug.", "而且这个 bug")
    t.add("them_run", "And this bug. Is the user.", "这个 bug 出在用户身上。")
    rows = t.csv.read_text(encoding="utf-8-sig").splitlines()
    assert rows[1].split(",")[1] == "对方" and rows[2].split(",")[1] == "对方（整段）"


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


class _Says:
    glossary: list = []
    backend = None

    def __init__(self, out):
        self.out, self.asked, self.kw = out, [], []

    def translate(self, text, src, tgt, **kw):
        self.asked.append(text)
        self.kw.append(kw)
        if kw.get("on_text"):
            kw["on_text"](self.out[:2])
        return self.out


def _engine(**settings):
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en", quiet_gate=False, **settings), True
    e._transcript, e._them, e._me = _Rows(), _Listener(), _Listener()
    return e, events


def _utt(text, lang="zh", sec=1.5, at=None):
    return Utterance(text, lang, np.full(int(sec * 16000), 0.1, np.float32), time.time() if at is None else at, 0.05)


def _jobs(e):
    out = []
    while not e._jobs.empty():
        out.append(e._jobs.get_nowait())
    return [(kind, item.text if isinstance(item, Utterance) else item) for _, _, kind, item in out]


def _until(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.005)
    return cond()


# ---- my own speech

def test_pieces_of_mine_in_a_row_wait_for_the_next_and_go_out_together():
    e, _ = _engine()
    e.TELL_S = 60.0
    for text in ("有个用户申请了这个岗位。", "但是他所在的分组已经满了。"):  # nothing says yet that I am explaining
        e._on_me(_utt(text))
    assert [k for k, _ in _jobs(e)] == ["me", "me"] and e._held_me is None
    e._on_me(_utt("按现在的逻辑处理。"))  # the third in a row: it waits for what follows
    assert e._jobs.empty() and e._held_me.text == "按现在的逻辑处理。"
    e._on_me(_utt("他会被系统直接拒绝。"))
    assert e._jobs.empty() and e._held_me.text == "按现在的逻辑处理，他会被系统直接拒绝。"


def test_what_waits_is_spoken_when_i_stop_and_kept_when_i_go_on():
    e, _ = _engine()
    e.TELL_AFTER, e.TELL_S, e.HOLD_ME_POLL_S = 0, 0.05, 0.02
    e._on_me(_utt("按现在的逻辑处理。"))
    assert _until(lambda: not e._jobs.empty())  # I stopped: a sentence
    assert _jobs(e) == [("me", "按现在的逻辑处理。")]
    e._me.talking = True
    e._on_me(_utt("他会被系统直接拒绝。"))
    assert _until(lambda: e._held_me_more) and e._jobs.empty()  # I am talking again: it stays for the rest


def test_a_long_sentence_of_mine_is_not_kept_for_more():
    e, _ = _engine()
    e.TELL_AFTER, e.TELL_S, e.TELL_MAX_S = 0, 60.0, 6.0
    e._on_me(_utt("按现在的逻辑处理。", sec=3.2))
    e._on_me(_utt("他会被系统直接拒绝。", sec=3.2))  # 6.4 s together: enough for one sentence
    assert _jobs(e) == [("me", "按现在的逻辑处理，他会被系统直接拒绝。")] and e._held_me is None


def test_sentences_with_a_pause_between_them_do_not_wait():
    e, _ = _engine()
    e.TELL_S = 60.0
    t = time.time()
    for i, text in enumerate(("我们先看第一个问题。", "接着看第二个问题。", "最后看第三个问题。", "今天就到这里结束。")):
        e._on_me(_utt(text, at=t + 4.0 * i))  # each begins 2.5 s after the one before was cut
    assert len(_jobs(e)) == 4 and e._held_me is None and e._me_run == 0


def test_push_to_talk_and_online_recognition_never_wait_for_more():
    e, _ = _engine(mode="ptt")
    e.TELL_AFTER, e.TELL_S = 0, 60.0
    e._on_me(_utt("按现在的逻辑处理。"))
    assert len(_jobs(e)) == 1
    e, _ = _engine()
    e.TELL_AFTER, e.TELL_S = 0, 60.0
    e._me.tells_speaking = False
    e._on_me(_utt("按现在的逻辑处理。"))
    assert len(_jobs(e)) == 1


# ---- the other side

def _said(e, *pieces):
    for src, out in pieces:
        e._them_done(_utt(src, "en"), out, latency=1.0)


def test_what_they_said_in_one_go_is_translated_once_more_as_a_whole():
    e, events = _engine()
    e.RUN_GAP_S = 60.0
    _said(e, ("And this bug is the.", "而且这个bug就是"), ("User who applied for the job.", "申请了这份工作的用户。"))
    rows = [d["row"] for k, d in events if k == "them"]
    assert rows == [1, 2] and e._jobs.empty()
    e._run_over(e._run_gen)  # (what the timer does RUN_GAP_S after their last word)
    (kind, (run, tries)), = _jobs(e)
    assert kind == "them_run" and tries == 0 and e._run == []
    tr = _Says("这个 bug 出在申请这份工作的用户身上。")
    e._do_them_run(tr, run, tries)
    assert tr.asked == ["And this bug is the. User who applied for the job."] and tr.kw[0]["passage"] is True
    assert events[-1] == ("them_run", {"rows": [1, 2], "src": tr.asked[0], "tgt": tr.out})
    assert e._transcript.rows[-1] == ("them_run", tr.asked[0], tr.out)


def test_one_sentence_alone_is_not_translated_twice():
    e, _ = _engine()
    e.RUN_GAP_S = 60.0
    _said(e, ("Is this a model issue.", "这是模型问题吗？"))
    e._run_over(e._run_gen)
    assert e._run == [] and e._run_timer is None and e._jobs.empty()


def test_it_waits_while_they_are_still_talking_or_a_piece_still_waits():
    e, _ = _engine()
    e.RUN_GAP_S = e.RUN_POLL_S = 60.0
    e._them.talking = True
    _said(e, ("And this bug is the.", "而且这个bug就是"), ("User who applied for the job.", "申请了这份工作的用户。"))
    at = e._run_gen
    e._run_over(at)  # (what the timer does)
    assert len(e._run) == 2 and e._jobs.empty()  # they go on: the passage is not complete
    e._them.talking = False
    e._held = _utt("But.", "en")
    e._run_over(at)
    assert len(e._run) == 2 and e._jobs.empty()
    e._held = None
    e._run_over(at)
    assert [k for k, _ in _jobs(e)] == ["them_run"]


def test_the_timer_ends_a_passage_by_itself_and_an_old_timer_ends_nothing():
    e, _ = _engine()
    e.RUN_GAP_S = 60.0
    _said(e, ("And this bug is the.", "而且这个bug就是"))
    old = e._run_gen
    _said(e, ("User who applied for the job.", "申请了这份工作的用户。"))
    e._run_over(old)  # (a timer of an earlier piece that was not cancelled in time)
    assert len(e._run) == 2 and e._jobs.empty()
    e.RUN_GAP_S = 0.02
    _said(e, ("It was rejected.", "它被拒绝了。"))
    assert _until(lambda: not e._jobs.empty())
    (kind, (run, _tries)), = _jobs(e)
    assert kind == "them_run" and len(run) == 3


def test_my_answer_ends_what_they_were_saying():
    e, _ = _engine()
    e.RUN_GAP_S = 60.0
    _said(e, ("And this bug is the.", "而且这个bug就是"), ("User who applied for the job.", "申请了这份工作的用户。"))
    e._on_me(_utt("我明白你的意思了。"))
    assert sorted(k for k, _ in _jobs(e)) == ["me", "them_run"] and e._run == [] and e._run_timer is None


def test_a_passage_is_put_off_while_i_talk_and_my_words_come_first():
    e, events = _engine()
    e.RUN_RETRY_S = 0.02
    run = [(1, "And this bug is the.", "而且这个bug就是", "en"), (2, "User who applied.", "申请的用户。", "en")]
    e._me.talking = True
    tr = _Says("这个 bug 出在申请的用户身上。")
    e._do_them_run(tr, run, 0)
    assert tr.asked == [] and events == []
    assert _until(lambda: not e._jobs.empty())
    assert _jobs(e) == [("them_run", (run, 1))]
    e._do_them_run(tr, run, e.RUN_TRIES - 1)  # the last try: given up, the pieces stay
    assert tr.asked == [] and e._jobs.empty()


def test_a_passage_gives_way_to_a_sentence_that_waits_for_the_translator():
    e, events = _engine()
    e.RUN_RETRY_S = 60.0
    run = [(1, "And this bug is the user.", "而且这个bug就是用户。", "en"), (2, "Who applied.", "谁申请了。", "en")]

    class Slow(_Says):
        def translate(self, text, src, tgt, **kw):
            e._queue_them(_utt("Is this a model issue.", "en"))  # their next sentence arrives meanwhile
            return super().translate(text, src, tgt, **kw)

    e._do_them_run(Slow("这个 bug 出在申请的用户身上。"), run, 0)
    assert [k for k, _ in events] == [] and [k for k, _ in _jobs(e)] == ["them"]  # (the passage: again in a moment)


def test_a_passage_that_says_nothing_new_changes_nothing():
    e, events = _engine()
    run = [(1, "Is this a model issue.", "这是模型问题吗？", "en"), (2, "Or something else.", "还是别的原因？", "en")]
    e._do_them_run(_Says("这是模型问题吗？还是别的原因？"), run, 0)
    e._do_them_run(_Says("这是模型的问题吗，还是别的原因呢？"), run, 0)  # a word moved: rows that were read stay
    e._do_them_run(_Says(""), run, 0)
    e._do_them_run(_Says("这是 model issue 吗 or something else"), run, 0)  # left in their language: worse than the pieces
    assert events == [] and e._transcript.rows == []


def test_a_long_talk_is_several_passages():
    e, _ = _engine()
    e.RUN_GAP_S, e.RUN_MAX_S = 60.0, 8.0
    piece = "We need to check the status of every applicant and."  # ~3.8 s of speech, stopping on a word that asks for more
    _said(e, (piece, "一"), (piece, "二"))
    assert e._jobs.empty() and [r[0] for r in e._run] == [1, 2]
    _said(e, (piece, "三"), (piece, "四"))  # the third does not fit: the first two are a passage
    (kind, (run, _tries)), = _jobs(e)
    assert kind == "them_run" and [r[0] for r in run] == [1, 2] and [r[0] for r in e._run] == [3, 4]


def test_nothing_of_theirs_is_left_waiting_at_stop():
    e, _ = _engine()
    e.RUN_GAP_S = 60.0
    e._save_level = lambda: None
    _said(e, ("And this bug is the.", "而且这个bug就是"), ("User who applied.", "申请的用户。"))
    e.stop()
    assert e._run == [] and e._run_timer is None
