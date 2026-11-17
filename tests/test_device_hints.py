import pytest

from mirrorvoice import device_hints as dh

MAC_OUT = ["BlackHole 16ch", "BlackHole 2ch", "外置耳机", "MacBook Pro扬声器", "Microsoft Teams Audio",
           "Teams 扬声器 (耳机+字幕)"]
MAC_IN = ["“Bard的iPhone”的麦克风", "BlackHole 16ch", "BlackHole 2ch", "外置麦克风", "MacBook Pro麦克风",
          "Microsoft Teams Audio"]
MAC = dh.Context("BlackHole 2ch", "BlackHole 16ch", "外置耳机")

WIN_OUT = ["扬声器 (Realtek(R) Audio)", "耳机 (Realtek(R) Audio)", "CABLE Input (VB-Audio Virtual Cable)",
           "Microsoft Teams Audio Device"]
WIN_IN = ["麦克风阵列 (Realtek(R) Audio)", "CABLE Output (VB-Audio Virtual Cable)", "Microsoft Teams Audio Device",
          "loopback:default", "loopback:扬声器 (Realtek(R) Audio)", "loopback:CABLE Input (VB-Audio Virtual Cable)"]
WIN = dh.Context("CABLE Input", "loopback:default", "耳机 (Realtek(R) Audio)")


def rank(role, name, ctx):
    return dh.hint(role, name, ctx).rank


@pytest.mark.parametrize("role,names,ctx,first", [
    ("call", MAC_OUT, MAC, "BlackHole 2ch"),
    ("monitor", MAC_OUT, MAC, "外置耳机"),
    ("source", MAC_IN, MAC, "BlackHole 16ch"),
    ("call", WIN_OUT, WIN, "CABLE Input (VB-Audio Virtual Cable)"),
    ("source", WIN_IN, WIN, "loopback:default"),
])
def test_recommended_is_listed_first(role, names, ctx, first, monkeypatch):
    # (which machine the names are from, not which one the test runs on: listening to my own
    #  translation is recommended on a Mac only — see the test of that below)
    monkeypatch.setattr(dh, "IS_WIN", ctx is WIN)
    ordered = dh.ordered(role, names, ctx)
    assert ordered[0] == first
    assert rank(role, first, ctx) == dh.RECOMMENDED
    assert sorted(ordered) == sorted(names)  # nothing hidden


@pytest.mark.parametrize("role,name,ctx", [
    ("call", "BlackHole 16ch", MAC),                  # translation into the subtitle line
    ("call", "Teams 扬声器 (耳机+字幕)", MAC),         # ... via the meeting speaker
    ("monitor", "BlackHole 2ch", MAC),                 # same as 送进会议: heard twice
    ("source", "BlackHole 2ch", MAC),                  # subtitles of my own translation
    ("mic", "BlackHole 2ch", MAC),                     # translation loop
    ("source", "CABLE Output (VB-Audio Virtual Cable)", WIN),
    ("mic", "CABLE Output (VB-Audio Virtual Cable)", WIN),
    ("source", "loopback:CABLE Input (VB-Audio Virtual Cable)", WIN),
    ("call", "耳机 (Realtek(R) Audio)", WIN),          # the speaker that loopback records
])
def test_conflicts(role, name, ctx):
    h = dh.hint(role, name, ctx)
    assert h.rank == dh.CONFLICT and h.tag.startswith("⚠️") and h.warning


def test_default_speaker_set_to_cable_is_an_echo():
    ctx = dh.Context("CABLE Input", "loopback:default", "CABLE Input (VB-Audio Virtual Cable)")
    assert rank("source", "loopback:default", ctx) == dh.CONFLICT


@pytest.mark.parametrize("role,name,ctx,tag", [
    ("call", "外置耳机", MAC, "仅自己能听到"),
    ("call", "Microsoft Teams Audio", MAC, "Teams 共享屏幕用"),
    ("source", "外置麦克风", MAC, "麦克风，会收到你自己"),
    ("monitor", "MacBook Pro扬声器", MAC, "外放，建议用耳机"),
    ("mic", "BlackHole 16ch", MAC, "虚拟声卡，不是麦克风"),
])
def test_tags(role, name, ctx, tag, monkeypatch):
    monkeypatch.setattr(dh, "IS_WIN", ctx is WIN)
    assert dh.hint(role, name, ctx).tag == tag


