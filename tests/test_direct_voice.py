"""原声直说, and what else the meeting of 2026-11-16 (14:01) asked for: I put a question in Chinese,
the others heard nothing for seconds ("没声音，你是不是在说？") and then said "你直接用中文说" — which
the app had no way to do. Also: a short piece of mine was spoken 5 s late, and the subtitles kept
"yeah", hesitation marks and rows of "呃。"."""
import time

import numpy as np
import pytest

from mirrorvoice.asr import Utterance
from mirrorvoice.config import Settings
from mirrorvoice.translate import Translator, tidy_asr, tidy_subtitle


# ---------------------------------------------------------------- words

@pytest.mark.parametrize("heard, clean", [
    ("OkayO sure Okay, thanks.", "Okay sure Okay, thanks."),
    ("Yeah, sureO.", "Yeah, sure."),
    ("Also, just like the 嗯The displaying logic of this呃 bar chart.",
     "Also, just like the The displaying logic of this bar chart."),
    ("呃，day per page and呃 per week.", "day per page and per week."),
    ("In this呃.", "In this."),
    ("嗯，table list.", "table list."),
    ("I think mean 嗯这 is in developing.", "I think mean 嗯这 is in developing."),  # real Chinese in it: not mine to cut
    ("这个or好像好久之前就做完了，对吧？嗯", "这个or好像好久之前就做完了，对吧？嗯"),
    ("呃。", "呃。"),
    ("It should be O.", "It should be OK."),
    ("HELLO", "HELLO"),
])
def test_what_the_recogniser_adds_to_an_english_sentence_is_taken_out(heard, clean):
    assert tidy_asr(heard) == clean


@pytest.mark.parametrize("out, shown", [
    ("好的， sure，谢谢。好的，所以第一个。", "好的，好的，谢谢。好的，所以第一个。"),
    ("是的， yeah，这个。", "是的，嗯，这个。"),
    ("yeah，就像这样 yeah。", "嗯，就像这样嗯。"),
    ("Yes，就是这个。", "是的，就是这个。"),
    ("抱歉，我 just 举手。", "抱歉，我 just 举手。"),
    ("我们要 make sure 这个上线", "我们要 make sure 这个上线"),  # a phrase left in English is left alone
    ("OK Google 是一个唤醒词", "OK Google 是一个唤醒词"),
    ("一旦 grooming 完成", "一旦 grooming 完成"),
])
def test_yeah_and_okay_left_in_a_chinese_subtitle_are_said_in_chinese(out, shown):
    assert tidy_subtitle(out, "zh") == shown
    assert tidy_subtitle(out, "en") == out  # (only where the subtitle is Chinese)


def test_the_translator_cleans_subtitles_and_never_what_i_say():
    class Backend:
        def chat(self, msgs):
            return "Yeah， sure，这个可以。" if "this works" in msgs[-1]["content"] else "Yeah, sure."

    assert Translator(Backend()).translate("Yeah, sure, this works.", "en", "zh", subtitle=True) == "嗯，好的，这个可以。"
    assert Translator(Backend()).translate("嗯，好的。", "zh", "en") == "Yeah, sure."


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
        self.audio, self.live = [], []

    def put(self, audio, sr):
        self.audio.append(audio)

    def put_live(self, audio, sr):
        self.live.append((audio, sr))


class _Player:
    def __init__(self):
        self.outs = [_Out(), _Out()]  # the meeting, my headphones


def _engine(**settings):
    from mirrorvoice.engine import Engine

    events = []
    e = Engine(lambda kind, **d: events.append((kind, d)))
    e.s, e.running, e._ready = Settings(my_lang="zh", their_lang="en", quiet_gate=False, **settings), True, True
    e._transcript, e._them, e._me, e._player = _Rows(), _Listener(), _Listener(), _Player()
    e.TELL_AFTER = 99
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


def test_with_it_on_my_voice_goes_to_the_meeting_as_it_is_and_nothing_is_translated():
    e, events = _engine(gain=1.0)
    block = np.full(512, 0.25, np.float32)
    e._on_my_audio(block)
    assert e._player.outs[0].live == []  # off: the microphone is only listened to
    e.set_direct(True)
    assert e.direct and events[-1] == ("direct", {"on": True})
    e._on_my_audio(block)
    (sent, sr), = e._player.outs[0].live
    assert sr == 16000 and np.array_equal(sent, block) and e._player.outs[1].live == []  # not into my own ears
    e._on_me_guess(_utt("我看到这个卡里面有一个字段。", guess=1))
    e._on_me(_utt("我看到这个卡里面有一个字段。", guess=1))
    assert e._jobs.empty() and e._player.outs[0].audio == []  # no translation, nothing spoken for me
    assert events[-1] == ("me", {"src": "我看到这个卡里面有一个字段。", "tgt": "我看到这个卡里面有一个字段。", "latency": 0.0,
                                 "translate_s": 0.0, "passthrough": True})
    assert e._transcript.rows == [("me", "我看到这个卡里面有一个字段。", "（原声直说）")]
    e._on_me(_utt("嗯。"))
    assert len(e._transcript.rows) == 1  # a hesitation is no line of the record
    e.set_direct(False)
    e._on_my_audio(block)
    e._on_me(_utt("我们回到翻译的方式。"))
    assert len(e._player.outs[0].live) == 1 and _jobs(e) == [("me", "我们回到翻译的方式。")]


