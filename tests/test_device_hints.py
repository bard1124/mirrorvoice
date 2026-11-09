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
    ("monitor", WIN_OUT, WIN, "耳机 (Realtek(R) Audio)"),
    ("source", WIN_IN, WIN, "loopback:default"),
])
def test_recommended_is_listed_first(role, names, ctx, first):
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
def test_tags(role, name, ctx, tag):
    assert dh.hint(role, name, ctx).tag == tag


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
