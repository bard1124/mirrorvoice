"""一键设置 / 一键还原 of Teams' microphone and speaker (its cmd_settings.json)."""
import json

import pytest

from mirrorvoice import device_hints as dh
from mirrorvoice import teams_devices as td

# as written by Teams 2 on macOS (2026-11), shortened
BEFORE = {"videoDenoisingEnabled": 0, "calling_selected_camera_device": {}, "call_set_touch_up_strength": -0.1,
          "calling_selected_microphone_device": {"Microphone": "BuiltInMicrophoneDevice"},
          "calling_selected_speaker_device": {"Speaker": "BuiltInHeadphoneOutputDevice"},
          "calling_user_selected_devices": {"Speaker": "BuiltInHeadphoneOutputDevice",
                                            "Microphone": "BuiltInMicrophoneDevice"},
          "noise_suppression_mode": 2}


@pytest.fixture
def teams(tmp_path, monkeypatch):
    f = tmp_path / "MSTeams" / "cmd_settings.json"
    f.parent.mkdir()
    f.write_text(json.dumps(BEFORE), encoding="utf-8")
    monkeypatch.setenv("MIRRORVOICE_TEAMS_SETTINGS", str(f))
    monkeypatch.setattr(td, "BACKUP", tmp_path / "teams-devices-before.json")
    return f


def _now(f):
    return json.loads(f.read_text(encoding="utf-8"))


def test_one_click_points_teams_at_the_app_and_one_click_puts_it_back(teams):
    assert td.state() == "other"
    assert "已设置好" in td.apply()
    d = _now(teams)
    assert d[td.MIC_KEY] == {"Microphone": "BlackHole2ch_UID"}
    assert d[td.SPEAKER_KEY] == {"Speaker": "local.zh2en-voice.multi-output"}
    assert d[td.CHOSEN_KEY] == {"Speaker": "local.zh2en-voice.multi-output", "Microphone": "BlackHole2ch_UID"}
    assert {k: v for k, v in d.items() if k not in td.KEYS} == {k: v for k, v in BEFORE.items() if k not in td.KEYS}
    assert td.state() == "set" and td.has_backup()
    assert not list(teams.parent.glob("*.mirrorvoice"))  # no half-written file left beside it

    assert "已经是这样" in td.apply()  # a second click keeps the first memory of what was there
    assert "之前的样子" in td.restore()
    assert _now(teams) == BEFORE and td.state() == "other" and not td.has_backup()


def test_restore_with_nothing_remembered_goes_back_to_teams_own_default(teams):
    """Set by hand long ago (as on the author's machine): there is no "before" to return to."""
    teams.write_text(json.dumps({**BEFORE, **td.wanted()}), encoding="utf-8")
    assert td.state() == "set" and not td.has_backup()
    assert "默认" in td.restore()
    d = _now(teams)
    assert d[td.MIC_KEY] == {} and d[td.SPEAKER_KEY] == {} and d[td.CHOSEN_KEY] == {}
    assert d["noise_suppression_mode"] == 2
    assert "不用改" in td.restore()


@pytest.mark.parametrize("content", ["not json", "[1, 2]", json.dumps({**BEFORE, td.MIC_KEY: "BlackHole"})])
def test_a_file_that_is_not_what_we_know_is_left_alone(teams, content):
    teams.write_text(content, encoding="utf-8")
    assert td.state() == "unknown"
    for change in (td.apply, td.restore):
        with pytest.raises(td.TeamsError):
            change()
    assert teams.read_text(encoding="utf-8") == content and not td.has_backup()


def test_no_teams_is_said_plainly(teams):
    teams.unlink()
    assert td.state() == "unknown"
    with pytest.raises(td.TeamsError, match="没找到 Teams 的设置"):
        td.apply()


def test_the_speaker_teams_is_pointed_at_is_made_when_it_was_never_made(monkeypatch):
    """A user had skipped that wizard step: Teams on the headphones gave no subtitles, Teams on
    BlackHole 16ch no sound — and the device to choose was not in Teams' list at all."""
    made = []
    monkeypatch.setattr(td.virtual_audio, "mac_multi_output_state", lambda: ("missing", []))
    monkeypatch.setattr(td.virtual_audio, "mac_create_multi_output",
                        lambda name: made.append(name) or "created: Teams 扬声器 (耳机+字幕) = 外置耳机 + BlackHole 16ch")
    assert "已用“外置耳机”创建" in td.prepare("外置耳机")
    td.prepare("BlackHole 16ch")  # the system speaker is a virtual line: never a member of itself
    assert made == ["外置耳机", "外置耳机"]
    monkeypatch.setattr(td.virtual_audio, "mac_create_multi_output", lambda name: "error: BlackHole 16ch not found")
    with pytest.raises(td.TeamsError, match="BlackHole 16ch"):
        td.prepare("外置耳机")
    monkeypatch.setattr(td.virtual_audio, "mac_multi_output_state", lambda: ("ok", ["外置耳机", "BlackHole 16ch"]))
    assert td.prepare("外置耳机") == ""  # it is there: nothing to do


@pytest.mark.parametrize("monitor,default,expect", [
    ("", "外置耳机", "外置耳机"),
    ("Bard的AirPods Pro", "外置耳机", "Bard的AirPods Pro"),
    ("", "MacBook Pro扬声器", "MacBook Pro扬声器"),  # no headphones in sight: subtitles still need the device
    ("", "BlackHole 16ch", ""),
    ("", "", ""),
])
def test_the_main_window_offers_to_make_the_missing_meeting_speaker(monitor, default, expect):
    target, why = dh.multi_output_fix([], monitor, default, missing=True)
    assert target == expect and (why == dh.MULTI_MISSING if expect else why == "")