def test_my_voice_is_never_louder_than_full_scale_and_silent_while_muted():
    e, _ = _engine(gain=3.0)
    e.set_direct(True)
    e._on_my_audio(np.full(512, 0.5, np.float32))
    assert float(e._player.outs[0].live[0][0].max()) == 1.0
    e.muted = True
    e._on_my_audio(np.full(512, 0.5, np.float32))
    assert len(e._player.outs[0].live) == 1


def test_what_i_said_before_is_still_translated_when_i_switch():
    e, _ = _engine()
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._on_me(_utt("然后那个。"))
    assert e._held_me is not None
    e.set_direct(True)
    assert _jobs(e) == [("me", "然后那个。")] and e._held_me is None


def test_it_is_off_at_every_start_and_cannot_be_put_on_while_stopped():
    e, events = _engine()
    e.set_direct(True)
    e._save_level = lambda: None
    e.stop()
    assert not e.direct and ("direct", {"on": False}) in events
    e.set_direct(True)  # stopped: there is no meeting to speak into
    assert not e.direct and events[-1] == ("direct", {"on": False})
    e.running, e._ready = True, False  # still loading
    e.set_direct(True)
    assert not e.direct


def test_without_a_device_for_the_meeting_it_says_so_and_stays_off():
    e, events = _engine()
    e._player = None
    e._ensure_player = lambda: (_ for _ in ()).throw(LookupError("BlackHole 2ch"))
    e.set_direct(True)
    assert not e.direct and [k for k, _ in events] == ["notice", "direct"] and "BlackHole 2ch" in events[0][1]["text"]


def test_the_listener_hands_on_what_it_listens_to_and_nothing_while_the_gate_is_shut(monkeypatch):
    from mirrorvoice import asr

    class Seg:
        count = 0

        def __init__(self, *a, **k):
            pass

        def feed(self, block, gate):
            if len(block):
                fed.append(float(block[0]))

        def flush(self):
            pass

    class Cap:
        label = "mic"

        def __init__(self, source, put, block):
            self.put = put

        def start(self):
            pass

        def close(self):
            pass

        def health(self):
            return ""

        def stalled(self):
            return False

    monkeypatch.setattr(asr, "Segmenter", Seg)
    monkeypatch.setattr(asr, "Capture", Cap)
    heard, gate, fed = [], [True], []
    me = asr.Listener("me", "mic", "zh", 0.35, lambda u: None, gate=lambda: gate[0], on_audio=heard.append)
    me.start()
    try:
        me.capture.put(np.full(512, 0.1, np.float32))
        assert _until(lambda: len(heard) == 1)
        assert _until(lambda: len(fed) == 1)
        gate[0] = False
        me.capture.put(np.full(512, 0.2, np.float32))
        assert _until(lambda: len(fed) == 2) and len(heard) == 1  # listened to by nobody: not handed on
        gate[0] = True
        me.capture.put(np.full(512, 0.3, np.float32))
        assert _until(lambda: len(heard) == 2) and float(heard[1][0]) == pytest.approx(0.3)
    finally:
        me.close()


def test_live_audio_is_continuous_across_the_blocks_and_starts_with_a_little_in_hand():
    from mirrorvoice.audio import DeviceOut

    class Lock:
        def __enter__(self):
            pass

        def __exit__(self, *a):
            pass

    class Dev:
        LIVE_LEAD_S, lock, put_live = DeviceOut.LIVE_LEAD_S, Lock(), DeviceOut.put_live

        def __init__(self, sr):
            self.sr, self.buf, self._live_last, self._live_pos = sr, np.zeros(0, np.float32), 0.0, 1.0

    t = np.arange(16000) / 16000
    x = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    for sr in (48000, 44100, 16000):
        d = Dev(sr)
        for i in range(0, len(x), 512):
            d.put_live(x[i:i + 512], 16000)
        lead = int(DeviceOut.LIVE_LEAD_S * sr)
        assert not d.buf[:lead].any() and abs(len(d.buf) - lead - sr) <= 2
        got = d.buf[lead:]
        want = np.sin(2 * np.pi * 440 * np.arange(len(got)) / sr)
        assert float(np.abs(got - want).max()) < 0.01  # (holding the last sample of each block: 0.17)


# ---- a piece of mine that may be a sentence

def test_a_piece_that_may_be_a_sentence_waits_half_as_long_as_one_that_asks_for_more():
    e, _ = _engine()
    e.MORE_S = {"open": 60.0, "short": 60.0}
    e._on_me(_utt("对，是我在说。"))
    assert e._held_me_max == e.HOLD_ME_SHORT_S == 2.0
    e._take_held_me()
    e._on_me(_utt("然后那个。"))
    assert e._held_me_max == e.HOLD_ME_MAX_S == 4.0


