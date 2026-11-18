"""The other side's subtitles never keep my voice waiting, and previews do not crowd out the finished
sentences. Meeting of 2026-11-18 (29 minutes of fluent English, Qwen3-ASR for the subtitles): one of
their sentences took 8.5 s to translate and a sentence of mine was heard 12.1 s late behind it; 589
previews were translated (5.2 a sentence) and the finished subtitles came 3.8 s after the speaker
stopped; thirteen "passages" were made of whole sentences that gained nothing from it."""
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


class _Says:
    """A translator that writes its answer in two steps, and lets the test act between them."""
    glossary: list = []
    backend = None

    def __init__(self, out, between=None):
        self.out, self.asked, self.between = out, [], between

    def translate(self, text, src, tgt, **kw):
        self.asked.append(text)
        on_text = kw.get("on_text")
        if on_text:
            on_text(self.out[:2])
            if self.between:
                self.between()
            on_text(self.out)
        return self.out


def _engine(**settings):
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running = Settings(my_lang="zh", their_lang="en", quiet_gate=False, **settings), True
    e._transcript, e._them, e._me = _Rows(), _Listener(), _Listener()
    return e, events


def _utt(text, lang="en", sec=1.5, ago=0.0):
    return Utterance(text, lang, np.full(int(sec * 16000), 0.1, np.float32), time.time() - ago, 0.05)


def _jobs(e):
    out = []
    while not e._jobs.empty():
        out.append(e._jobs.get_nowait())
    return out


def _until(cond, timeout=10.0):
    deadline = time.monotonic() + timeout
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.005)
    return cond()


# ---------------------------------------------------------------- my speech first

def test_a_subtitle_being_translated_gives_way_to_what_i_say_and_is_done_again_after_it():
    e, events = _engine()
    theirs = _utt("We will work as you direct on this particular story.")
    mine = _utt("我有一个问题。", "zh")
    e._do_them(_Says("我们会按你的指示处理这个故事。", between=lambda: e._queue_me("me", mine, mine)), theirs, 7)
    assert [k for k, _ in events if k == "them"] == [] and e._transcript.rows == []  # not finished: nothing is final
    first, second = _jobs(e)
    assert (first[0], first[2]) == (0, "me") and second[:3] == (1, 7, "them") and second[3] is theirs  # mine, then it — in its old place
    e._translating(mine)  # (the worker has taken my sentence)
    tr = _Says("我们会按你的指示处理这个故事。")
    e._do_them(tr, theirs, 7)
    assert events[-1][0] == "them" and events[-1][1]["tgt"] == tr.out and e._them_again == {}


def test_one_that_gave_way_is_not_skipped_for_being_late():
    e, events = _engine()
    old = _utt("We will work as you direct on this particular story.", ago=30.0)
    e._do_them(_Says("我们会按你的指示处理这个故事。"), old, 7)
    assert "积压太多" in events[-1][1]["tgt"]  # late by itself: skipped, as before
    e._them_again[8] = 1
    e._do_them(_Says("我们会按你的指示处理这个故事。"), old, 8)
    assert events[-1][1]["tgt"] == "我们会按你的指示处理这个故事。"  # late because I spoke: still shown


def test_it_gives_way_a_few_times_only_and_never_without_a_place_in_the_queue():
    e, events = _engine()
    theirs = _utt("We will work as you direct on this particular story.")
    e._waiting_s = 2.0  # something of mine is always waiting (I talk without a break)
    for n in range(1, e.THEM_YIELDS + 1):
        e._do_them(_Says("我们会按你的指示处理这个故事。"), theirs, 7)
        assert e._them_again == {7: n} and len(_jobs(e)) == 1
    e._do_them(_Says("我们会按你的指示处理这个故事。"), theirs, 7)  # … then it is finished whatever comes
    assert events[-1][0] == "them" and e._them_again == {} and e._jobs.empty()
    e._do_them(_Says("好的。"), _utt("Okay then."))  # (called without its place: as it always was)
    assert events[-1][1]["tgt"] == "好的。" and e._jobs.empty()


def test_a_preview_gives_way_too():
    e, events = _engine()
    e._on_them_guess(_utt("So the next point is about the release."))
    _jobs(e)
    e._waiting_s = 1.5
    tr = _Says("下一点是关于发布的。")
    n = len(events)
    e._do_them_guess(tr)
    assert tr.asked == ["So the next point is about the release."] and len(events) == n  # given up, nothing shown


# ---------------------------------------------------------------- previews

def test_the_first_pause_of_a_sentence_is_translated_and_the_next_ones_not_each_time():
    e, events = _engine()
    e.PREVIEW_LATE_S = 60.0
    e._on_them_guess(_utt("So for this one Cindy added"))
    assert [j[2] for j in _jobs(e)] == ["them_guess"]
    e._do_them_guess(_Says("所以对于这个，Cindy 加了"))
    e._on_them_guess(_utt("So for this one Cindy added some design about the user guide"))  # a moment later, six words more
    assert e._jobs.empty() and e._preview_timer is not None  # shown, not translated again yet
    assert events[-1] == ("partial", {"who": "them", "src": "So for this one Cindy added some design about the user guide", "tgt": ""})
    e._on_them_guess(None)  # they went on: that pause was no end of a sentence
    assert e._preview_timer is None and e._jobs.empty()


