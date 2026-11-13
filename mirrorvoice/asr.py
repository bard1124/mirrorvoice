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
    guess: int = 0     # a guess made at a short pause (Segmenter.on_guess), or the guess this sentence confirms


def _plain(text: str) -> str:
    """The words alone: no punctuation or spaces."""
    return re.sub(r"[\W_]+", "", text)


CJK_LANGS = {"zh", "yue", "ja", "ko"}


def reads_as_latin(text: str) -> bool:
    """Words in Latin letters with (next to) no Chinese among them: speech in a Western language
    that came out labelled Chinese ('呃 for eightight one one呃sevenf', 'using the。', '0686. Yeah,
    one second.'). Chinese with a term or two of English in it stays Chinese ('但这个现在叫 job
    document', 'bug他这个bug跟我跟他')."""
    words = len(re.findall(r"[A-Za-z][A-Za-z']*", text))
    cjk = len(re.findall(r"[\u3400-\u9fff]", text))
    return (cjk == 0 and words >= 1) or (words >= 3 and cjk <= 4)


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


QWEN_LANG = {"zh": "Chinese", "en": "English", "ja": "Japanese", "ko": "Korean", "yue": "Cantonese"}


class QwenASR:
    """Qwen3-ASR (mlx-audio) for the words of a sentence; SenseVoice still finds cut points and the
    language. One instance is shared by both listeners, one sentence at a time."""

    def __init__(self, path) -> None:
        from mlx_audio.stt import load

        self.model = load(str(path))
        self.lock = threading.Lock()
        self.hotwords: list[str] = []
        self.transcribe(np.zeros(ASR_SR // 2, np.float32), "zh")  # warm-up: the first call compiles kernels

    def transcribe(self, seg: np.ndarray, lang: str) -> str:
        with self.lock:
            r = self.model.generate(seg.astype(np.float32), language=QWEN_LANG.get(lang),
                                    hotwords=self.hotwords or None)
        return (r.text or "").strip()


def glossary_hotwords(rows) -> list[str]:
    """Product names, people and terms from the glossary, both wordings, for the recognizer."""
    words = []
    for row in rows:
        for w in row[:2]:
            w = w.strip()
            if w and len(w) <= 40 and w not in words:
                words.append(w)
    return words[:100]


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


def live_cuts(d: Decoded, duration: float, min_head: float = 1.0, min_tail: float = 0.25,
              min_chars: int = 4) -> list[tuple[float, str]]:
    """While someone is still talking: every (seconds, text-so-far) ending at punctuation that
    already has speech after it (so the recognizer did not just put it at the end of what it
    heard), with enough words before it to translate on their own."""
    out: list[tuple[float, str]] = []
    for i, (tok, t) in enumerate(zip(d.tokens, d.times)):
        tok = tok.strip()
        if not tok or tok[-1] not in SENTENCE_END | CLAUSE_END or not min_head <= t <= duration - min_tail:
            continue
        head = "".join(d.tokens[:i + 1]).replace("\u2581", " ").strip()
        words = re.sub(r"[\W_]+", " ", head).split()
        cjk = len(re.findall(r"[\u3400-\u9fff\u3040-\u30ff\uac00-\ud7af]", head))
        if cjk >= min_chars or (not cjk and len(words) >= max(3, min_chars - 1)):
            out.append((t, re.sub(r"\s+", "", head)))
    return out


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

    Simultaneous mode (stream=True, 边说边译): while the speaker is still talking the sentence so
    far is decoded every LIVE_STEP_S; a clause that ends in punctuation, has speech after it and
    reads the same in two decodes in a row goes out at once, and the sentence carries on from there
    (the engine passes it as context, like an early cut). Long sentences start being translated
    after their first clause instead of at their end.

    Head start (on_guess): a sentence only counts as finished after `pause` of silence, and nothing
    downstream used to start before that. Now GUESS_S into a pause the speech so far is decoded
    and handed out as a guess — on_guess(Utterance with .guess = n) — so translation can begin;
    if the speaker goes on it is withdrawn (on_guess(None)), and if the sentence does end there
    with the same words, the finished Utterance carries the same .guess and the work already done
    is used. Saves most of `pause` on every sentence.

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
    LIVE_STEP_S, LIVE_MIN_S = 0.4, 1.4  # simultaneous mode: decode the sentence so far this often
    GUESS_S, GUESS_MIN_S = 0.12, 0.5    # head start: guess this far into a pause, for at least this much audio

    def __init__(self, name: str, pause: float, on_utt: Callable[[Utterance], None],
                 lang: str = "", alt: str = "", log: Callable[[str], None] | None = None,
                 on_level: Callable[[str, float], None] | None = None, recognizer=None, vad=None,
                 words: "QwenASR | None" = None, stream: bool = False,
                 on_guess: Callable[[Utterance | None], None] | None = None, patient: bool = False) -> None:
        """patient: whole sentences matter more than a fast start (subtitles) — long speech is cut
        later and only at a clearer pause. 2026-11-13: with the settings made for my own voice, the
        other side was cut 93 times in 23 minutes; 33 lines ended on "the", "to", "because"."""
        self.name, self.pause, self.on_utt = name, pause, on_utt
        if patient:
            self.LONG_S, self.DIP_S, self.HARD_S, self.HARD_DIP_S, self.MAX_S = 8.0, 0.45, 12.0, 0.25, 14.0
        self.on_guess = on_guess
        self.guess: tuple[int, str] | None = None  # (n, its words) of the guess not settled yet
        self.guess_n, self.guess_dip = 0, -1       # guesses made; the pause the last one was made in
        self.stream = stream  # 边说边译: send finished clauses while the sentence goes on
        self.live_from, self.live_next, self.live_prev = -1, 0, ""
        self.words = words  # optional better recognizer for the final text of each sentence
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
        if not self.lang or d.lang == self.lang or not re.sub(r"[\W_]+", "", d.text):
            return d
        if d.lang == self.alt:
            # The other side's language is welcome here (shown as it is) — unless the words say it
            # is not that language at all: English labelled Chinese was shown untranslated.
            if not (self.alt in CJK_LANGS and self.lang not in CJK_LANGS and reads_as_latin(d.text)):
                return d
        if self._fixed is None:
            self._fixed = Recognizer(self.lang)
        fixed = self._fixed.decode(seg)
        self.log(f"[{self.name}] 语言识别为 {d.lang}，按 {self.lang} 重新识别：{d.text!r} -> {fixed.text!r}")
        return Decoded(fixed.text, self.lang, fixed.tokens, fixed.times)

    def _emit(self, seg: np.ndarray, t_end: float, d: Decoded | None = None) -> None:
        d = d or self.recognize(seg)
        if self.words is not None and re.sub(r"[\W_]+", "", d.text):
            try:
                better = self.words.transcribe(seg, d.lang)
                if re.sub(r"[\W_]+", "", better):
                    d = Decoded(better, d.lang, d.tokens, d.times)
            except Exception as e:  # noqa: BLE001 — keep SenseVoice's words
                self.log(f"[{self.name}] Qwen3-ASR 失败，用 SenseVoice 结果：{e}")
        self.count += 1
        self.log(f"[{self.name}] segment {len(seg)/ASR_SR:.2f}s lang={d.lang} text={d.text!r}")
        guess = 0
        if self.guess is not None:  # whatever goes out next settles the guess
            n, words = self.guess
            self.guess = None
            if words == _plain(d.text):
                guess = n
            else:
                self.on_guess(None)
        if _plain(d.text):
            self.on_utt(Utterance(d.text, d.lang, seg, t_end, time.time() - t_end, guess))

    def _guess(self) -> None:
        """The speaker has just paused: decode the speech so far and hand it out as a guess."""
        self.guess_dip = self.dip_from
        if self.live_from >= 0:
            start = self.live_from
        else:  # (the VAD notices speech a little late: same lead-in as simultaneous mode)
            start = self.carry[0] if self.carry else self.speech_from - self.pre - int(0.25 * ASR_SR)
        a = max(start, self.hist_start, self.cut_to)
        seg = self.hist[self._abs(a):self._abs(self.fed)]
        if len(seg) < self.GUESS_MIN_S * ASR_SR:
            return
        d = self.recognize(seg)
        if not _plain(d.text):
            return
        self.guess_n += 1
        self.guess = (self.guess_n, _plain(d.text))
        self.on_guess(Utterance(d.text, d.lang, seg, time.time(), 0.0, self.guess_n))

    def _drop_guess(self) -> None:
        if self.guess is not None:
            self.guess = None
            self.on_guess(None)

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
                elif self.on_guess is not None:
                    if self.guess is not None and self.dip_from < 0:
                        self._drop_guess()  # the speaker went on
                    elif self.guess is None and dip >= self.GUESS_S and self.dip_from != self.guess_dip:
                        self._guess()
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
        if self.stream and open_gate:
            self._live()
        self._pop()
        if self.guess is not None and not vad.is_speech_detected():
            self._drop_guess()  # the speech ended without a sentence (too short for the VAD)
        self._send_lone_tail()

    def _live(self) -> None:
        """Simultaneous mode: send the finished clauses of the sentence still being spoken."""
        if self.speech_from < 0 or not self.vad.is_speech_detected():
            return
        if self.guess is not None:
            return  # in a pause, the whole sentence so far is already being tried
        if self.live_from < 0:  # a new stretch of speech (the VAD noticed it a little late)
            start = self.carry[0] if self.carry else self.speech_from - self.pre - int(0.25 * ASR_SR)
            self.live_from = max(start, self.hist_start, self.cut_to)
            self.live_next, self.live_prev = self.fed + int(self.LIVE_STEP_S * ASR_SR), ""
        if self.fed < self.live_next:
            return
        self.live_next = self.fed + int(self.LIVE_STEP_S * ASR_SR)
        seg = self.hist[self._abs(self.live_from):self._abs(self.fed)]
        if len(seg) < self.LIVE_MIN_S * ASR_SR:
            return
        cuts = live_cuts(self.recognize(seg), len(seg) / ASR_SR)
        same = next((c for c in cuts if self.live_prev and c[1] == self.live_prev), None)
        if same is None:  # wait until a clause reads the same in two decodes in a row
            self.live_prev = cuts[-1][1] if cuts else ""
            return
        t = same[0]
        at = self.live_from + int((t + 0.05) * ASR_SR)
        self.log(f"[{self.name}] 边说边译：先送出前 {t:.2f}s")
        self._emit(self.hist[self._abs(self.live_from):self._abs(at)], time.time())
        self.carry = None  # it began the clause just sent
        self.cut_to = self.live_from = at
        self.live_prev = ""

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
            self.live_from, self.live_prev = -1, ""  # the sentence is over
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
                 on_notice: Callable[[str], None] | None = None, words: "QwenASR | None" = None,
                 stream: bool = False, on_guess: Callable[[Utterance | None], None] | None = None,
                 patient: bool = False) -> None:
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
                                       on_partial, on_notice, detector=Recognizer("auto"), words=words)
        else:
            self.seg = Segmenter(name, pause, on_utt, lang, alt, self.log, on_level, words=words, stream=stream,
                                 on_guess=None if words is not None else on_guess,  # (a second recognizer may reword it)
                                 patient=patient)
        self.q: queue.Queue[np.ndarray] = queue.Queue()
        self.source = source
        self.capture = Capture(source, self.q.put, self.WINDOW)
        self.label = self.capture.label
        self._cap_lock = threading.Lock()
        self.stop_evt = threading.Event()
        self._flush = threading.Event()
        self._restart_at = 0.0
        self._hb = [time.time(), 0, 0.0, 0]  # window start, blocks, peak, segments

    def speaking(self) -> bool:
        """Speech is being heard right now (a sentence is under way)."""
        return getattr(self.seg, "speech_from", -1) >= 0

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
            with self._cap_lock:
                self.capture.close()
                self.capture = Capture(self.source, self.q.put, self.WINDOW)
                self.capture.start()
        except Exception as e:  # noqa: BLE001 — keep trying every 5 s
            self.log(f"[{self.name}] 重新打开输入流失败：{e}")

    def switch_source(self, source: str) -> str:
        """Hot switch to another input while running: the new device is opened first (a failure
        leaves the old one running and raises), then the old one closes; the sentence in progress
        ends here. Returns the new device's name."""
        new = Capture(source, self.q.put, self.WINDOW)
        new.start()
        with self._cap_lock:
            old, self.capture, self.source, self.label = self.capture, new, source, new.label
        old.close()
        self._restart_at = time.time() + 5
        self.flush()
        self.log(f"[{self.name}] 切换输入：{new.label}")
        return new.label

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
