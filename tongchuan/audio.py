"""Audio devices: callback-driven outputs, mic/virtual inputs, Windows loopback capture."""
from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import sounddevice as sd

from .config import IS_WIN

ASR_SR = 16000


def resample(x: np.ndarray, sr_in: int, sr_out: int) -> np.ndarray:
    if sr_in == sr_out or len(x) == 0:
        return x.astype(np.float32, copy=False)
    n = int(round(len(x) * sr_out / sr_in))
    return np.interp(np.linspace(0, len(x), n, endpoint=False), np.arange(len(x)), x).astype(np.float32)


def time_stretch(x: np.ndarray, sr: int, speed: float) -> np.ndarray:
    """Change duration by 1/speed without changing pitch (WSOLA). speed 0.8 -> 25 % longer.

    Used for voices whose models have no speaking-rate control (Pocket TTS, Qwen3-TTS): the clone
    otherwise inherits whatever pace the reference sample was read at.
    """
    if abs(speed - 1.0) < 0.02 or len(x) < sr // 10:
        return x
    win = int(0.025 * sr)
    hop_out = win // 2
    hop_in = int(round(hop_out * speed))
    tol = max(1, int(0.005 * sr))
    step = max(1, tol // 8)
    w = np.hanning(win).astype(np.float32)
    n_out = int(len(x) / speed) + 2 * win
    out = np.zeros(n_out, np.float32)
    norm = np.zeros(n_out, np.float32)
    nominal = pos_out = 0  # nominal input position advances at hop_in regardless of the match found
    target = None          # the frame that would naturally follow the last copied frame
    while nominal + win <= len(x) and pos_out + win <= n_out:
        pos = nominal
        if target is not None:  # align within ±tol of the nominal position (never move it)
            lo, hi = max(0, nominal - tol), min(len(x) - win, nominal + tol)
            best_c = -np.inf
            for cand in range(lo, hi + 1, step):
                c = float(np.dot(x[cand:cand + win], target))
                if c > best_c:
                    best_c, pos = c, cand
        out[pos_out:pos_out + win] += x[pos:pos + win] * w
        norm[pos_out:pos_out + win] += w
        nxt = pos + hop_out
        target = x[nxt:nxt + win] if nxt + win <= len(x) else None
        pos_out += hop_out
        nominal += hop_in
    end = min(pos_out + win, n_out)
    return (out[:end] / np.maximum(norm[:end], 1e-3)).astype(np.float32)


def list_devices(kind: str) -> list[str]:
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    names: list[str] = []
    hostapis = sd.query_hostapis()
    for d in sd.query_devices():
        # on Windows show each device once (WASAPI names), skip MME/DirectSound duplicates
        if IS_WIN and "WASAPI" not in hostapis[d["hostapi"]]["name"]:
            continue
        if d[key] > 0 and d["name"] not in names:
            names.append(d["name"])
    if kind == "input" and IS_WIN:
        names += [f"loopback:{n}" for n in list_loopback_speakers()]
    return names


def find_device(name: str | None, kind: str) -> int | None:
    """Resolve a device by (partial) name; ''/None -> system default; raises LookupError."""
    if not name:
        return None
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    hostapis = sd.query_hostapis()
    cands = [(i, d) for i, d in enumerate(sd.query_devices()) if d[key] > 0
             and name.lower() in d["name"].lower()]
    if IS_WIN:  # prefer WASAPI for low latency
        cands.sort(key=lambda c: "WASAPI" not in hostapis[c[1]["hostapi"]]["name"])
    if not cands:
        raise LookupError(f"找不到{'输入' if kind == 'input' else '输出'}设备：{name}")
    return cands[0][0]


def device_name(idx: int | None, kind: str) -> str:
    return sd.query_devices(idx, kind)["name"]


# ------------------------------------------------------------------ outputs
class DeviceOut:
    """One output device: callback stream fed from its own buffer (never blocks the producer).

    Blocking-mode writes to BlackHole produced silence (AUHAL -10863) and could stall forever.
    """

    def __init__(self, device: int | None, src_sr: int) -> None:
        info = sd.query_devices(device, "output")
        self.name = info["name"]
        self.sr = int(info["default_samplerate"])
        self.ch = min(2, int(info["max_output_channels"]))
        self.src_sr = src_sr
        self.buf = np.zeros(0, dtype=np.float32)
        self.lock = threading.Lock()
        self.stream = sd.OutputStream(samplerate=self.sr, channels=self.ch, dtype="float32",
                                      device=device, callback=self._cb)
        self.stream.start()

    def _cb(self, outdata, frames, t, status) -> None:
        with self.lock:
            n = min(frames, len(self.buf))
            out = self.buf[:n]
            self.buf = self.buf[n:]
        outdata[:n] = out[:, None]
        outdata[n:] = 0

    def put(self, chunk: np.ndarray, sr: int) -> None:
        y = resample(chunk, sr, self.sr)
        with self.lock:  # cap backlog at 60 s so a dead device can't grow memory forever
            self.buf = np.concatenate([self.buf, y])[-self.sr * 60:]

    def busy(self) -> bool:
        with self.lock:
            return len(self.buf) > 0

    def close(self) -> None:
        try:
            self.stream.abort()
            self.stream.close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


class Player:
    """Plays to the call device (virtual mic) and optionally a monitor device (headphones)."""

    def __init__(self, call_dev: int | None, monitor_dev: int | None, gain: float = 1.0) -> None:
        self.gain = gain
        self.outs = [DeviceOut(call_dev, 24000)]
        if monitor_dev is not None:
            self.outs.append(DeviceOut(monitor_dev, 24000))

    def put(self, chunk: np.ndarray, sr: int) -> None:
        chunk = np.clip(np.asarray(chunk, dtype=np.float32).reshape(-1) * self.gain, -1, 1)
        for o in self.outs:
            o.put(chunk, sr)

    def set_monitor(self, monitor_dev: int | None) -> None:
        """Replace the headphone monitor (outs[1:]) without touching the call output."""
        old, self.outs = self.outs[1:], self.outs[:1] + ([DeviceOut(monitor_dev, 24000)] if monitor_dev is not None else [])
        for o in old:
            o.close()

    def busy(self) -> bool:
        return any(o.busy() for o in self.outs)

    def close(self) -> None:
        for o in self.outs:
            o.close()


# ------------------------------------------------------------------ inputs
OnAudio = Callable[[np.ndarray], None]  # mono float32 @ 16 kHz


def open_input(device: int | None, want_sr: int, channels: int, callback, blocksize: int = 0
               ) -> tuple[sd.InputStream, int]:
    """InputStream at want_sr, else at the device's native rate (caller resamples).

    find_device prefers WASAPI, whose shared mode only accepts the device's own rate (48 kHz here):
    16 / 24 kHz fail with "Invalid sample rate [PaErrorCode -9997]". The MME default device
    resampled for us, which is why "系统默认" worked and a named microphone did not.
    """
    try:
        return sd.InputStream(samplerate=want_sr, channels=channels, dtype="float32", blocksize=blocksize,
                              device=device, callback=callback), want_sr
    except sd.PortAudioError:
        native = int(sd.query_devices(device, "input")["default_samplerate"])
        if native == want_sr:
            raise
        return sd.InputStream(samplerate=native, channels=channels, dtype="float32", blocksize=blocksize,
                              device=device, callback=callback), native


class Capture:
    """Mono 16 kHz capture from a device name; 'loopback:<speaker>' = Windows WASAPI loopback."""

    def __init__(self, name: str, on_audio: OnAudio, block: int = 512) -> None:
        self.on_audio, self.block = on_audio, block
        self.stop_evt = threading.Event()
        self.last_cb, self.overflows, self.error = 0.0, 0, None
        self.loopback = name.startswith("loopback:")
        if self.loopback:
            self.label = name
            self._spk = name.split(":", 1)[1]
        else:
            self.dev = find_device(name, "input")
            info = sd.query_devices(self.dev, "input")
            self.label = info["name"]
            self.channels = min(2, int(info["max_input_channels"]))

    def start(self) -> None:
        if self.loopback:
            threading.Thread(target=self._loopback_loop, daemon=True, name="loopback").start()
            return
        self.stream, self.sr = open_input(self.dev, ASR_SR, self.channels, self._cb, self.block)
        self.stream.start()
        self.last_cb = time.time()

    def _cb(self, indata, frames, t, status) -> None:
        self.last_cb = time.time()
        if status:
            self.overflows += 1
        try:
            self.on_audio(resample(indata.mean(axis=1).astype(np.float32), self.sr, ASR_SR))
        except Exception as e:  # noqa: BLE001 — an exception here would abort the PortAudio stream for good
            self.error = e

    def stalled(self, seconds: float = 3.0) -> bool:
        """No callback for `seconds` although the stream should be running (loopback has its own loop)."""
        return (not self.loopback and self.last_cb > 0 and time.time() - self.last_cb > seconds)

    def health(self) -> str:
        if self.loopback:
            return "loopback"
        st = getattr(self, "stream", None)
        return (f"active={st.active if st else None} stopped={st.stopped if st else None} "
                f"overflows={self.overflows} last_cb={time.time() - self.last_cb:.1f}s ago err={self.error!r}")

    def _loopback_loop(self) -> None:  # Windows only
        import soundcard as sc

        spk = sc.default_speaker() if self._spk in ("", "default") else sc.get_speaker(self._spk)
        mic = sc.get_microphone(id=str(spk.name), include_loopback=True)
        with mic.recorder(samplerate=48000, channels=2, blocksize=1536) as rec:
            while not self.stop_evt.is_set():
                data = rec.record(numframes=1536)
                self.on_audio(resample(data.mean(axis=1), 48000, ASR_SR))

    def close(self) -> None:
        self.stop_evt.set()
        if not self.loopback:
            try:
                self.stream.abort()
                self.stream.close()
            except Exception:  # noqa: BLE001
                pass


def list_loopback_speakers() -> list[str]:
    if not IS_WIN:
        return []
    try:
        import soundcard as sc

        return ["default"] + [s.name for s in sc.all_speakers()]
    except Exception:  # noqa: BLE001
        return ["default"]


def record_seconds(device: str, seconds: float, sr: int = 24000,
                   on_level: Callable[[float], None] | None = None,
                   stop: threading.Event | None = None) -> np.ndarray:
    """Blocking mono recording for voice enrollment: up to `seconds`, or until `stop` is set."""
    idx = find_device(device, "input") if device else None
    chunks: list[np.ndarray] = []

    def cb(indata, frames, t, status):
        x = indata[:, 0].copy()
        chunks.append(x)
        if on_level:
            on_level(float(np.sqrt((x * x).mean())))

    stream, got_sr = open_input(idx, sr, 1, cb)
    with stream:
        end = time.time() + seconds
        while time.time() < end and not (stop is not None and stop.is_set()):
            sd.sleep(50)
    return resample(np.concatenate(chunks), got_sr, sr) if chunks else np.zeros(0, np.float32)
