"""What each audio device is good for, per drop-down in the control panel.

The drop-downs list every device on the machine (other virtual cables, testing on headphones and
recording tools are legitimate), so instead of hiding anything each entry gets a short tag, the
recommended one is listed first, and choices that break the setup (echo, translating your own voice,
the other side hearing nothing) come with a warning. Pure functions: both platforms' device names are
handled here and unit-testable anywhere.

Roles: "mic" (my microphone), "call" (sent into the meeting), "monitor" (I listen), "source" (the other side).
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import IS_WIN
from .virtual_audio import MULTI_OUTPUT_NAME

RECOMMENDED, NORMAL, UNSUITED, CONFLICT = 0, 1, 2, 3

_VIRTUAL = ("blackhole", "vb-audio", "cable", "voicemeeter", "loopback audio", "soundflower", "krisp",
            "obs", "zoomaudio", "virtual", "虚拟")
_HEADPHONES = ("耳机", "耳麦", "headphone", "headset", "earphone", "earbud", "airpods", "buds", "beats")
_SPEAKERS = ("扬声器", "speaker", "喇叭", "display audio", "hdmi", "displayport", "monitor", "电视")
_MICS = ("麦克风", "microphone", "mic", "话筒", "阵列", "array")


@dataclass(frozen=True)
class Hint:
    rank: int        # RECOMMENDED < NORMAL < UNSUITED < CONFLICT
    tag: str = ""    # appended to the drop-down text
    warning: str = ""  # explanation shown when it is selected


@dataclass(frozen=True)
class Context:
    call_out: str = ""       # current "sent into the meeting" device
    their_source: str = ""   # current "other side" source ('loopback:<speaker>' on Windows)
    default_out: str = ""    # system default output (what 'loopback:default' records)


def _same(a: str, b: str) -> bool:
    a, b = a.strip().lower(), b.strip().lower()
    return bool(a and b) and (a == b or a in b or b in a)


def kind(name: str, direction: str) -> str:
    """bh2 | bh16 | multi | cable_in | cable_out | teams | loopback | virtual | headphones | speaker | mic | other."""
    n = name.lower()
    if n.startswith("loopback:"):
        return "loopback"
    if n == MULTI_OUTPUT_NAME.lower():
        return "multi"
    if "blackhole 2ch" in n:
        return "bh2"
    if "blackhole 16ch" in n:
        return "bh16"
    if "cable input" in n:
        return "cable_in"
    if "cable output" in n:
        return "cable_out"
    if "teams" in n:
        return "teams"
    if any(k in n for k in _VIRTUAL):
        return "virtual"
    if direction == "input":
        return "mic" if any(k in n for k in _MICS + _HEADPHONES) else "other"
    if any(k in n for k in _HEADPHONES):
        return "headphones"
    if any(k in n for k in _SPEAKERS):
        return "speaker"
    return "other"


def loopback_target(their_source: str, default_out: str) -> str:
    """The speaker a 'loopback:<speaker>' source records ('' when the source is not a loopback)."""
    if not their_source.startswith("loopback:"):
        return ""
    spk = their_source.split(":", 1)[1]
    return default_out if spk in ("", "default") else spk


def feeds_source(name: str, ctx: Context) -> bool:
    """Sound played into `name` ends up in the other side's source (= gets subtitled as if they said it)."""
    k, src = kind(name, "output"), ctx.their_source
    if not src:
        return False
    if kind(src, "input") == "bh16" and k in ("bh16", "multi"):
        return True
    if k == "cable_in" and kind(src, "input") == "cable_out":
        return True
    if k == "bh2" and kind(src, "input") == "bh2":
        return True
    target = loopback_target(src, ctx.default_out)
    return bool(target) and _same(name, target)


TEAMS_AUDIO = ("Microsoft Teams Audio 是 Teams 自带的虚拟声卡，只在共享屏幕并勾选“包含电脑声音”时用来传电脑声音，"
               "平时放进去对方听不到。")
ECHO_SUBTITLE = "放进这里的声音会进入“对方声音来源”，你的翻译会被当成对方说的话再翻译成字幕（回声）。"


def _virtual_mic() -> str:
    return "CABLE Input" if IS_WIN else "BlackHole 2ch"


def _their_line() -> str:
    return "“扬声器回采：系统默认扬声器”" if IS_WIN else "BlackHole 16ch"


def hint(role: str, name: str, ctx: Context) -> Hint:
    if not name:  # the special first entry: 系统默认 / 不监听 / 不显示字幕
        return Hint(NORMAL)
    direction = "input" if role in ("mic", "source") else "output"
    k = kind(name, direction)
    return {"mic": _mic, "call": _call, "monitor": _monitor, "source": _source}[role](name, k, ctx)


def _call(name: str, k: str, ctx: Context) -> Hint:
    if feeds_source(name, ctx):
        return Hint(CONFLICT, "⚠️ 会造成回声", "选这个对方听不到你的翻译，而且" + ECHO_SUBTITLE +
                    "\n\n请改选 " + _virtual_mic() + "，并在会议软件里把麦克风设为它。")
    if k in ("bh2", "cable_in"):
        return Hint(RECOMMENDED, "推荐")
    if k == "teams":
        return Hint(UNSUITED, "Teams 共享屏幕用", TEAMS_AUDIO + "\n\n请改选 " + _virtual_mic() + "，并在 Teams 里把麦克风设为它。")
    if k == "bh16":
        return Hint(UNSUITED, "对方字幕专用", "BlackHole 16ch 是“对方声音 → 字幕”的线路。会议软件的麦克风请用 BlackHole 2ch。")
    if k == "multi":
        return Hint(UNSUITED, "会议扬声器用", "这是给会议软件当扬声器用的多输出设备，不是麦克风。请改选 BlackHole 2ch。")
    if k == "virtual":
        return Hint(NORMAL, "其他虚拟声卡", "")
    return Hint(UNSUITED, "仅自己能听到", "选这个对方听不到你的翻译，只适合自己试听效果。开会时请改回 " + _virtual_mic() + "。")


