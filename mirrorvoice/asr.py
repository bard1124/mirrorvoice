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


@dataclass
class Decoded:
    text: str
    lang: str
    tokens: list[str]
    times: list[float]  # seconds from the start of the audio, one per token


class Recognizer:
    def __init__(self, language: str = "auto") -> None:
        import sherpa_onnx

        self.language = language
        self.rec = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(ASR.path / "model.int8.onnx"), tokens=str(ASR.path / "tokens.txt"),
            num_threads=4, language=language, use_itn=True)

    def decode(self, samples: np.ndarray, sr: int = ASR_SR) -> Decoded:
        s = self.rec.create_stream()
        s.accept_waveform(sr, samples.astype(np.float32))
        self.rec.decode_stream(s)
        r = s.result
        return Decoded(r.text.strip(), sensevoice_lang_tag(r.lang), list(r.tokens), list(r.timestamps))

    def __call__(self, samples: np.ndarray, sr: int = ASR_SR) -> tuple[str, str]:
        d = self.decode(samples, sr)
        return d.text, d.lang


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


SENTENCE_END = set("。！？!?；;")
CLAUSE_END = set("，,、：:")


def punctuation_cut(d: Decoded, duration: float, min_head: float = 2.0, min_tail: float = 0.4) -> float:
    """Seconds into the audio just after the last sentence end (else the last comma) that leaves at
    least min_head before and min_tail after it; 0 when there is none."""
    best = {True: 0.0, False: 0.0}
    for tok, t in zip(d.tokens, d.times):
        tok = tok.strip()
        if tok and tok[-1] in SENTENCE_END | CLAUSE_END and min_head <= t <= duration - min_tail:
            best[tok[-1] in SENTENCE_END] = t
    return best[True] or best[False]


