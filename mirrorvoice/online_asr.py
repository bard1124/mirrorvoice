"""Streaming online speech recognition: 阿里云百炼 Fun-ASR realtime over a WebSocket.

Why: the service sends partial text while the person is still talking, so subtitles appear as
they speak and correct themselves, and a big streaming model recognises better than the local one.
It ends a sentence after max_sentence_silence of quiet (multi-threshold mode keeps sentences from
growing very long); speech without such a pause is split here, at a sentence end that stayed the
same in two partial results in a row.

Protocol (help.aliyun.com/zh/model-studio/fun-asr-realtime-websocket-api): connect with
"Authorization: Bearer <key>", send run-task (JSON), wait for task-started, stream binary 16 kHz
16-bit mono PCM, receive result-generated events (payload.output.sentence: text, begin_time /
end_time in ms of the audio sent in this task, sentence_end), send finish-task, task-finished.
A task with no audio for 23 s fails, so audio is only sent while someone speaks (local VAD), and an
idle task is finished after IDLE_FINISH_S; the socket is reused for the next one.

OnlineSegmenter has the Segmenter interface (feed / flush / count) so Listener can use either. It
falls back to the local Segmenter for the rest of the session after repeated failures.
"""
from __future__ import annotations

import json
import queue
import threading
import time
import uuid
from typing import Callable

import numpy as np

from . import __version__
from .asr import ASR_SR, Recognizer, Segmenter, Utterance, make_vad

URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
MODEL = "fun-asr-realtime"
FRAME = ASR_SR // 10                # 100 ms per binary frame
PREROLL_S, HANGOVER_S = 0.5, 1.0    # audio sent before VAD saw speech / after it stopped
IDLE_FINISH_S = 15.0                # finish the task well before the service's 23 s idle limit
MAX_SENTENCE_SILENCE_MS = 700       # service-side end of sentence (default 1300 ms: too slow live)
FAILS_TO_FALL_BACK = 3


_PUNCT = set("。！？!?；;，,、：:.…\"'“”‘’（）()《》 \t\n")
_ENDS = set("。！？!?")


def normalize(text: str) -> str:
    """Text without punctuation or spaces: partial results flip ，/。 between updates."""
    return "".join(c for c in text if c not in _PUNCT)


def strip_chars(text: str, n: int) -> str:
    """text minus its first n non-punctuation characters (and the punctuation right after them)."""
    if n <= 0:
        return text
    seen = 0
    for i, c in enumerate(text):
        if c not in _PUNCT:
            seen += 1
            if seen == n:
                rest = text[i + 1:]
                return rest.lstrip("".join(_PUNCT))
    return ""


def last_sentence_end(text: str) -> int:
    """Index just after the last 。！？ (or ". ") that has more text after it (0 = none)."""
    for i in range(len(text) - 1, -1, -1):
        end = text[i] in _ENDS or (text[i] == "." and i + 1 < len(text) and text[i + 1].isspace())  # not 3.5
        if end and normalize(text[i + 1:]):
            return i + 1
    return 0


def words_end(words, n: int) -> int:
    """end_ms of the word that completes the first n characters (0 = unknown)."""
    seen = 0
    for w, end in words:
        seen += len(normalize(w))
        if seen >= n:
            return end
    return 0


class StreamError(RuntimeError):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # auth | network | server


def _connect(url: str, key: str, timeout: float):
    import websocket  # websocket-client

    try:
        return websocket.create_connection(
            url, timeout=timeout, header=[f"Authorization: Bearer {key}", f"User-Agent: MirrorVoice/{__version__}",
                                          "X-DashScope-DataInspection: disable"])
    except websocket.WebSocketBadStatusException as e:
        code = getattr(e, "status_code", 0)
        if code in (401, 403):
            raise StreamError("auth", f"阿里云百炼 API key 无效或没有开通实时语音识别（{code}）") from None
        raise StreamError("server", f"语音识别服务拒绝连接（{code}）") from None
    except (OSError, websocket.WebSocketException) as e:
        raise StreamError("network", f"连不上语音识别服务：{e}") from None


