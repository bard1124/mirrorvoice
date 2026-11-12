"""Where to set the microphone / speaker in common meeting apps (shown in 会议软件设置 and the README)."""
from __future__ import annotations

from .config import IS_MAC
from .virtual_audio import MULTI_OUTPUT_NAME

APPS = ("Teams", "Zoom", "腾讯会议", "飞书")

_PATH = {
    "Teams": "右上角“…” → 设置 → 设备",
    "Zoom": "右上角头像 → 设置 → 音频",
    "腾讯会议": "右上角头像 → 设置 → 音频",
    "飞书": "头像 → 设置 → 音视频（会议中：麦克风按钮旁的“^”）",
}
_NOISE = {
    "Teams": "“噪音抑制”选“低”或“关闭”",
    "Zoom": "“背景噪音抑制”选“低”，并取消“自动调节麦克风音量”",
    "腾讯会议": "关闭“音频降噪”（或选“低”），关闭“自动调节麦克风音量”",
    "飞书": "关闭“智能降噪”",
}


def mic_name(mac: bool = IS_MAC) -> str:
    return "BlackHole 2ch" if mac else "CABLE Output (VB-Audio Virtual Cable)"


def speaker_name(mac: bool = IS_MAC) -> str:
    return MULTI_OUTPUT_NAME if mac else "你的耳机（系统默认扬声器）"


def steps(app: str, mac: bool = IS_MAC) -> list[str]:
    s = [f"打开 {app}：{_PATH[app]}",
         f"麦克风：选“{mic_name(mac)}”（对方听到的是你的翻译）",
         f"扬声器：选“{speaker_name(mac)}”" + ("（名字带 Teams，其他会议软件也用它）" if mac and app != "Teams" else ""),
         f"降噪：{_NOISE[app]}，否则合成的翻译语音可能被当成噪音削弱"]
    if not mac:
        s.append("Windows 的系统默认扬声器保持为你的耳机，不要设成 CABLE Input")
    s.append("戴耳机；先在翻了么点“开始”，再进会议")
    return s


def guide_text(mac: bool = IS_MAC) -> str:
    out = []
    for app in APPS:
        out.append(f"{app}\n" + "\n".join(f"  {i}. {t}" for i, t in enumerate(steps(app, mac), 1)))
    return "\n\n".join(out)
