"""One click to point Microsoft Teams at the app's audio lines, and one to put it back.

Teams (the "new" one, com.microsoft.teams2) keeps the microphone and speaker chosen in 设置 → 设备
in a small JSON file, by the devices' system ids. Nothing documents it, so every step here checks
what it finds and changes only the three device entries; anything unexpected is an error with the
file left as it was, and the page still shows the steps to do by hand.

Teams reads the file when it starts and writes it while it runs, so it must be closed for a change
to hold: quit(), then apply() / restore(), then launch().

macOS only for now: on Windows neither the place of the file nor the form of its device ids has
been checked on a real machine.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from pathlib import Path

from . import virtual_audio
from .config import HOME, IS_MAC
from .device_hints import kind
from .virtual_audio import BLACKHOLE_2CH_UID, MULTI_OUTPUT_NAME, MULTI_OUTPUT_UID

BUNDLE_ID = "com.microsoft.teams2"
PROCESS = "Microsoft Teams.app/Contents/MacOS/MSTeams"
MIC_KEY, SPEAKER_KEY, CHOSEN_KEY = ("calling_selected_microphone_device", "calling_selected_speaker_device",
                                    "calling_user_selected_devices")
KEYS = (MIC_KEY, SPEAKER_KEY, CHOSEN_KEY)
BACKUP = HOME / "teams-devices-before.json"


class TeamsError(RuntimeError):
    """Said to the user as it is."""


def supported() -> bool:
    return IS_MAC


def settings_file() -> Path:
    override = os.environ.get("MIRRORVOICE_TEAMS_SETTINGS")
    if override:
        return Path(override)
    return (Path.home() / "Library/Containers" / BUNDLE_ID
            / "Data/Library/Application Support/Microsoft/MSTeams/cmd_settings.json")


def wanted() -> dict[str, dict[str, str]]:
    """Teams hears my translated voice as its microphone, and plays the meeting to my headphones
    and to the line the subtitles listen on."""
    return {MIC_KEY: {"Microphone": BLACKHOLE_2CH_UID}, SPEAKER_KEY: {"Speaker": MULTI_OUTPUT_UID},
            CHOSEN_KEY: {"Speaker": MULTI_OUTPUT_UID, "Microphone": BLACKHOLE_2CH_UID}}


# what Teams keeps when nothing was ever chosen: it follows the system's devices
DEFAULTS: dict[str, dict[str, str]] = {MIC_KEY: {}, SPEAKER_KEY: {}, CHOSEN_KEY: {}}


def _read() -> dict:
    f = settings_file()
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise TeamsError("没找到 Teams 的设置：请先安装并打开过一次 Teams（新版），再回来点这里。") from None
    except PermissionError:
        raise TeamsError("系统没有允许翻了么读取 Teams 的设置：系统设置 → 隐私与安全性 → App 管理（或“完全磁盘访问权限”）"
                         "里打开翻了么，再试一次。") from None
    except (OSError, ValueError) as e:
        raise TeamsError(f"Teams 的设置读不出来（{e}），请在 Teams 里手动设置。") from None
    if not isinstance(data, dict) or any(not isinstance(data.get(k, {}), dict) for k in KEYS):
        raise TeamsError("这个版本的 Teams 设置格式和预期不一样，没有改动它，请在 Teams 里手动设置。")
    return data


def _write(data: dict) -> None:
    f = settings_file()
    tmp = f.with_name(f.name + ".mirrorvoice")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, f)
    except PermissionError:
        raise TeamsError("系统没有允许翻了么修改 Teams 的设置：系统设置 → 隐私与安全性 → App 管理里打开翻了么，"
                         "再试一次。") from None
    except OSError as e:
        raise TeamsError(f"Teams 的设置写不进去（{e}），请在 Teams 里手动设置。") from None
    finally:
        tmp.unlink(missing_ok=True)


def _devices(data: dict) -> dict[str, dict[str, str]]:
    return {k: dict(data.get(k, {})) for k in KEYS}


def state() -> str:
    """'set' (Teams uses the app's lines) | 'other' | 'unknown' (no Teams, or its settings are unreadable)."""
    try:
        d = _devices(_read())
    except TeamsError:
        return "unknown"
    want = wanted()
    return "set" if d[MIC_KEY] == want[MIC_KEY] and d[SPEAKER_KEY] == want[SPEAKER_KEY] else "other"


def prepare(default_out: str) -> str:
    """Before apply(): the speaker Teams is pointed at must exist. Makes the multi-output when it
    was never made (the wizard step is easy to skip: then Teams on the headphones gave no
    subtitles, and Teams on BlackHole 16ch no sound). Returns a line to add to the answer."""
    state, _ = virtual_audio.mac_multi_output_state()
    if state != "missing":
        return ""
    listen = default_out if default_out and kind(default_out, "output") in ("headphones", "other", "speaker") \
        else "外置耳机"
    out = virtual_audio.mac_create_multi_output(listen)
    if not out.startswith(("created:", "exists:")):
        raise TeamsError(f"还没有“{MULTI_OUTPUT_NAME}”这个设备，创建它也没成功（{out or '没有回应'}）。"
                         "请先在“设置向导 → 声音线路”里装好 BlackHole 16ch。")
    return f"“{MULTI_OUTPUT_NAME}”原来不存在，已用“{listen}”创建。"


def has_backup() -> bool:
    return BACKUP.is_file()


def apply() -> str:
    """Point Teams at the app's lines, remembering what it had. Returns what to tell the user."""
    data = _read()
    now, want = _devices(data), wanted()
    if now[MIC_KEY] == want[MIC_KEY] and now[SPEAKER_KEY] == want[SPEAKER_KEY]:
        return "Teams 已经是这样设置的：麦克风 BlackHole 2ch，扬声器“Teams 扬声器 (耳机+字幕)”。"
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    BACKUP.write_text(json.dumps(now, ensure_ascii=False), encoding="utf-8")
    data.update(want)
    _write(data)
    return "已设置好：Teams 的麦克风是 BlackHole 2ch（对方听到你的翻译），扬声器是“Teams 扬声器 (耳机+字幕)”。"


def restore() -> str:
    """Put back what Teams had before apply(); with nothing remembered, its own default."""
    data = _read()
    before, how = DEFAULTS, "Teams 的麦克风和扬声器已改回默认（跟随系统）。"
    if has_backup():
        try:
            saved = json.loads(BACKUP.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and all(isinstance(saved.get(k), dict) for k in KEYS):
                before, how = {k: saved[k] for k in KEYS}, "Teams 的麦克风和扬声器已还原成一键设置之前的样子。"
        except (OSError, ValueError):
            pass
    if _devices(data) == before:
        BACKUP.unlink(missing_ok=True)
        return "Teams 现在就是还原后的样子，不用改。"
    data.update(before)
    _write(data)
    BACKUP.unlink(missing_ok=True)
    return how


# ---------------------------------------------------------------- the Teams app itself
def _pids() -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-f", PROCESS], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(p) for p in out.split() if p.isdigit()]


def running() -> bool:
    return bool(_pids())


def quit(wait_s: float = 12.0) -> bool:  # noqa: A001 — teams_devices.quit() reads well
    """Ask Teams to close (as ⌘Q does), without forcing it. False: it is still there."""
    for pid in _pids():
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    end = time.monotonic() + wait_s
    while time.monotonic() < end:
        if not running():
            return True
        time.sleep(0.3)
    return not running()


def launch() -> None:
    subprocess.run(["open", "-b", BUNDLE_ID], capture_output=True, timeout=15)