class DashScopeStream:
    """One WebSocket, one task at a time. send() from the listener thread; results arrive on a
    reader thread as on_result(task_id, text, sentence_end, begin_ms, end_ms, words) — words:
    [(text with its punctuation, end_ms)] when the service sends them."""

    def __init__(self, key: str, lang: str, on_result: Callable[..., None],
                 on_error: Callable[[StreamError], None], url: str = URL, model: str = MODEL,
                 timeout: float = 3.0) -> None:
        self.key, self.lang, self.url, self.model, self.timeout = key, lang, url, model, timeout
        self.on_result, self.on_error = on_result, on_error
        self.ws = None
        self.task_id = ""
        self._started = threading.Event()
        self._lock = threading.Lock()
        self._reader: threading.Thread | None = None

    @property
    def open(self) -> bool:
        return bool(self.task_id) and self._started.is_set()

    def start_task(self) -> str:
        """Blocks until task-started (or raises StreamError)."""
        with self._lock:
            if self.ws is None:
                self.ws = _connect(self.url, self.key, self.timeout)
                self._reader = threading.Thread(target=self._read, args=(self.ws,), daemon=True, name="asr-online")
                self._reader.start()
            self.task_id = uuid.uuid4().hex
            self._started.clear()
            params = {"format": "pcm", "sample_rate": ASR_SR, "max_sentence_silence": MAX_SENTENCE_SILENCE_MS,
                      "multi_threshold_mode_enabled": True,  # keeps the service from making very long sentences
                      "heartbeat": False}
            if self.lang:
                params["language_hints"] = [self.lang]
            self._send_json({"header": {"action": "run-task", "task_id": self.task_id, "streaming": "duplex"},
                             "payload": {"task_group": "audio", "task": "asr", "function": "recognition",
                                         "model": self.model, "parameters": params, "input": {}}})
        if not self._started.wait(self.timeout):
            self.close()
            raise StreamError("network", "语音识别服务没有响应（等待开始超时）")
        return self.task_id

    def send(self, pcm16: bytes) -> None:
        import websocket

        try:
            self.ws.send(pcm16, opcode=websocket.ABNF.OPCODE_BINARY)
        except Exception as e:  # noqa: BLE001 — dropped connection: the next task reconnects
            self.close()
            raise StreamError("network", f"语音识别连接断开：{e}") from None

    def finish_task(self) -> None:
        if not self.task_id:
            return
        tid, self.task_id = self.task_id, ""
        try:
            self._send_json({"header": {"action": "finish-task", "task_id": tid, "streaming": "duplex"},
                             "payload": {"input": {}}})
        except Exception:  # noqa: BLE001
            self.close()

    def close(self) -> None:
        ws, self.ws, self.task_id = self.ws, None, ""
        self._started.clear()
        if ws is not None:
            try:
                ws.close()
            except Exception:  # noqa: BLE001
                pass

    def _send_json(self, d: dict) -> None:
        self.ws.send(json.dumps(d, ensure_ascii=False))

    def _read(self, ws) -> None:
        while True:
            try:
                msg = ws.recv()
            except Exception:  # noqa: BLE001 — closed (by us or the server)
                if self.ws is ws:
                    self.close()
                    self.on_error(StreamError("network", "语音识别连接断开"))
                return
            if not isinstance(msg, str):
                continue
            try:
                d = json.loads(msg)
            except ValueError:
                continue
            head = d.get("header", {})
            event, tid = head.get("event"), head.get("task_id", "")
            if event == "task-started":
                if tid == self.task_id:
                    self._started.set()
            elif event == "result-generated":
                s = (d.get("payload", {}).get("output", {}) or {}).get("sentence") or {}
                if s.get("heartbeat") or "text" not in s:
                    continue
                words = [(w.get("text", "") + (w.get("punctuation") or ""), int(w.get("end_time") or 0))
                         for w in s.get("words") or [] if isinstance(w, dict)]
                self.on_result(tid, s.get("text", ""), bool(s.get("sentence_end")),
                               int(s.get("begin_time") or 0), int(s.get("end_time") or 0), words)
            elif event == "task-failed":
                if tid == self.task_id:
                    self.task_id = ""
                self.close()
                code, text = head.get("error_code", ""), head.get("error_message", "")
                self.on_error(StreamError("auth" if "Access" in code or "Invalid" in code else "server",
                                          f"语音识别出错：{code} {text}".strip()))
                return


