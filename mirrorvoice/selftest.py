"""One-click check of the whole audio routing, without a meeting.

A short sweep is played into a device and listened for on the device that should receive it:
  送进会议      call_out (BlackHole 2ch / CABLE Input)  ->  its input side (BlackHole 2ch / CABLE Output),
                which is exactly what the meeting app records as its microphone
  对方声音      macOS: the meeting speaker device (Teams 扬声器 (耳机+字幕))  ->  BlackHole 16ch
                Windows: the speaker  ->  its WASAPI loopback ('loopback:default')
  我的麦克风    the level while the user says something
  我自己监听    a short tone the user should hear (cannot be verified automatically)
Detection is a normalised cross-correlation, so background noise does not fake a pass; the delay of
the routing itself is measured on the way. Must run while translation is stopped.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from . import device_hints as dh
from .config import IS_MAC, Settings
from .virtual_audio import MULTI_OUTPUT_NAME

SR = 16000
PASS_SCORE = 0.35  # normalised correlation; silence/noise stays well below, a clean loop is ~0.8-1.0


@dataclass
class Check:
    key: str
    title: str
    ok: bool | None          # None = informational / skipped
    detail: str
    delay_s: float | None = None


def probe_signal(sr: int = SR, dur: float = 0.5, f0: float = 600.0, f1: float = 2600.0) -> np.ndarray:
    """Linear sweep with soft edges: easy to find by correlation, pleasant enough to hear."""
    t = np.arange(int(sr * dur)) / sr
    phase = 2 * np.pi * (f0 * t + (f1 - f0) / (2 * dur) * t * t)
    env = np.minimum(1.0, np.minimum(t, dur - t) / 0.03)
    return (np.sin(phase) * env).astype(np.float32)


def find_probe(rec: np.ndarray, ref: np.ndarray) -> tuple[float, int]:
    """(score 0..1, sample offset of the best match) — score is the correlation normalised by the
    energy of the reference and of the matching window in the recording."""
    rec = np.asarray(rec, dtype=np.float64).reshape(-1)
    ref = np.asarray(ref, dtype=np.float64).reshape(-1)
    if len(rec) < len(ref) or not np.any(rec):
        return 0.0, 0
    n = 1 << int(np.ceil(np.log2(len(rec) + len(ref))))
    corr = np.fft.irfft(np.fft.rfft(rec, n) * np.conj(np.fft.rfft(ref, n)), n)[: len(rec) - len(ref) + 1]
    win = np.sqrt(np.convolve(rec * rec, np.ones(len(ref)), "valid"))
    denom = win * np.linalg.norm(ref)
    score = np.where(denom > 1e-9, corr / np.maximum(denom, 1e-9), 0.0)
    i = int(np.argmax(score))
    return float(score[i]), i


# ------------------------------------------------------------------ planning (pure, unit-tested)


def call_return_input(call_out: str, inputs: list[str]) -> str:
    """The input a meeting app would pick to hear what we play into `call_out` ('' = unknown)."""
    k = dh.kind(call_out, "output")
    want = {"bh2": "blackhole 2ch", "cable_in": "cable output"}.get(k)
    if want is None and k == "virtual":
        want = call_out.lower()
    if want is None:
        return ""
    return next((n for n in inputs if want in n.lower()), "")


def their_route(their_source: str, outputs: list[str], default_out: str) -> tuple[str, str]:
    """(device to play into, why) for the other side's line, so the sweep takes the meeting audio's path."""
    if their_source.startswith("loopback:"):
        target = dh.loopback_target(their_source, default_out)
        return next((o for o in outputs if dh._same(o, target)), target), "扬声器"
    if dh.kind(their_source, "input") == "bh16":
        multi = next((o for o in outputs if o == MULTI_OUTPUT_NAME), "")
        if multi:
            return multi, "会议扬声器"
        return next((o for o in outputs if "blackhole 16ch" in o.lower()), ""), "BlackHole 16ch"
    return "", ""


# ------------------------------------------------------------------ running


def _duplex_loop(dev: int, gain: float, listen_s: float) -> tuple[float, float | None]:
    """Same device for output and input (BlackHole 2ch -> BlackHole 2ch): one duplex stream, because
    macOS refuses a second, separate stream on that device (AUHAL -10863)."""
    import sounddevice as sd

    from .audio import resample

    info = sd.query_devices(dev)
    sr = int(info["default_samplerate"])
    probe = resample(probe_signal(), SR, sr) * gain
    lead = int(sr * 0.3)
    play = np.concatenate([np.zeros(lead, np.float32), probe, np.zeros(int(sr * listen_s), np.float32)])
    rec, pos = [], [0]
    in_ch, out_ch = min(2, int(info["max_input_channels"])), min(2, int(info["max_output_channels"]))

    def cb(indata, outdata, frames, t, status) -> None:
        rec.append(indata.mean(axis=1).copy())
        chunk = play[pos[0]:pos[0] + frames]
        outdata[:len(chunk)] = chunk[:, None]
        outdata[len(chunk):] = 0
        pos[0] += frames

    with sd.Stream(device=(dev, dev), samplerate=sr, channels=(in_ch, out_ch), dtype="float32", callback=cb):
        time.sleep(len(play) / sr + 0.2)
    got = resample(np.concatenate(rec).astype(np.float32), sr, SR) if rec else np.zeros(0, np.float32)
    score, at = find_probe(got, probe_signal())
    return score, (max(0.0, at / SR - lead / sr) if score >= PASS_SCORE else None)


def _loop(out_name: str, in_name: str, gain: float = 0.5, listen_s: float = 2.0) -> tuple[float, float | None]:
    """Play the sweep into `out_name` while recording `in_name`; (score, routing delay in s)."""
    from .audio import Capture, DeviceOut, find_device

    if not in_name.startswith("loopback:"):
        out_dev, in_dev = find_device(out_name, "output"), find_device(in_name, "input")
        if out_dev == in_dev:
            return _duplex_loop(out_dev, gain, listen_s)
    chunks: list[np.ndarray] = []
    cap = Capture(in_name, chunks.append)
    cap.start()
    out = None
    try:
        time.sleep(0.4)  # let the input stream settle so the sweep is not cut
        t_rec = time.time() - sum(len(c) for c in chunks) / SR
        out = DeviceOut(find_device(out_name, "output"), SR)
        probe = probe_signal()
        t_play = time.time()
        out.put(probe * gain, SR)
        time.sleep(listen_s)
    finally:
        cap.close()
        if out is not None:
            out.close()
    rec = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    score, at = find_probe(rec, probe_signal())
    delay = at / SR - (t_play - t_rec)
    return score, (max(0.0, delay) if score >= PASS_SCORE else None)


def check_call(s: Settings, inputs: list[str]) -> Check:
    title = "送进会议（对方能不能听到你的翻译）"
    back = call_return_input(s.call_out, inputs)
    if not back:
        return Check("call", title, None, f"“{s.call_out}”不是虚拟麦克风，无法自动检测：这样设置对方听不到你的翻译。")
    try:
        score, delay = _loop(s.call_out, back)
    except Exception as e:  # noqa: BLE001
        return Check("call", title, False, f"打开设备失败：{e}")
    if score >= PASS_SCORE:
        return Check("call", title, True, f"会议软件把麦克风设为“{back}”就能收到你的翻译。", delay)
    return Check("call", title, False, f"放进“{s.call_out}”的测试声没有从“{back}”收到。请重新运行设置向导的“虚拟声卡”一步，"
                 "或在声音设置里确认该设备没有被静音。")


def check_their(s: Settings, outputs: list[str], default_out: str) -> Check:
    title = "对方声音（能不能显示对方的字幕）"
    if not (s.subtitles and s.their_source):
        return Check("them", title, None, "没有开启对方字幕，已跳过。")
    play, via = their_route(s.their_source, outputs, default_out)
    if not play:
        return Check("them", title, None, f"无法为“{s.their_source}”自动检测。")
    try:
        score, delay = _loop(play, s.their_source, gain=0.25)
    except Exception as e:  # noqa: BLE001
        return Check("them", title, False, f"打开设备失败：{e}")
    if score >= PASS_SCORE:
        extra = ""
        if IS_MAC and play != MULTI_OUTPUT_NAME:
            extra = f"但没找到“{MULTI_OUTPUT_NAME}”，请在设置向导里创建，并在会议软件里把扬声器设为它。"
        speaker = (f"会议软件的扬声器设为“{play}”时字幕能正常工作。" if via == "会议扬声器"
                   else "对方的声音能被收到。")
        return Check("them", title, not extra, speaker + extra, delay)
    hint = (f"请在设置向导里重新创建“{MULTI_OUTPUT_NAME}”。" if IS_MAC
            else "请确认会议软件的扬声器就是系统默认扬声器。")
    return Check("them", title, False, f"播放到“{play}”的测试声没有从“{s.their_source}”收到。" + hint)


def check_mic(s: Settings, seconds: float = 3.0) -> Check:
    from .audio import Capture

    title = "我的麦克风"
    chunks: list[np.ndarray] = []
    try:
        cap = Capture(s.mic or "", chunks.append) if s.mic else _default_mic(chunks.append)
        cap.start()
        time.sleep(seconds)
        cap.close()
    except Exception as e:  # noqa: BLE001
        return Check("mic", title, False, f"打开麦克风失败：{e}（请检查麦克风权限）")
    rec = np.concatenate(chunks) if chunks else np.zeros(0, np.float32)
    peak = float(np.abs(rec).max()) if len(rec) else 0.0
    if peak < 0.02:
        return Check("mic", title, False, "几乎没收到声音：请检查麦克风权限、输入音量，或换一个麦克风。")
    if peak > 0.98:
        return Check("mic", title, True, "收到声音，但音量偏大有削波，建议调低输入音量。")
    return Check("mic", title, True, f"收到声音（峰值 {20 * np.log10(peak):.0f} dB）。")


def _default_mic(on_audio):
    import sounddevice as sd

    from .audio import Capture

    return Capture(sd.query_devices(kind="input")["name"], on_audio)


def check_monitor(s: Settings) -> Check:
    from .audio import DeviceOut, find_device

    title = "我自己监听"
    if not s.monitor:
        return Check("monitor", title, None, "没有开启监听，已跳过。")
    try:
        out = DeviceOut(find_device(s.monitor, "output"), SR)
        out.put(probe_signal() * 0.3, SR)
        time.sleep(0.9)
        out.close()
    except Exception as e:  # noqa: BLE001
        return Check("monitor", title, False, f"打开“{s.monitor}”失败：{e}")
    return Check("monitor", title, None, f"刚才在“{s.monitor}”播放了一声提示音，听到了就说明监听正常。")


def run_all(s: Settings, step: Callable[[str], None] = lambda _: None) -> list[Check]:
    """Every check in order; `step(text)` reports what is happening (e.g. "请说一句话…")."""
    from .audio import default_output_name, list_devices

    ins, outs = list_devices("input"), list_devices("output")
    results = []
    step("检测“送进会议”线路…")
    results.append(check_call(s, ins))
    step("检测“对方声音”线路…")
    results.append(check_their(s, outs, default_output_name()))
    step("请对着麦克风说一句话（3 秒）…")
    results.append(check_mic(s))
    step("在监听设备上播放提示音…")
    results.append(check_monitor(s))
    return results