def test_on_windows_no_device_is_recommended_for_listening_to_my_own_translation(monkeypatch):
    """Teams takes out of the microphone what plays on its speaker: with 我自己也听 on the headphones
    nobody in the meeting heard the translation (2026-11-17). 不监听 is the first entry of the list."""
    monkeypatch.setattr(dh, "IS_WIN", True)
    for name in ("耳机 (Realtek(R) Audio)", "扬声器 (Realtek(R) Audio)", "Bose QC45"):
        h = dh.hint("monitor", name, WIN)
        assert h.rank == dh.UNSUITED and h.tag == "开会时对方会听不到翻译" and "不监听" in h.warning, name
    assert dh.hint("monitor", "", WIN).rank == dh.NORMAL  # 不监听 itself
    assert dh.hint("monitor", "CABLE Input (VB-Audio Virtual Cable)", WIN).rank == dh.CONFLICT  # (said for its own reason)
    assert all(dh.hint("monitor", n, WIN).rank != dh.RECOMMENDED for n in WIN_OUT)
    assert dh.hint("call", "CABLE Input (VB-Audio Virtual Cable)", WIN).rank == dh.RECOMMENDED  # (the other roles as before)
    monkeypatch.setattr(dh, "IS_WIN", False)
    assert dh.hint("monitor", "外置耳机", MAC).rank == dh.RECOMMENDED
    assert dh.hint("monitor", "耳机 (Realtek(R) Audio)", WIN).rank == dh.RECOMMENDED  # decided by the platform, not the name


def test_advice_names_the_platform_device(monkeypatch):
    monkeypatch.setattr(dh, "IS_WIN", True)
    w = dh.hint("call", "扬声器 (Realtek(R) Audio)", WIN).warning
    assert "CABLE Input" in w and "BlackHole" not in w
    monkeypatch.setattr(dh, "IS_WIN", False)
    m = dh.hint("call", "外置耳机", MAC).warning
    assert "BlackHole 2ch" in m and "CABLE" not in m


def test_label():
    h = dh.hint("call", "BlackHole 2ch", MAC)
    assert dh.label("BlackHole 2ch", h) == "BlackHole 2ch（推荐）"
    assert dh.label("loopback:default", dh.Hint(dh.NORMAL)) == "扬声器回采：系统默认扬声器"
    assert dh.hint("mic", "", MAC).rank == dh.NORMAL  # the 系统默认 entry


@pytest.mark.parametrize("members,monitor,default,expect", [
    (["外置耳机", "BlackHole 16ch"], "外置耳机", "外置耳机", ""),
    (["外置耳机", "BlackHole 16ch"], "Bard的AirPods Pro", "", "Bard的AirPods Pro"),
    (["missing:AB-12", "BlackHole 16ch"], "", "Bard的AirPods Pro", "Bard的AirPods Pro"),
    (["missing:AB-12", "BlackHole 16ch"], "", "MacBook Pro扬声器", ""),  # never route the meeting to loudspeakers
    (["外置耳机", "BlackHole 16ch"], "MacBook Pro扬声器", "", ""),
    (["外置耳机", "BlackHole 16ch"], "BlackHole 2ch", "外置耳机", ""),   # monitor is not headphones
    ([], "AirPods", "", ""),                                             # no multi-output: wizard's job
])
def test_multi_output_fix(members, monitor, default, expect):
    target, why = dh.multi_output_fix(members, monitor, default)
    assert target == expect and bool(why) == bool(expect)


def test_the_meeting_guide_says_it_on_windows_only():
    from mirrorvoice import meeting_apps

    win = "\n".join(meeting_apps.steps("Teams", mac=False))
    assert "不监听" in win and "回声" in win
    assert "不监听" not in "\n".join(meeting_apps.steps("Teams", mac=True))


def test_the_line_test_does_not_call_it_fine(monkeypatch):
    import sys
    import types

    from mirrorvoice import selftest
    from mirrorvoice.config import Settings

    class Out:
        def __init__(self, *a):
            pass

        def put(self, *a):
            pass

        def close(self):
            pass

    monkeypatch.setattr("mirrorvoice.audio.DeviceOut", Out)
    monkeypatch.setattr("mirrorvoice.audio.find_device", lambda name, kind: 0)
    monkeypatch.setattr(selftest.time, "sleep", lambda s: None)
    s = Settings(monitor="耳机 (Realtek(R) Audio)")
    monkeypatch.setattr(dh, "IS_WIN", True)
    c = selftest.check_monitor(s)
    assert c.ok is False and "不监听" in c.detail and "回声" in c.detail
    monkeypatch.setattr(dh, "IS_WIN", False)
    assert selftest.check_monitor(s).ok is None
    monkeypatch.setattr(dh, "IS_WIN", True)
    assert selftest.check_monitor(Settings(monitor="")).ok is None  # 不监听: nothing to say


def test_the_engine_says_it_at_every_start_on_windows(monkeypatch):
    from mirrorvoice import engine
    from mirrorvoice.config import Settings

    voice = object()
    monkeypatch.setattr(engine, "IS_WIN", True)
    on = Settings(monitor="耳机 (Realtek(R) Audio)", speak=True)
    assert engine.Engine._monitor_cancelled(on, voice)
    assert not engine.Engine._monitor_cancelled(Settings(monitor="", speak=True), voice)
    assert not engine.Engine._monitor_cancelled(Settings(monitor="耳机", speak=False), voice)  # nothing is spoken
    assert not engine.Engine._monitor_cancelled(on, None)
    monkeypatch.setattr(engine, "IS_WIN", False)
    assert not engine.Engine._monitor_cancelled(on, voice)