class Segmenter:
    """VAD -> sentence segments -> SenseVoice, fed with 16 kHz blocks; on_utt(Utterance) per sentence.

    Long sentences are cut early so translation can start before the speaker is done: after
    LONG_S of continuous speech a dip of DIP_S (energy well below the running speech level,
    shorter than the VAD's end-of-sentence silence) ends the segment; after HARD_S a shorter
    dip suffices; at MAX_S it is cut regardless. The VAD's own max_speech_duration stays as the
    backstop.

    An early cut lands wherever the voice dipped — often mid-clause (45 % of the other side's
    sentences in one meeting). So the cut segment is moved back to the last punctuation the
    recognizer heard in it, and the rest is carried into the next segment (or sent on its own when
    the speaker stops). The engine passes the previous part as context so the translation stays
    coherent.

    Language: decoded in "auto" first. A result in neither the expected language nor the other
    side's (Mandarin heard as Cantonese / Japanese) is decoded again in the expected language;
    the other side's language is kept, so passthrough still sees it.
    """

    WINDOW = 512
    LONG_S, DIP_S = 5.0, 0.30     # after 5 s of speech, a 0.3 s dip cuts
    HARD_S, HARD_DIP_S = 8.0, 0.15
    MAX_S = 10.0
    DIP_RATIO = 0.25              # "dip" = rms below a quarter of the running speech rms …
    NOISE_MULT = 2.0              # … or within 2x of the noise floor (quiet microphones: the floor
                                  #   can sit above a quarter of the speech level)
    HIST_S = 30

    def __init__(self, name: str, pause: float, on_utt: Callable[[Utterance], None],
                 lang: str = "", alt: str = "", log: Callable[[str], None] | None = None,
                 on_level: Callable[[str, float], None] | None = None, recognizer=None, vad=None) -> None:
        self.name, self.pause, self.on_utt = name, pause, on_utt
        self.lang, self.alt = lang, alt
        self.log = log or (lambda *_: None)
        self.on_level = on_level or (lambda *_: None)
        self.asr = recognizer or Recognizer("auto")
        self._fixed: Recognizer | None = None  # built on the first misheard language (~0.8 s, 230 MB)
        self.vad = vad or make_vad(pause)
        self.count = 0
        self.buf = np.zeros(0, dtype=np.float32)
        self.hist = np.zeros(0, dtype=np.float32)  # rolling ~30 s for re-cutting with lead-in
        self.hist_start = self.fed = 0
        self.pre, self.post = int(0.3 * ASR_SR), int(0.15 * ASR_SR)
        self.lvl_t, self.lvl_sq, self.lvl_n = time.time(), 0.0, 0
        self.speech_from = -1     # sample index where the current speech run started (-1 = none)
        self.speech_rms, self.dip_from = 0.0, -1
        self.noise_rms = 0.0      # running floor measured outside speech
        self.early_end = -1       # sample index of the last early cut (its segment gets re-cut)
        self.carry: tuple[int, int] | None = None  # (from, to) audio after the punctuation, not yet sent
        self.cut_to = -1          # end of the last early-cut segment: the next one must not re-include it

    # ---------------------------------------------------------------- recognition
    def recognize(self, seg: np.ndarray) -> Decoded:
        d = self.asr.decode(seg)
        if not self.lang or d.lang in (self.lang, self.alt) or not re.sub(r"[\W_]+", "", d.text):
            return d
        if self._fixed is None:
            self._fixed = Recognizer(self.lang)
        fixed = self._fixed.decode(seg)
        self.log(f"[{self.name}] 语言识别为 {d.lang}，按 {self.lang} 重新识别：{d.text!r} -> {fixed.text!r}")
        return Decoded(fixed.text, self.lang, fixed.tokens, fixed.times)

    def _emit(self, seg: np.ndarray, t_end: float, d: Decoded | None = None) -> None:
        d = d or self.recognize(seg)
        self.count += 1
        self.log(f"[{self.name}] segment {len(seg)/ASR_SR:.2f}s lang={d.lang} text={d.text!r}")
        if re.sub(r"[\W_]+", "", d.text):
            self.on_utt(Utterance(d.text, d.lang, seg, t_end, time.time() - t_end))

    def _abs(self, i: int) -> int:
        return min(max(i, self.hist_start), self.fed) - self.hist_start

    # ---------------------------------------------------------------- feeding
    def flush(self) -> None:
        """Close the current sentence now (push-to-talk released)."""
        self.vad.flush()
        self._pop()

    def feed(self, block: np.ndarray, open_gate: bool = True) -> None:
        vad, w = self.vad, self.WINDOW
        self.buf = np.concatenate([self.buf, block])
        while len(self.buf) >= w:
            chunk = self.buf[:w] if open_gate else np.zeros(w, np.float32)
            self.buf = self.buf[w:]
            vad.accept_waveform(chunk)
            self.hist = np.concatenate([self.hist, chunk])
            self.fed += w
            rms = float(np.sqrt((chunk * chunk).mean()))
            if vad.is_speech_detected():
                if self.speech_from < 0:
                    self.speech_from, self.speech_rms, self.dip_from = self.fed, rms, -1
                dur = (self.fed - self.speech_from) / ASR_SR
                if rms < max(self.DIP_RATIO * self.speech_rms, self.NOISE_MULT * self.noise_rms):
                    self.dip_from = self.fed if self.dip_from < 0 else self.dip_from
                else:
                    self.dip_from = -1
                    self.speech_rms = 0.9 * self.speech_rms + 0.1 * rms
                dip = (self.fed - self.dip_from) / ASR_SR if self.dip_from >= 0 else 0.0
                if (dur >= self.LONG_S and dip >= self.DIP_S) or (dur >= self.HARD_S and dip >= self.HARD_DIP_S) \
                        or dur >= self.MAX_S:
                    self.log(f"[{self.name}] 长句提前断句：已说 {dur:.1f}s，停顿 {dip:.2f}s")
                    vad.flush()
                    self.early_end = self.fed
                    self.speech_from, self.dip_from = -1, -1
            else:
                self.speech_from, self.dip_from = -1, -1
                self.noise_rms = rms if self.noise_rms == 0.0 else 0.95 * self.noise_rms + 0.05 * rms
            if len(self.hist) > self.HIST_S * ASR_SR:
                drop = len(self.hist) - self.HIST_S * ASR_SR
                self.hist, self.hist_start = self.hist[drop:], self.hist_start + drop
            self.lvl_sq += float((chunk * chunk).sum())
            self.lvl_n += w
        if time.time() - self.lvl_t > 0.1:
            self.on_level(self.name, (self.lvl_sq / max(self.lvl_n, 1)) ** 0.5)
            self.lvl_t, self.lvl_sq, self.lvl_n = time.time(), 0.0, 0
        self._pop()
        self._send_lone_tail()

    def _pop(self) -> None:
        vad = self.vad
        while not vad.empty():
            start, n = vad.front.start, len(vad.front.samples)
            vad.pop()
            a = max(start - self.pre, self.hist_start)
            if self.carry and self.hist_start <= self.carry[0] <= start:
                a = self.carry[0]  # the half sentence left after the last cut starts this one
            elif a < self.cut_to <= start + n:
                a = self.cut_to  # the lead-in would repeat the last words of the cut segment
            self.carry, self.cut_to = None, -1
            b = min(start + n + self.post, self.fed)
            seg = self.hist[self._abs(a):self._abs(b)] if b > a else np.zeros(0, np.float32)
            t_end = time.time()
            if self.early_end >= 0 and b >= self.early_end - self.WINDOW:
                self.early_end = -1
                if self._cut_at_punctuation(seg, a, b, t_end):
                    continue
            self._emit(seg, t_end)

    def _cut_at_punctuation(self, seg: np.ndarray, a: int, b: int, t_end: float) -> bool:
        d = self.recognize(seg)
        cut = punctuation_cut(d, len(seg) / ASR_SR)
        if not cut:
            self._emit(seg, t_end, d)
            self.cut_to = b
            return True
        at = a + int((cut + 0.05) * ASR_SR)
        head = self.hist[self._abs(a):self._abs(at)]
        self.log(f"[{self.name}] 断在标点：{cut:.2f}s / {len(seg) / ASR_SR:.2f}s")
        self._emit(head, t_end)
        self.carry = (at, b)
        return True

    def _send_lone_tail(self) -> None:
        """The speaker stopped right after an early cut: no next segment will pick the tail up."""
        if not self.carry or self.vad.is_speech_detected() or not self.vad.empty():
            return
        frm, to = self.carry
        if self.fed - to < int((self.pause + 0.2) * ASR_SR):
            return
        self.carry = None
        if frm >= self.hist_start:
            self._emit(self.hist[self._abs(frm):self._abs(to)], time.time())