def test_it_goes_out_alone_when_my_next_sentence_takes_longer_than_that():
    e, _ = _engine()
    e.MORE_S, e.HOLD_ME_POLL_S, e.HOLD_ME_SHORT_S, e.HOLD_ME_MAX_S = {"open": 0.02, "short": 0.02}, 0.02, 0.2, 60.0
    e._me.talking = True  # … and I keep talking: a long sentence
    e._on_me(_utt("对，是我在说。"))
    assert _until(lambda: not e._jobs.empty())
    assert _jobs(e) == [("me", "对，是我在说。")]


# ---- their side

def test_a_hesitation_in_my_language_is_no_row():
    e, events = _engine()
    for text in ("呃。", "嗯。", "嗯嗯。"):
        e._on_them(_utt(text, "zh"))
    assert events == [] and e._transcript.rows == []
    e._on_them(_utt("这个是发到哪了？", "zh"))
    assert [k for k, _ in events] == ["them"] and len(e._transcript.rows) == 1


def test_previews_leave_a_line_in_the_log(monkeypatch):
    e, _ = _engine()
    lines = []
    e.log = lines.append
    e._on_them_guess(_utt("We have five global filters on the top", "en", sec=3.2))
    assert any(line.startswith("[them] 预览原文：已说 3.2s，8 词") for line in lines)

    class Says:
        glossary: list = []

        def translate(self, text, src, tgt, **kw):
            return "页面顶部有五个全局筛选器"

    e._jobs.get_nowait()
    e._do_them_guess(Says())
    assert any(line.startswith("[them] 预览译文：已说 3.2s，停顿后 ") for line in lines)


# ---------------------------------------------------------------- the control panel, the tray, the key

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


def test_the_button_the_tray_and_the_log_follow_the_engine(monkeypatch, home):
    from mirrorvoice.ui import main_window, theme

    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(app)
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, kind, lambda *a, **k: None)
    monkeypatch.setattr(main_window, "list_devices", lambda kind: ["BlackHole 2ch", "BlackHole 16ch", "外置耳机"])
    monkeypatch.setattr(main_window, "default_output_name", lambda: "外置耳机")
    monkeypatch.setattr(main_window, "IS_MAC", False)
    s = Settings(auto_check_update=False, setup_done=True, call_out="BlackHole 2ch", their_source="BlackHole 16ch")
    s.save()
    w = main_window.MainWindow(s)
    try:
        asked = []
        monkeypatch.setattr(w.engine, "set_direct", asked.append)
        assert w.direct_btn.text() == "原声直说" and "Ctrl+Alt+D" in w._hotkey_text
        w.direct_btn.click()  # the button asks the engine …
        w.toggle_direct()
        assert asked == [True, False]
        n = w.log.count()
        w._on_event("direct", {"on": True})  # … and shows what the engine says (the hotkey goes this way too)
        assert w.direct_btn.isChecked() and w.direct_btn.text() == "直说中" and asked == [True, False]
        assert w.log.count() == n + 1 and "原声直说" in w.log.item(n).text()
        w._on_event("direct", {"on": True})
        assert w.log.count() == n + 1  # said once
        w.engine.running, w.engine.direct = True, True
        w.tray.refresh()
        assert w.tray.act_direct.isEnabled() and w.tray.act_direct.isChecked()
        w.engine.running, w.engine.direct = False, False
        w._on_event("direct", {"on": False})
        w.tray.refresh()
        assert not w.direct_btn.isChecked() and w.direct_btn.text() == "原声直说" and not w.tray.act_direct.isEnabled()
        assert w.log.count() == n + 2
    finally:
        w.close()


def test_the_key_for_it_is_registered_beside_the_mute_key(monkeypatch):
    import sys
    import types

    from mirrorvoice import hotkeys

    made = {}

    class GlobalHotKeys:
        def __init__(self, combos):
            made.update(combos)
            self.daemon = False

        def start(self):
            pass

        def stop(self):
            pass

    class Listener(GlobalHotKeys):
        def __init__(self, on_press=None, on_release=None):
            self.daemon = False

    class Key:
        alt_r = object()

    kb = types.SimpleNamespace(GlobalHotKeys=GlobalHotKeys, Listener=Listener, Key=Key,
                               KeyCode=types.SimpleNamespace(from_char=lambda c: c))
    monkeypatch.setitem(sys.modules, "pynput", types.SimpleNamespace(keyboard=kb))
    monkeypatch.setitem(sys.modules, "pynput.keyboard", kb)
    monkeypatch.setattr(hotkeys.sys, "platform", "linux")
    calls = []
    hk = hotkeys.Hotkeys("alt_r", "<ctrl>+<alt>+m", lambda down: None, lambda: calls.append("mute"),
                         "<ctrl>+<alt>+d", lambda: calls.append("direct"))
    hk.start()
    made["<ctrl>+<alt>+d"]()
    made["<ctrl>+<alt>+m"]()
    assert calls == ["direct", "mute"] and Settings().direct_hotkey == "<ctrl>+<alt>+d"
    hk.stop()