class OnlineSegmenter:
    """Local VAD decides when someone speaks; the service decides where sentences end.

    on_utt(Utterance) per final sentence (audio cut from local history by the service's
    timestamps, language detected locally — needed for passthrough); on_partial(text) while a
    sentence is still being spoken."""

    WINDOW = Segmenter.WINDOW
    HIST_S = 60

    def __init__(self, name: str, pause: float, on_utt: Callable[[Utterance], None], key: str,
                 lang: str = "", alt: str = "", log: Callable[[str], None] | None = None,
                 on_level: Callable[[str, float], None] | None = None,
                 on_partial: Callable[[str], None] | None = None,
                 on_notice: Callable[[str], None] | None = None,
                 stream: DashScopeStream | None = None, vad=None, detector=None, url: str = URL) -> None:
        self.name, self.pause, self.on_utt = name, pause, on_utt
        self.lang, self.alt = lang, alt
        self.log = log or (lambda *_: None)
        self.on_level = on_level or (lambda *_: None)
        self.on_partial = on_partial or (lambda _t: None)
        self.on_notice = on_notice or (lambda _t: None)
        self.stream = stream or DashScopeStream(key, lang, self._result, self._error, url=url)
        self.vad = vad or make_vad(pause)
        self._detector = detector  # local "auto" recognizer, only for the language of each sentence
        self.count = 0
        self.hist = np.zeros(0, np.float32)
        self.hist_start = self.fed = 0
        self.buf = np.zeros(0, np.float32)
        self.last_speech = -10 ** 12
        self.idle_since = time.monotonic()
        self.runs: dict[str, list[tuple[int, int]]] = {}  # task -> [(task ms offset, absolute sample)]
        self.sent_ms = 0
        self.sent_to = -1  # absolute sample just after the last audio sent in this task
        self.fails = 0
        self.local: Segmenter | None = None  # after repeated failures
        self._results: queue.Queue = queue.Queue()
        self._sentence: tuple[str, int] | None = None  # (task, begin_ms) of the open sentence
        self._done_chars, self._done_ms, self._prev_norm = 0, 0, ""
        self.lvl_t, self.lvl_sq, self.lvl_n = time.time(), 0.0, 0

    # ---------------------------------------------------------------- results (reader thread)
    def _result(self, tid: str, text: str, final: bool, begin_ms: int, end_ms: int, words=()) -> None:
        self._results.put((tid, text, final, begin_ms, end_ms, list(words)))

    def _error(self, e: StreamError) -> None:
        self._results.put(e)

    def _abs(self, tid: str, ms: int) -> int:
        runs = self.runs.get(tid) or [(0, self.fed)]
        off, at = runs[0]
        for r_off, r_at in runs:
            if r_off <= ms:
                off, at = r_off, r_at
        return at + (ms - off) * ASR_SR // 1000

    def _handle_results(self) -> None:
        while True:
            try:
                r = self._results.get_nowait()
            except queue.Empty:
                return
            if isinstance(r, StreamError):
                self._failed(r)
                continue
            tid, text, final, b_ms, e_ms, words = r
            if (tid, b_ms) != self._sentence:  # a new sentence: nothing of it was sent yet
                self._sentence, self._done_chars, self._done_ms, self._prev_norm = (tid, b_ms), 0, b_ms, ""
            rest = strip_chars(text, self._done_chars)
            if final:
                self.fails = 0
                self._emit(tid, rest, self._done_ms, e_ms, "online segment")
                self._sentence = None
                continue
            if self._commit(tid, text, b_ms, e_ms, words):
                rest = strip_chars(text, self._done_chars)
            if rest:
                self.on_partial(rest)

    COMMIT_AFTER_MS = 4000  # a sentence still open after this long gives up its finished parts

    def _commit(self, tid: str, text: str, b_ms: int, e_ms: int, words) -> bool:
        """Long speech without a pause: send a finished sentence inside the partial text on as if
        it were final — once it has stayed the same in two results in a row."""
        norm = normalize(text)
        stable, self._prev_norm = self._prev_norm, norm
        if e_ms - self._done_ms < self.COMMIT_AFTER_MS:
            return False
        cut = last_sentence_end(text)
        if cut <= 0:
            return False
        n = len(normalize(text[:cut]))
        if n - self._done_chars < 6 or stable[:n] != norm[:n]:
            return False
        end_ms = words_end(words, n) or (b_ms + (e_ms - b_ms) * n // max(len(norm), 1))
        self._emit(tid, strip_chars(text[:cut], self._done_chars), self._done_ms, end_ms, "online commit")
        self._done_chars, self._done_ms = n, end_ms
        return True

    def _emit(self, tid: str, text: str, b_ms: int, e_ms: int, what: str) -> None:
        text = text.strip()
        a, b = self._abs(tid, b_ms), self._abs(tid, e_ms) + int(0.15 * ASR_SR)
        lo, hi = max(a - self.hist_start, 0), max(min(b, self.fed) - self.hist_start, 0)
        seg = self.hist[lo:hi]
        lang = self.lang
        if self._detector is not None and len(seg) > ASR_SR // 4:
            d = self._detector.decode(seg)
            if d.lang == self.alt:
                lang = d.lang  # e.g. I switched to their language: passthrough
        self.count += 1
        behind = (self.fed - min(b, self.fed)) / ASR_SR
        self.log(f"[{self.name}] {what} {len(seg) / ASR_SR:.2f}s lang={lang} text={text!r}")
        if normalize(text):
            self.on_utt(Utterance(text, lang, seg, time.time(), behind))

    def _failed(self, e: StreamError) -> None:
        self.fails = FAILS_TO_FALL_BACK if e.kind == "auth" else self.fails + 1
        self.log(f"[{self.name}] 在线识别失败 {e.kind}: {e}")
        if self.fails >= FAILS_TO_FALL_BACK and self.local is None:
            self.stream.close()
            self.on_notice(f"在线语音识别不可用（{e}），本次改用本机识别")
            self.local = Segmenter(self.name, self.pause, self.on_utt, self.lang, self.alt, self.log, self.on_level)

    # ---------------------------------------------------------------- feeding (listener thread)
    def flush(self) -> None:
        if self.local is not None:
            self.local.flush()
        elif self.stream.open:
            self.stream.finish_task()  # push-to-talk released: the service closes the sentence

    def feed(self, block: np.ndarray, open_gate: bool = True) -> None:
        self._handle_results()
        if self.local is not None:
            before = self.local.count
            self.local.feed(block, open_gate)
            self.count += self.local.count - before
            return
        w = self.WINDOW
        self.buf = np.concatenate([self.buf, block])
        out = []
        while len(self.buf) >= w:
            chunk = self.buf[:w] if open_gate else np.zeros(w, np.float32)
            self.buf = self.buf[w:]
            self.vad.accept_waveform(chunk)
            while not self.vad.empty():  # segments are the service's job here
                self.vad.pop()
            if open_gate and self.vad.is_speech_detected():
                self.last_speech = self.fed + w
            self.hist = np.concatenate([self.hist, chunk])
            self.fed += w
            out.append(chunk)
            self.lvl_sq += float((chunk * chunk).sum())
            self.lvl_n += w
        if len(self.hist) > self.HIST_S * ASR_SR:
            drop = len(self.hist) - self.HIST_S * ASR_SR
            self.hist, self.hist_start = self.hist[drop:], self.hist_start + drop
        if time.time() - self.lvl_t > 0.1:
            self.on_level(self.name, (self.lvl_sq / max(self.lvl_n, 1)) ** 0.5)
            self.lvl_t, self.lvl_sq, self.lvl_n = time.time(), 0.0, 0
        if out:
            self._send(np.concatenate(out))
        self._handle_results()

    def _send(self, audio: np.ndarray) -> None:
        speaking = self.fed - self.last_speech <= int(HANGOVER_S * ASR_SR)
        if not speaking:
            if self.stream.open and time.monotonic() - self.idle_since > IDLE_FINISH_S:
                self.stream.finish_task()
            return
        self.idle_since = time.monotonic()
        start = self.fed - len(audio)
        try:
            if not self.stream.open:
                tid = self.stream.start_task()
                self.runs = {tid: []}  # older tasks' results are done
                self.sent_ms, self.sent_to = 0, -1
            tid = self.stream.task_id
            if start != self.sent_to:  # speech (re)starts: send what the VAD heard too late as well
                pre = min(int(PREROLL_S * ASR_SR), start - self.hist_start, max(start - self.sent_to, 0)
                          if self.sent_to >= 0 else int(PREROLL_S * ASR_SR))
                audio = np.concatenate([self.hist[start - pre - self.hist_start:start - self.hist_start], audio])
                start -= pre
                self.runs.setdefault(tid, []).append((self.sent_ms, start))  # the service hears it right after the last
            pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
            for i in range(0, len(pcm), FRAME * 2):
                self.stream.send(pcm[i:i + FRAME * 2])
            self.sent_ms += len(audio) * 1000 // ASR_SR
            self.sent_to = start + len(audio)
        except StreamError as e:
            self._failed(e)