class Listener:
    """Capture -> Segmenter in its own thread; calls on_utt(Utterance) per sentence.

    gate() -> False mutes the input (push-to-talk released / muted): zeros are fed so
    the current sentence closes cleanly, and nothing new is emitted.
    """

    WINDOW = Segmenter.WINDOW

    def __init__(self, name: str, source: str, language: str, pause: float,
                 on_utt: Callable[[Utterance], None],
                 on_level: Callable[[str, float], None] | None = None,
                 gate: Callable[[], bool] | None = None,
                 log: Callable[[str], None] | None = None, alt: str = "", online_key: str = "",
                 on_partial: Callable[[str], None] | None = None,
                 on_notice: Callable[[str], None] | None = None) -> None:
        """language: the language expected from this source ("auto" = any); alt: the other
        side's language, accepted as detected (passthrough / same-language subtitles).
        online_key: 阿里云百炼 key → streaming online recognition (online_asr.py) instead of
        SenseVoice, with partial text through on_partial."""
        self.name, self.on_utt = name, on_utt
        self.gate = gate or (lambda: True)
        self.log = log or (lambda *_: None)
        lang = "" if language == "auto" else language
        if online_key:
            from .online_asr import OnlineSegmenter

            self.seg = OnlineSegmenter(name, pause, on_utt, online_key, lang, alt, self.log, on_level,
                                       on_partial, on_notice, detector=Recognizer("auto"))
        else:
            self.seg = Segmenter(name, pause, on_utt, lang, alt, self.log, on_level)
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.source = source
        self.capture = Capture(source, self.q.put, self.WINDOW)
        self.label = self.capture.label
        self.stop_evt = threading.Event()
        self._flush = threading.Event()
        self._restart_at = 0.0
        self._hb = [time.time(), 0, 0.0, 0]  # window start, blocks, peak, segments

    def _heartbeat(self) -> None:
        """Every 10 s: is audio arriving, how loud, how many sentences — the first thing to read when
        'nothing happens' (the GUI has no console and the level bar is only a momentary hint)."""
        now = time.time()
        if now - self._hb[0] < 10:
            return
        t0, blocks, peak, segs = self._hb
        self.log(f"[{self.name}] 10s: 音频块={blocks} 峰值={peak:.3f} 片段={segs} 门开={self.gate()} {self.capture.health()}")
        self._hb = [now, 0, 0.0, 0]

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
        while not self.stop_evt.is_set():
            try:
                block = self.q.get(timeout=0.2)
            except queue.Empty:
                self._check_capture()
                self._heartbeat()
                self.seg.feed(np.zeros(0, np.float32), self.gate())  # lone tails still go out
                continue
            self._hb[1] += 1
            if len(block):
                self._hb[2] = max(self._hb[2], float(np.abs(block).max()))
            self._heartbeat()
            before = self.seg.count
            self.seg.feed(block, self.gate())
            if self._flush.is_set():
                self._flush.clear()
                self.seg.flush()
            self._hb[3] += self.seg.count - before