def test_later_in_the_sentence_a_preview_is_translated_again_when_there_is_time_and_more_to_say():
    e, _ = _engine()
    e.PREVIEW_LATE_S = 60.0
    e._on_them_guess(_utt("So for this one Cindy added"))
    _jobs(e)
    e._do_them_guess(_Says("所以对于这个，Cindy 加了"))
    gen, at, words = e._preview_tr
    e._preview_tr = (gen, at - e.PREVIEW_EVERY_S - 0.1, words)  # (as if that was a little over two seconds ago)
    e._on_them_guess(_utt("So for this one Cindy added some"))  # one word more: not worth it
    assert e._jobs.empty()
    e._on_them_guess(None)
    e._on_them_guess(_utt("So for this one Cindy added some design about the user guide"))
    assert [j[2] for j in _jobs(e)] == ["them_guess"]


def test_a_pause_that_lasts_is_translated_and_the_next_sentence_starts_afresh():
    e, _ = _engine()
    e.PREVIEW_LATE_S = 0.02
    e._on_them_guess(_utt("So for this one Cindy added"))
    _jobs(e)
    e._do_them_guess(_Says("所以对于这个，Cindy 加了"))
    e._on_them_guess(_utt("So for this one Cindy added some design about the user guide"))
    assert _until(lambda: not e._jobs.empty())  # the pause went on: this is how a sentence ends
    assert [j[2] for j in _jobs(e)] == ["them_guess"]
    tr = _Says("所以对于这个，Cindy 加了一些关于用户指南的设计")
    e._do_them_guess(tr)  # (the worker takes it)
    assert tr.asked == ["So for this one Cindy added some design about the user guide"]
    e.PREVIEW_LATE_S = 60.0
    e._on_them(_utt("So for this one Cindy added some design about the user guide."))  # the sentence is finished
    _jobs(e)
    e._on_them_guess(_utt("And here you can have a"))  # the next one: its first pause, at once
    assert [j[2] for j in _jobs(e)] == ["them_guess"]


def test_a_pause_that_lasts_in_a_sentence_already_finished_translates_nothing():
    e, _ = _engine()
    e._on_them_guess(_utt("So for this one Cindy added"))
    _jobs(e)
    e._do_them_guess(_Says("所以对于这个，Cindy 加了"))
    gen = e._them_gen
    e._on_them(_utt("So for this one Cindy added some design."))
    _jobs(e)
    e._preview_go(8, gen)  # (what the timer of the old sentence would do)
    assert e._jobs.empty()


def test_the_finished_sentence_uses_the_preview_s_translation_when_only_the_capitals_differ():
    e, events = _engine()
    e._on_them_guess(_utt("okay so the backend is already done"))  # as the first recogniser writes it
    _jobs(e)
    tr = _Says("好的，后端已经完成了。")
    e._do_them_guess(tr)
    e._on_them(_utt("Okay, so the backend is already done."))  # as the second one does
    (_, seq, kind, utt), = _jobs(e)
    e._do_them(tr, utt, seq)
    assert len(tr.asked) == 1 and events[-1][0] == "them" and events[-1][1]["tgt"] == tr.out


def test_nothing_of_a_preview_is_left_waiting_at_stop():
    e, _ = _engine()
    e.PREVIEW_LATE_S = 60.0
    e._save_level = lambda: None
    e._on_them_guess(_utt("So for this one Cindy added"))
    _jobs(e)
    e._do_them_guess(_Says("所以对于这个，Cindy 加了"))
    e._on_them_guess(_utt("So for this one Cindy added some design about the user guide"))
    assert e._preview_timer is not None
    e.stop()
    assert e._preview_timer is None


# ---------------------------------------------------------------- passages

@pytest.mark.parametrize("pieces, worth", [
    (["Let me have a check. What's this one?", "Helen."], False),                     # a short answer at the end is an answer
    (["Okay, so I think this ticket is not linked to ours, right?", "Yes."], False),
    (["Hi, Sandy. Let's go ahead.", "Most people are already here. Let me share my screen."], False),
    (["Usability.", "So for this one Cindy added some design about the user guide."], True),   # a fragment, and more
    (["And this bug is the.", "User who applied for the job."], True),                 # stops on a word that asks for more
    (["So that is why I wanted to.", "Check it with you.", "Okay."], True),
])
def test_only_what_was_cut_is_put_together_again(pieces, worth):
    e, _ = _engine()
    e.RUN_GAP_S = 60.0
    for i, src in enumerate(pieces):
        e._them_done(_utt(src), f"译文{i}", latency=1.0)
    e._run_over(e._run_gen)  # (what the timer does when they have stopped)
    assert [j[2] for j in _jobs(e)] == (["them_run"] if worth else []) and e._run == []
