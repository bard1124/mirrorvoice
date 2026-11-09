import numpy as np
import pytest

from mirrorvoice import meeting_apps, selftest


def test_probe_is_found_in_noise_and_not_in_noise_alone():
    p = selftest.probe_signal()
    rng = np.random.default_rng(0)
    noise = rng.normal(0, 0.05, 32000).astype(np.float32)
    rec = noise.copy()
    rec[8000:8000 + len(p)] += p * 0.3
    score, at = selftest.find_probe(rec, p)
    assert score > 0.9 and at == 8000
    assert selftest.find_probe(noise, p)[0] < selftest.PASS_SCORE
    assert selftest.find_probe(np.zeros(32000), p) == (0.0, 0)
    assert selftest.find_probe(np.zeros(10), p)[0] == 0.0


@pytest.mark.parametrize("call,inputs,expect", [
    ("BlackHole 2ch", ["外置麦克风", "BlackHole 2ch", "BlackHole 16ch"], "BlackHole 2ch"),
    ("CABLE Input (VB-Audio Virtual Cable)", ["麦克风", "CABLE Output (VB-Audio Virtual Cable)"],
     "CABLE Output (VB-Audio Virtual Cable)"),
    ("外置耳机", ["外置麦克风"], ""),
    ("BlackHole 2ch", ["外置麦克风"], ""),   # driver missing
])
def test_call_return_input(call, inputs, expect):
    assert selftest.call_return_input(call, inputs) == expect


def test_their_route():
    outs = ["外置耳机", "BlackHole 16ch", "Teams 扬声器 (耳机+字幕)"]
    assert selftest.their_route("BlackHole 16ch", outs, "") == ("Teams 扬声器 (耳机+字幕)", "会议扬声器")
    assert selftest.their_route("BlackHole 16ch", outs[:2], "") == ("BlackHole 16ch", "BlackHole 16ch")
    assert selftest.their_route("loopback:default", ["耳机 (Realtek)"], "耳机 (Realtek)") == ("耳机 (Realtek)", "扬声器")
    assert selftest.their_route("外置麦克风", outs, "") == ("", "")


def test_skipped_checks_explain_why():
    from mirrorvoice.config import Settings

    s = Settings(call_out="外置耳机", subtitles=False, monitor="")
    assert selftest.check_call(s, ["外置麦克风"]).ok is None
    assert selftest.check_their(s, [], "").ok is None
    assert selftest.check_monitor(s).ok is None


@pytest.mark.parametrize("mac", [True, False])
def test_meeting_guides(mac):
    text = meeting_apps.guide_text(mac)
    for app in meeting_apps.APPS:
        assert app in text
    if mac:
        assert "BlackHole 2ch" in text and "Teams 扬声器 (耳机+字幕)" in text and "CABLE" not in text
    else:
        assert "CABLE Output (VB-Audio Virtual Cable)" in text and "BlackHole" not in text
