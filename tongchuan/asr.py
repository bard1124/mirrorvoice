"""SenseVoice recognition + Silero VAD sentence segmentation (sherpa-onnx, CPU, both OSes)."""
from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .audio import ASR_SR, Capture
from .languages import sensevoice_lang_tag
from .models import ASR, VAD


@dataclass
class Utterance:
    text: str
    lang: str          # detected language code ('' if unknown)
    audio: np.ndarray  # 16 kHz mono, padded with 0.3 s lead-in
    t_end: float       # wall time the sentence was cut
    asr_s: float


class Recognizer:
    def __init__(self, language: str = "auto") -> None:
        import sherpa_onnx

        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(ASR.path / "model.int8.onnx"), tokens=str(ASR.path / "tokens.txt"),
            num_threads=4, language=language, use_itn=True)

    def __call__(self, samples: np.ndarray, sr: int = ASR_SR) -> tuple[str, str]:
        s = self.rec.create_stream()
        s.accept_waveform(sr, samples.astype(np.float32))
        self.rec.decode_stream(s)
        return s.result.text.strip(), sensevoice_lang_tag(s.result.lang)


def make_vad(pause: float):
    import sherpa_onnx

    cfg = sherpa_onnx.VadModelConfig()
    cfg.silero_vad.model = str(VAD.path)
    cfg.silero_vad.threshold = 0.5
    cfg.silero_vad.min_silence_duration = pause
    cfg.silero_vad.min_speech_duration = 0.1  # 0.3 dropped short words (你好/对/是的)
    cfg.silero_vad.max_speech_duration = 15
    cfg.sample_rate = ASR_SR
    return sherpa_onnx.VoiceActivityDetector(cfg, buffer_size_in_seconds=60)


class Listener:
    """Capture -> VAD -> ASR in its own thread; calls on_utt(Utterance) per sentence.

    gate() -> False mutes the input (push-to-talk released / muted): zeros are fed so
    the current sentence closes cleanly, and nothing new is emitted.
    """

    WINDOW = 512

    def __init__(self, name: str, source: str, language: str, pause: float,
                 on_utt: Callable[[Utterance], None],
                 on_level: Callable[[str, float], None] | None = None,
                 gate: Callable[[], bool] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.name, self.on_utt = name, on_utt
        self.on_level = on_level or (lambda *_: None)
        self.gate = gate or (lambda: True)
        self.log = log or (lambda *_: None)
        self.asr = Recognizer(language)
        self.vad = make_vad(pause)
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.source = source
        self.capture = Capture(source, self.q.put, self.WINDOW)
        self.label = self.capture.label
        self.stop_evt = threading.Event()
        self._flush = threading.Event()
        self._restart_at = 0.0

    def start(self) -> None:
        self.capture.start()
        threading.Thread(target=self._loop, daemon=True, name=f"listen-{self.name}").start()

    def flush(self) -> None:
        """Close the current sentence now (e.g. push-to-talk key released)."""
        self._flush.set()

    def close(self) -> None:
        self.stop_evt.set()
        self.capture.close()

    def _check_capture(self) -> None:
        """Reopen the input stream when PortAudio silently stopped calling back (seen after a few
        sentences on Windows: the listener sat idle with no audio and no error anywhere)."""
        if not self.capture.stalled() or time.time() < self._restart_at:
            return
        self._restart_at = time.time() + 5
        self.log(f"[{self.name}] 输入流 3 s 无数据（{self.capture.health()}），重新打开")
        try:
            self.capture.close()
            self.capture = Capture(self.source, self.q.put, self.WINDOW)
            self.capture.start()
        except Exception as e:  # noqa: BLE001 — keep trying every 5 s
            self.log(f"[{self.name}] 重新打开输入流失败：{e}")

    def _loop(self) -> None:
        vad, w = self.vad, self.WINDOW
        buf = np.zeros(0, dtype=np.float32)
        hist = np.zeros(0, dtype=np.float32)  # rolling ~30 s for re-cutting with lead-in
        hist_start = fed = 0
        pre, post = int(0.3 * ASR_SR), int(0.15 * ASR_SR)
        lvl_t, lvl_sq, lvl_n = time.time(), 0.0, 0
        while not self.stop_evt.is_set():
            try:
                buf = np.concatenate([buf, self.q.get(timeout=0.2)])
            except queue.Empty:
                self._check_capture()
                continue
            open_gate = self.gate()
            while len(buf) >= w:
                chunk = buf[:w] if open_gate else np.zeros(w, np.float32)
                buf = buf[w:]
                vad.accept_waveform(chunk)
                hist = np.concatenate([hist, chunk])
                fed += w
                if len(hist) > 30 * ASR_SR:
                    drop = len(hist) - 30 * ASR_SR
                    hist, hist_start = hist[drop:], hist_start + drop
                lvl_sq += float((chunk * chunk).sum())
                lvl_n += w
            if time.time() - lvl_t > 0.1:
                self.on_level(self.name, (lvl_sq / max(lvl_n, 1)) ** 0.5)
                lvl_t, lvl_sq, lvl_n = time.time(), 0.0, 0
            if self._flush.is_set():
                self._flush.clear()
                vad.flush()
            while not vad.empty():
                start, n = vad.front.start, len(vad.front.samples)
                vad.pop()
                a = max(start - pre, hist_start) - hist_start
                b = min(start + n + post, fed) - hist_start
                seg = hist[a:b] if b > a else np.zeros(0, np.float32)
                t_end = time.time()
                text, lang = self.asr(seg)
                self.log(f"[{self.name}] segment {len(seg)/ASR_SR:.2f}s lang={lang} text={text!r}")
                if re.sub(r"[\W_]+", "", text):
                    self.on_utt(Utterance(text, lang, seg, t_end, time.time() - t_end))