def _monitor(name: str, k: str, ctx: Context) -> Hint:
    if ctx.call_out and _same(name, ctx.call_out):
        return Hint(CONFLICT, "⚠️ 与送进会议相同", "“我自己监听”和“送进会议”选了同一个设备，翻译会被播放两遍，对方听到重叠的声音。"
                    "监听请选耳机，或选“不监听”。")
    if k in ("bh16", "multi") and feeds_source(name, ctx):
        return Hint(CONFLICT, "⚠️ 会造成回声", ECHO_SUBTITLE + "监听请选耳机。")
    if k == "headphones":
        return Hint(RECOMMENDED, "推荐")
    if k == "speaker":
        return Hint(NORMAL, "外放，建议用耳机", "")
    if k in ("bh2", "cable_in"):
        return Hint(UNSUITED, "虚拟麦克风，听不到", "这是虚拟麦克风，放进去的声音会进入会议，你自己听不到。监听请选耳机。")
    if k in ("teams", "bh16", "multi", "virtual"):
        return Hint(UNSUITED, "虚拟声卡，听不到", "这是虚拟声卡，你自己听不到。监听请选耳机。")
    return Hint(NORMAL)


def _source(name: str, k: str, ctx: Context) -> Hint:
    if k in ("bh2", "cable_out"):
        return Hint(CONFLICT, "⚠️ 这是你的翻译线路", "这里传的是你自己的翻译语音，选它会把你的翻译当成对方说的话再翻一遍。"
                    "对方声音来源请选 " + _their_line() + "。")
    if k == "bh16":
        return Hint(RECOMMENDED, "推荐")
    if k == "loopback":
        target = loopback_target(name, ctx.default_out)
        if ctx.call_out and _same(target, ctx.call_out):
            return Hint(CONFLICT, "⚠️ 会造成回声", "回采的扬声器正是“送进会议”的设备，你的翻译会被当成对方的话。"
                        "请检查系统默认扬声器是不是被设成了 CABLE Input。")
        return Hint(RECOMMENDED, "推荐") if name == "loopback:default" else Hint(NORMAL)
    if k == "mic":
        return Hint(UNSUITED, "麦克风，会收到你自己", "这是麦克风，会把你自己说的话当成对方的话翻译成字幕。对方声音来源请选 "
                    + _their_line() + "。")
    if k == "teams":
        return Hint(UNSUITED, "不推荐", TEAMS_AUDIO)
    if k == "virtual":
        return Hint(NORMAL, "其他虚拟声卡", "")
    return Hint(NORMAL)


def _mic(name: str, k: str, ctx: Context) -> Hint:
    if k in ("bh2", "cable_out"):
        return Hint(CONFLICT, "⚠️ 这是你的翻译线路", "这里传的是你自己的翻译语音，选它会把翻译再翻译一遍（死循环）。请选你的真实麦克风。")
    if k in ("bh16", "teams", "virtual", "loopback"):
        return Hint(UNSUITED, "虚拟声卡，不是麦克风", "这是虚拟声卡，收到的是电脑或会议里的声音，不是你说的话。请选你的真实麦克风。")
    return Hint(NORMAL)


def label(name: str, h: Hint) -> str:
    shown = ("扬声器回采：" + name.split(":", 1)[1]) if name.startswith("loopback:") else name
    if name == "loopback:default":
        shown = "扬声器回采：系统默认扬声器"
    return f"{shown}（{h.tag}）" if h.tag else shown


def ordered(role: str, names: list[str], ctx: Context) -> list[str]:
    """Recommended first, then normal, unsuited and conflicting; original order within each group."""
    return sorted(names, key=lambda n: min(hint(role, n, ctx).rank, UNSUITED))


def multi_output_fix(members: list[str], monitor: str, default_out: str) -> tuple[str, str]:
    """macOS: (headphones to rebuild the meeting-speaker multi-output with, why) — ('', '') when fine.

    The multi-output sends meeting audio to fixed headphones + BlackHole 16ch. After switching
    headphones (AirPods instead of the wired ones) you would no longer hear the other side.
    `members` comes from `multi_output --members` ('missing:<uid>' for a disconnected device).
    """
    if not members:
        return "", ""
    listen = [m for m in members if "blackhole" not in m.lower()]
    gone = [m for m in listen if m.startswith("missing:")]
    present = [m for m in listen if not m.startswith("missing:")]

    def physical(name: str) -> bool:  # headphones (or an unknown real device such as "Bose QC45"),
        return bool(name) and kind(name, "output") in ("headphones", "other")  # never loudspeakers: echo

    if physical(monitor) and not any(_same(monitor, m) for m in present):
        now = f"“{present[0]}”" if present else "已断开的设备"
        return monitor, (f"会议里对方的声音现在送到{now}，但你监听用的是“{monitor}”，开会时会听不到对方。")
    if (gone or not present) and physical(default_out):
        return default_out, "会议扬声器里的耳机已经断开，开会时会听不到对方。"
    return "", ""
