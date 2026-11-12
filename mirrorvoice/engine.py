"""Engine: wires listeners, translator, voice and player; reports events to the UI.

Threads: one Listener per direction (capture/VAD/ASR on CPU) and ONE model worker that owns
every MLX/LLM/TTS object (MLX streams are per-thread). My speech (priority 0) always jumps
ahead of the other side's subtitles (priority 1).
"""
from __future__ import annotations

import itertools
import queue
import threading
import time
import re
import traceback
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import models
from .asr import SENTENCE_END, Listener, Utterance
from .audio import Player, find_device, speech_level, time_stretch, trim_silence
from .config import LOG_FILE, Settings
from . import hotkeys
from .hotkeys import Hotkeys
from .languages import LANGS
from .transcript import Transcript
from .online_llm import FailoverChat, OnlineChat, TranslateError
from .translate import Translator, correct_asr, is_filler, is_fragment, local_backend, spoken_en, tidy_asr
from .tts import make_voice

Emit = Callable[..., None]  # emit(kind: str, **data)

SIMILAR = {"zh": {"zh", "yue"}, "yue": {"yue", "zh"}}


@dataclass
class _Speech:
    """One sentence of mine on its way to the voice thread: text pieces arrive in `parts` (the
    first clause while the translator is still writing, then the rest; None = done)."""
    utt: Utterance
    waited: float
    t0: float                       # translation started
    parts: queue.Queue = field(default_factory=queue.Queue)
    t_first_text: float = 0.0       # when the first piece to speak was known
    out: str = ""                   # the whole translation ("" = nothing to report)
    t_tr: float = 0.0
    # A head start (Segmenter.on_guess): translated while it is still open whether the sentence
    # ends there. "open" -> "go" (it did: speak it) or "void" (I went on: drop it unheard).
    guess: int = 0
    state: str = "go"
    decided: threading.Event = field(default_factory=threading.Event)


def speech_seconds(text: str) -> float:
    """Rough spoken length: ~2.6 English words or ~4.5 CJK characters per second."""
    words = len(text.split())
    cjk = len(re.findall(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]", text))
    return cjk / 4.5 + max(words - cjk, 0) / 2.6


def check_models(required: list) -> str:
    """'' or a message naming the damaged files of downloaded models — which are deleted, so the
    「下载模型」banner offers to fetch just those again (a cut download used to fail every start)."""
    found = []
    for m in required:
        if m.ready():
            bad = models.remove_damaged(m)
            if bad:
                found.append(f"{m.title}（{'、'.join(bad)}）")
    if not found:
        return ""
    return ("模型文件损坏：" + "；".join(found) + "。损坏的文件已删除，请点窗口顶部的「下载模型」补齐"
            "（只下载这几个文件），再点「开始」。")


class _Void(Exception):
    """The sentence went on after the pause a guess was made in: stop translating the guess."""


class _Cancelled(Exception):
    """The session a start-up belongs to was stopped (or replaced) while it was loading."""


class Engine:
    def __init__(self, emit: Emit) -> None:
        self._emit = emit
        self.running = False
        # Each start() is a session. Its start-up thread loads models for many seconds and cannot
        # be interrupted mid-load; if the session is stopped meanwhile, the thread notices at the
        # next step, closes what it made and quits without a word (no "运行中" after 停止).
        self._session = 0
        self._tl = threading.local()  # .sid on a start-up thread: the session it works for
        self._lock = threading.RLock()
        self._boot_thread: threading.Thread | None = None
        self.muted = False
        # exist before the first start(): the UI may call set_voice_speed / set_monitor / reload_voice early
        self._player = self._voice = self._transcript = self._me = self._them = None
        self._qwen_sides = set()
        self._prev = {"me": None, "them": None}
        self._hist = {"me": deque(maxlen=4), "them": deque(maxlen=4)}
        self._voice_q: queue.Queue = queue.Queue()
        self._play_until, self._pending_s, self._compact = 0.0, 0.0, False
        self._waiting_s = 0.0  # seconds of my speech heard but not being translated yet
        self._guess_job: _Speech | None = None
        self._synth_idle = threading.Event()
        self._synth_idle.set()
        self._interim_tr, self._interim_busy, self._interim_at, self._them_gen = None, False, 0.0, 0
        self._levels: deque = deque(maxlen=30)  # speech levels of my recent sentences
        self._quiet_hits: list[float] = []      # when sounds were dropped as too quiet
        self._quiet_told = 0.0
        self._held: Utterance | None = None     # a short piece of the other side's sentence, waiting for the rest
        self._held_timer: threading.Timer | None = None
        self._ready = False
        self.ptt_down = False
        self._jobs: queue.PriorityQueue = queue.PriorityQueue()
        self._seq = itertools.count()
        self._closers: list[Callable[[], None]] = []
        self._log = None

    # ------------------------------------------------------------ public
    def start(self, s: Settings) -> None:
        if self.running:
            return
        with self._lock:
            self._session += 1
            sid = self._session
            self._closers = []
            self.running = True
        self.s = s
        self.muted = False
        self._player = self._voice = self._transcript = self._me = self._them = None
        self._qwen_sides: set = set()
        self._ready = False
        self._interim_tr, self._interim_busy, self._interim_at, self._them_gen = None, False, 0.0, 0
        self._words = None
        self._levels.clear()
        self._quiet_hits, self._quiet_told = [], 0.0
        self._take_held()
        self._prev = {"me": None, "them": None}  # (source, translation, t_end) of the last part
        self._hist = {"me": deque(maxlen=self.HISTORY_N + 1), "them": deque(maxlen=self.HISTORY_N + 1)}
        # A fresh queue per session: the previous worker may still be busy (and the previous
        # stop() left a "stop" job behind). Sharing the queue let a new worker swallow that stale
        # "stop" and exit while its listeners kept running — "nothing happens" until restart.
        self._jobs = queue.PriorityQueue()
        self._voice_q: queue.Queue = queue.Queue()  # my translated speech, played in order by the voice thread
        self._play_until, self._pending_s, self._compact = 0.0, 0.0, False
        self._waiting_s = 0.0  # seconds of my speech heard but not being translated yet
        self._guess_job: _Speech | None = None
        self._synth_idle = threading.Event()
        self._synth_idle.set()
        self._seq = itertools.count()
        try:  # keep earlier sessions for diagnosis; start over when the file gets big
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > 2_000_000:
                LOG_FILE.unlink()
        except OSError:
            pass
        self._log = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        self.log(f"===== 开始 {time.strftime('%Y-%m-%d')} =====")
        try:  # main thread here; the hotkey listeners start later on worker threads
            hotkeys.prime_layout()
        except Exception as e:  # noqa: BLE001 — hotkeys then report themselves unavailable
            self.log(f"读取键盘布局失败：{e}")
        prev = self._boot_thread
        self._boot_thread = threading.Thread(target=self._boot, args=(sid, s, self._jobs, prev), daemon=True,
                                             name="engine-boot")
        self._boot_thread.start()

    def stop(self) -> None:
        with self._lock:
            if not self.running:
                return
            self.running = False
            closers, self._closers = self._closers, []
        self._take_held()
        self._save_level()
        self._jobs.put((99, next(self._seq), "stop", None))
        for close in reversed(closers):
            try:
                close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        self.emit("stopped")

    def emit(self, kind: str, **d) -> None:
        if getattr(self._tl, "sid", None) is None:
            self._emit(kind, **d)
            return
        with self._lock:  # checked and sent together: nothing from it can follow "stopped"
            if not self._stale():
                self._emit(kind, **d)

    def _stale(self) -> bool:
        """True on the start-up thread of a session that has been stopped or replaced."""
        sid = getattr(self._tl, "sid", None)
        return sid is not None and not (self.running and sid == self._session)

    def _check(self) -> None:
        if self._stale():
            raise _Cancelled

    def _own(self, close: Callable[[], None]) -> None:
        """Close this at stop(). A start-up whose session was stopped meanwhile closes it at once
        and gives up."""
        with self._lock:
            if not self._stale():
                self._closers.append(close)
                return
        try:
            close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass
        raise _Cancelled

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.emit("mute", muted=muted)

    def reload_glossary(self) -> None:
        self._jobs.put((0, next(self._seq), "glossary", None))

    # ------------------------------------------------------------ internals
    def log(self, msg: str) -> None:
        if self._log:
            self._log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

    # ------------------------------------------------------------ settings changed while running
    def _progress(self, text: str) -> None:
        """Loading steps: the status line while starting, a notice once running."""
        self.emit("notice" if self._ready else "status", text=text)

    def apply(self, changed: set[str]) -> None:
        """Settings changed in the control panel while running (UI thread): take effect now.
        "llm" = the online / local translator choice. Slow parts run on their own threads."""
        if not self.running:
            return
        s = self.s
        if changed & {"speak", "passthrough"} and (s.speak or s.passthrough):
            try:
                self._ensure_player()
            except Exception as e:  # noqa: BLE001
                self.emit("notice", text=f"打开“送进会议”设备失败：{e}")
        if changed & {"my_lang", "their_lang"}:
            self._set_languages()
        if "stream" in changed:
            self._set_stream()
        if changed & {"speak", "quality", "their_lang", "cpu_voice", "gpu_voice"} and self._voice_changes():
            self.reload_voice()
        if "subtitles" in changed:
            threading.Thread(target=self._apply_subtitles, daemon=True, name="subtitles").start()
        if "ptt_key" in changed:
            self._start_hotkeys()
        if changed & {"quality", "llm"}:
            self._jobs.put((0, next(self._seq), "models", None))

    def _voice_changes(self) -> bool:
        s = self.s
        if not (s.speak and LANGS[s.their_lang].tts):
            return self._voice is not None
        backend = models.voice_backend(s)
        tts_model = {"kokoro": models.KOKORO, "pocket": models.POCKET}.get(backend, models.TTS_CLONE[s.quality])
        return self._voice is None or getattr(self, "_voice_key", None) != (backend, str(tts_model.path))

    def _ensure_player(self) -> None:
        if self._player is not None:
            return
        s = self.s
        mon = None
        if s.monitor:
            try:
                mon = find_device(s.monitor, "output")
            except LookupError:
                pass
        self._player = Player(find_device(s.call_out, "output"), mon, s.gain)
        self._own(self._player.close)

    def _set_languages(self) -> None:
        """New language pair: listeners expect the new languages; earlier turns no longer apply."""
        s = self.s
        for listener, lang, alt in ((self._me, s.my_lang, s.their_lang), (self._them, s.their_lang, s.my_lang)):
            seg = getattr(listener, "seg", None)
            if seg is None:
                continue
            seg.lang, seg.alt = lang, alt
            if hasattr(seg, "_fixed"):
                seg._fixed = None  # built again for the new language on first need
            stream = getattr(seg, "stream", None)
            if stream is not None:
                stream.lang = lang  # online recognition: the next task's language hint
            local = getattr(seg, "local", None)
            if local is not None:
                local.lang, local.alt, local._fixed = lang, alt, None
        self._prev = {"me": None, "them": None}
        self._hist = {"me": deque(maxlen=self.HISTORY_N + 1), "them": deque(maxlen=self.HISTORY_N + 1)}

    def _set_stream(self) -> None:
        """边说边译 on / off for both listeners (local recognition; online recognition commits its
        own sentences)."""
        for listener in (self._me, self._them):
            seg = getattr(listener, "seg", None)
            for target in (seg, getattr(seg, "local", None)):
                if target is not None and hasattr(target, "live_from"):
                    target.stream = self.s.stream
        self.emit("notice", text="边说边译已开启" if self.s.stream else "边说边译已关闭")

    def _apply_subtitles(self) -> None:
        s = self.s
        try:
            if s.subtitles and s.their_source and self._them is None:
                self._start_them(s.their_source)
                self.emit("notice", text="对方字幕已开启")
            elif not s.subtitles and self._them is not None:
                self.set_their_source("")
                self.emit("notice", text="对方字幕已关闭")
        except Exception as e:  # noqa: BLE001
            self.emit("notice", text=f"开启对方字幕失败：{e}")

    def _start_hotkeys(self) -> None:
        """(Re)start the global keys — at start, and when the push-to-talk key changes (main thread:
        the keyboard layout is read there)."""
        old = getattr(self, "_hk", None)
        if old is not None:
            old.stop()
            if old.stop in self._closers:
                self._closers.remove(old.stop)
        hotkeys.prime_layout()
        s = self.s
        hk = Hotkeys(s.ptt_key, s.mute_hotkey, self._on_ptt, lambda: self.set_muted(not self.muted))
        try:
            hk.start()
            self._own(hk.stop)
            self._hk = hk
        except Exception as e:  # noqa: BLE001 — no permission: hotkeys just don't work
            self._hk = None
            self.emit("notice" if self._ready else "status", text=f"全局快捷键不可用：{e}")

    def _switch_translators(self, tr: Translator, tr_them: Translator) -> tuple[Translator, Translator]:
        """Worker thread: build the translators for the new model / online choice; the old ones
        keep working until the new ones are ready, and stay when that fails."""
        s = self.s
        need = [m for m in models.required(s.quality, s) if not m.ready() and m.key == "llm"]
        if need:
            self.emit("notice", text="翻译模型还没下载：" + "、".join(m.title for m in need)
                      + "，先在窗口顶部下载，下载完再切换")
            return tr, tr_them
        try:
            new, new_them = self._translators(s)
            new.translate("你好", "zh", "en")
        except Exception as e:  # noqa: BLE001
            self.emit("notice", text=f"切换翻译模型失败，继续用原来的：{e}")
            return tr, tr_them
        for t in {id(tr): tr, id(tr_them): tr_them}.values():
            if t not in (new, new_them):
                try:
                    t.close()
                except Exception:  # noqa: BLE001
                    pass
        self._tr_them = new_them
        self._interim_tr = Translator(new_them.backend.online, s.meeting_context) \
            if isinstance(new_them.backend, FailoverChat) else None
        self.emit("notice", text=f"翻译已切换为：{new.label or models.llm_name(s.quality)}")
        return new, new_them

    def _start_them(self, source: str) -> Listener:
        s = self.s
        them = Listener("them", source, s.their_lang, s.pause, self._on_them, self._on_level, self._them_gate,
                        self.log, alt=s.my_lang, online_key=self._asr_key(s, "them"),
                        on_partial=lambda t: self._on_partial("them", t), on_notice=self._notice,
                        words=self._words if "them" in self._qwen_sides else None, stream=s.stream)
        them.start()
        self._own(them.close)
        self._them = them
        return them

    # ------------------------------------------------------------ hot device switching
    def set_mic(self, name: str) -> str:
        """Switch my microphone while running. Returns the device opened (raises if it can't be)."""
        if not (self.running and self._me):
            return ""
        return self._me.switch_source(name)

    def set_call_out(self, name: str) -> str:
        """Switch the virtual mic the meeting listens to while running."""
        if not (self.running and self._player):
            return ""
        self._player.set_call(find_device(name, "output"))
        return self._player.outs[0].name

    def set_their_source(self, name: str) -> str:
        """Switch, start ("" before) or stop ("" now) the other side's subtitles while running.
        Starting loads a recognizer (~1 s): done here, so call it off the UI thread."""
        if not self.running:
            return ""
        them = self._them
        if not name:
            if them is not None:
                self._them = None
                if them.close in self._closers:
                    self._closers.remove(them.close)
                them.close()
            return ""
        if them is not None:
            return them.switch_source(name)
        if not self.s.subtitles:
            return ""
        return self._start_them(name).label

    def _them_gate(self) -> bool:
        """Ignore the other side's source while our own translation plays on the monitor output —
        only when that source is a speaker loopback (Windows), which hears the monitor and would
        translate our English back. BlackHole on macOS carries meeting audio only, so there the
        other side keeps being heard even while we speak."""
        s = self.s
        if not (s.monitor and s.their_source.startswith("loopback:")):
            return True
        return not (self._player is not None and self._player.busy())

    def _gate(self) -> bool:
        if self.muted:
            return False
        return self.ptt_down if self.s.mode == "ptt" else True

    def _boot(self, sid: int, s: Settings, jobs: queue.PriorityQueue,
              prev: threading.Thread | None = None) -> None:
        self._tl.sid = sid
        try:
            if prev is not None and prev.is_alive():  # 开始 → 停止 → 开始: let the last start-up wind down
                self.emit("status", text="等待上次启动结束…")
                prev.join(timeout=60)
            self._check()
            self._worker(s, jobs)
        except _Cancelled:
            self.log("启动途中已停止：放弃这次启动")
        except Exception as e:  # noqa: BLE001 — surface to UI instead of dying silently
            self.log(traceback.format_exc())
            if not self._stale():  # (a stopped start-up may fail on what stop() closed: no news)
                self.emit("error", message=str(e))
                self.stop()
        finally:
            if getattr(self._tl, "resident", False):
                models.keep_resident(False)

    def _worker(self, s: Settings, jobs: queue.PriorityQueue) -> None:
        backend = models.voice_backend(s)
        damaged = check_models(models.required(s.quality, s))
        if damaged:
            raise RuntimeError(damaged)
        need = [m for m in models.required(s.quality, s) if not m.ready()]
        if need:
            raise RuntimeError("模型未下载完整：" + "、".join(m.title for m in need) + "（请运行设置向导）")

        tr, tr_them = self._translators(s)

        voice = player = None
        their_tts = LANGS[s.their_lang].tts
        self._check()
        if s.speak and their_tts:
            self.emit("status", text="加载语音合成…")
            tts_model = {"kokoro": models.KOKORO, "pocket": models.POCKET}.get(backend, models.TTS_CLONE[s.quality])
            voice = make_voice(backend, tts_model.path)
            voice.speed = s.voice_speed
            self._own(voice.close)
            self._voice_key = (backend, str(tts_model.path))
        if s.speak or s.passthrough:
            self.emit("status", text="打开音频设备…")
            mon = None
            if s.monitor:
                try:
                    mon = find_device(s.monitor, "output")
                except LookupError:
                    self.emit("status", text=f"监听设备 {s.monitor} 未连接，已跳过")
            player = Player(find_device(s.call_out, "output"), mon, s.gain)
            self._own(player.close)

        models.keep_resident(True)  # until this session's thread ends (_boot)
        self._tl.resident = True
        self.emit("status", text="预热…")
        for t in {id(tr): tr, id(tr_them): tr_them}.values():
            try:
                t.translate("你好", "zh", "en")
            except TranslateError as e:
                if e.kind in TranslateError.PERSISTENT:
                    raise RuntimeError(f"在线翻译不可用：{e}") from None
                self.emit("notice", text=f"在线翻译预热失败（{e}），开始后会继续尝试")
        if voice:
            for _ in voice.stream("Hello.", s.their_lang):
                pass
        self._check()

        transcript = Transcript(s.my_lang, s.their_lang)
        self._transcript = transcript
        self._player, self._voice = player, voice
        # Speech is synthesised on its own thread: the translator no longer waits ~4 s for each
        # sentence's voice before taking the next one (or the other side's subtitles).
        vq = self._voice_q
        threading.Thread(target=self._voicer, args=(vq,), daemon=True, name="engine-voice").start()
        self._own(lambda: vq.put(None))

        # expected language fixed (Mandarin was misheard as Cantonese / Japanese); theirs is still
        # recognised, for passthrough
        self._tr_them = tr_them
        words, sides = None, models.qwen_asr_sides(s)
        if sides:
            self.emit("status", text="加载语音识别 Qwen3-ASR…")
            from .asr import QwenASR, glossary_hotwords

            words = QwenASR(models.ASR_QWEN.path)
            words.hotwords = glossary_hotwords(tr.glossary)
        self._check()
        self._words = words
        self._interim_tr = Translator(tr_them.backend.online, s.meeting_context) \
            if isinstance(tr_them.backend, FailoverChat) else None
        me = Listener("me", s.mic, s.my_lang, s.pause, self._on_me, self._on_level, self._gate, self.log,
                      alt=s.their_lang, online_key=self._asr_key(s, "me"),
                      on_partial=lambda t: self._on_partial("me", t), on_notice=self._notice,
                      words=words if "me" in sides else None, stream=s.stream, on_guess=self._on_me_guess)
        me.start()
        self._own(me.close)
        self._me = me
        them = None
        self._qwen_sides = sides
        if s.subtitles and s.their_source:
            try:
                them = self._start_them(s.their_source)
            except Exception as e:  # noqa: BLE001 — subtitles are optional
                self.emit("status", text=f"对方声音来源不可用（{e}），字幕关闭")

        self._start_hotkeys()

        self._check()
        self._ready = True
        self.emit("ready", mic=me.label, call=s.call_out, them=them.label if them else "",
                  voice="你的克隆音色" if (voice and voice.clone) else ("标准音色" if voice else "不出声"),
                  transcript=str(transcript.md), llm=tr.label)

        while self.running and not self._stale():  # this session's queue: start() makes a new one
            prio, _, kind, utt = jobs.get()
            if kind == "stop":
                break
            if kind == "glossary":
                for t in (tr, tr_them):
                    t.reload_glossary()
                    t.meeting = s.meeting_context
                if self._words is not None:
                    from .asr import glossary_hotwords

                    self._words.hotwords = glossary_hotwords(tr.glossary)
                continue
            if kind == "voice":
                self._voice_q.put(("swap", None))  # the voice thread owns every TTS object
                continue
            if kind == "models":
                tr, tr_them = self._switch_translators(tr, tr_them)
                continue
            if kind == "me":
                self._do_me(tr, utt)
            elif kind == "guess":
                self._do_me(tr, utt.utt, utt)
            else:
                self._do_them(tr_them, utt)

    def _translators(self, s: Settings) -> tuple[Translator, Translator]:
        """(mine, the subtitles'). Local model only when something uses it: offline mode, the
        subtitles when they stay local, or as the online translation's fallback (loaded on demand)."""
        local_path = models.LLM[s.quality].path
        shared: dict[str, object] = {}

        def local():
            if "b" not in shared:
                # later as the online fallback: a notice, the status line already says 运行中
                self._progress("加载翻译模型…")
                shared["b"] = local_backend(local_path)
                self._own(shared["b"].close)
            return shared["b"]

        if s.llm_mode != "online":
            t = Translator(local(), s.meeting_context)
            return t, t
        self._progress("连接在线翻译…")
        try:
            online = OnlineChat.from_settings(s)
        except TranslateError as e:
            raise RuntimeError(f"在线翻译设置不完整：{e}（左侧“在线模型”里设置）") from None
        fallback = local if models.LLM[s.quality].ready() else None
        mine = Translator(FailoverChat(online, fallback, lambda m: self.emit("notice", text=m)), s.meeting_context)
        if s.online_their or not (s.subtitles and s.their_source):
            return mine, mine
        self._progress("加载本地翻译模型（对方字幕）…")
        return mine, Translator(local(), s.meeting_context)

    CONTINUATION_GAP_S = 1.5  # a part starting this soon after the previous one continues it
    STALE_THEM_S = 6.0        # subtitles this far behind are skipped instead of piling up behind my speech

    HISTORY_N, HISTORY_S = 3, 90.0  # earlier turns given to the translator as conversation context

    def _context(self, who: str, utt: Utterance) -> tuple[str, str] | None:
        """The previous part when this one continues the same sentence: it started right after it
        and the previous part did not end a sentence (cuts now land on punctuation)."""
        prev = self._prev.get(who)
        if not prev:
            return None
        start = utt.t_end - len(utt.audio) / 16000
        if start - prev[2] >= self.CONTINUATION_GAP_S or prev[0].rstrip()[-1:] in SENTENCE_END:
            return None
        return prev[0], prev[1]

    LOCAL_HISTORY_N = 1  # local models: each earlier turn costs ~0.13 s of prompt (3 turns: +0.39 s)

    def _history(self, who: str, utt: Utterance, context, tr: Translator | None = None) -> list[tuple[str, str]]:
        start = utt.t_end - len(utt.audio) / 16000
        rows = [(a, b) for a, b, t in self._hist[who] if start - t < self.HISTORY_S]
        if context and rows and rows[-1] == context:
            rows = rows[:-1]  # already given as the sentence being continued
        n = self.HISTORY_N
        if tr is not None and not isinstance(tr.backend, FailoverChat):
            n = self.LOCAL_HISTORY_N
        return rows[-n:] if n else []

    def _remember(self, who: str, src: str, tgt: str, t_end: float) -> None:
        self._prev[who] = (src, tgt, t_end)
        self._hist[who].append((src, tgt, t_end))

    GUESS_WAIT_S = 1.0  # a guess is settled within the sentence-ending pause; never wait longer

    def _translating(self, utt: Utterance) -> None:
        with self._lock:
            self._waiting_s = max(0.0, self._waiting_s - len(utt.audio) / 16000)

    def _settled(self, job: _Speech) -> bool:
        """True when the job may be spoken and recorded: always, except for a guess that was not
        confirmed (waits a moment for the verdict if the translation was done first)."""
        if job.state == "open":
            job.decided.wait(self.GUESS_WAIT_S)
            with self._lock:
                if job.state == "open":
                    job.state = "void"
                    job.decided.set()
        return job.state == "go"

    def _do_me(self, tr: Translator, utt: Utterance, guess: _Speech | None = None) -> None:
        s = self.s
        if guess is not None and guess.state == "void":
            self._translating(utt)
            return
        t0 = time.time()
        waited = t0 - utt.t_end  # time the sentence sat in the queue behind earlier work
        utt.text = correct_asr(utt.text, tr.glossary, "me")
        speak = self._voice is not None and not self.muted and self._player is not None
        if speak and getattr(tr.backend, "uses_gpu", True):
            # Translator and voice share the GPU: running them side by side made both slower
            # (1.58 -> 1.75 s at a normal pace). Wait for the previous sentence's synthesis; only the
            # first clause of this one overlaps with the rest of its translation. An online translator
            # does not use this machine's GPU: it starts right away.
            self._synth_idle.wait(30)
            t0 = time.time()
            waited = t0 - utt.t_end
        self._translating(utt)
        if guess is not None and guess.state == "void":
            return  # I went on while it waited its turn
        job = guess or _Speech(utt, waited, t0)
        job.waited, job.t0 = max(0.0, waited), t0
        compact = self._update_compact()
        budget = 0
        if compact:  # roughly as long as I took to say it, so the backlog shrinks instead of growing
            said = len(utt.audio) / 16000
            budget = max(4, round(said * (2.4 if s.their_lang not in ("zh", "yue", "ja") else 4.0)))
        if speak:
            self._voice_q.put(("speak", job))
        first: dict[str, str] = {}

        def on_clause(text: str) -> None:  # the first clause, while the rest is still being written
            first["text"], job.t_first_text = text, time.time()
            job.parts.put(text)

        def still_wanted(_text: str) -> None:  # a guess I talked past: stop writing it
            if job.state == "void":
                raise _Void
        try:
            ctx = self._context("me", utt)
            out = tr.translate(utt.text, s.my_lang, s.their_lang, context=ctx, history=self._history("me", utt, ctx, tr),
                               concise=budget, on_clause=on_clause if speak else None,
                               on_text=still_wanted if guess is not None else None)
        except _Void:
            job.parts.put(None)
            return
        except TranslateError as e:  # nothing (more) is spoken; the sentence shows as failed
            job.parts.put(None)
            if not self._settled(job):
                return
            self.log(f"[me] 在线翻译失败 {e.kind}: {e}")
            self.emit("me", src=utt.text, tgt=f"⚠ 翻译失败：{e}", latency=time.time() - utt.t_end,
                      asr_s=utt.asr_s, queue_s=max(0.0, waited - utt.asr_s), translate_s=time.time() - t0,
                      voice_s=None, passthrough=False, failed=True)
            return
        t_tr = time.time() - t0
        if not out or not self._settled(job):
            job.parts.put(None)
            return
        self._remember("me", utt.text, out, utt.t_end)
        self._transcript.add("me", utt.text, out)
        if not speak:
            self.log(f"[me] 排队 {waited:.1f}s 翻译 {t_tr:.2f}s（不出声）: {out[:40]!r}")
            self.emit("me", src=utt.text, tgt=out, latency=time.time() - utt.t_end, asr_s=utt.asr_s,
                      queue_s=max(0.0, waited - utt.asr_s), translate_s=t_tr, voice_s=None, passthrough=False)
            return
        job.out, job.t_tr = out, t_tr
        head = first.get("text", "")
        if not head:
            job.t_first_text = time.time()
            job.parts.put(out)
        elif out.startswith(head):
            if out[len(head):].strip():
                job.parts.put(out[len(head):].strip())
        else:  # the final text was cleaned up differently: the spoken clause stays, log it
            self.log(f"[me] 先念的分句与整句不一致：{head!r} / {out!r}")
        job.parts.put(None)
        self._pending_s += speech_seconds(out)

    # ------------------------------------------------------------ voice thread
    BACKLOG_ON_S, BACKLOG_OFF_S, BACKLOG_SPEED = 3.0, 1.5, 1.15

    def _backlog(self) -> float:
        """Seconds of my speech not yet heard: queued audio plus sentences not synthesised yet."""
        return max(0.0, self._play_until - time.monotonic()) + max(0.0, self._pending_s)

    # Before it gets that far: a sentence that had to wait behind earlier speech (or has the next
    # one waiting behind it) is spoken a little faster, more the longer the wait.
    CATCH_UP_FROM_S, CATCH_UP_FULL_S, CATCH_UP_SPEED = 0.8, 3.0, 1.2

    def _catch_up(self, lag: float) -> float:
        """Speaking-rate factor for speech that is `lag` seconds behind (1.0 = on time)."""
        x = (lag - self.CATCH_UP_FROM_S) / (self.CATCH_UP_FULL_S - self.CATCH_UP_FROM_S)
        boost = 1.0 + (self.CATCH_UP_SPEED - 1.0) * min(1.0, max(0.0, x))
        return max(boost, self.BACKLOG_SPEED if self._compact else 1.0)

    def _update_compact(self) -> bool:
        """English renderings run longer than the Chinese said (2.5 s -> ~6 s): when speech piles up,
        ask for shorter translations and speak a little faster until it has caught up."""
        b = self._backlog()
        was, self._compact = self._compact, b > self.BACKLOG_ON_S or (self._compact and b > self.BACKLOG_OFF_S)
        if self._compact and not was:
            self.log(f"[me] 语音积压 {b:.1f}s：精简译文、语速 x{self.BACKLOG_SPEED}")
            self.emit("notice", text="翻译语音积压，已自动精简译文并稍微加快语速，追上后恢复")
        return self._compact

    def _voicer(self, vq: queue.Queue) -> None:
        models.keep_resident(True)
        try:
            self._voice_loop(vq)
        finally:
            models.keep_resident(False)

    def _voice_loop(self, vq: queue.Queue) -> None:
        while True:
            item = vq.get()
            if item is None:
                return
            kind, job = item
            self._synth_idle.clear()
            try:
                if kind == "swap":
                    self._swap_voice()
                else:
                    self._speak(job)
            except Exception as e:  # noqa: BLE001 — one bad sentence must not silence the rest
                self.log(traceback.format_exc())
                self.emit("notice", text=f"这句语音合成失败：{e}")
            finally:
                if vq.empty():
                    self._synth_idle.set()

    def _speak(self, job: _Speech) -> None:
        s, voice = self.s, self._voice
        if job.state != "go":  # a guess: its first words may be ready before it is known to stand
            job.decided.wait(10)
            if job.state != "go":
                return
        first_audio = None
        late = None  # how long this sentence waits behind earlier speech (known at its first sound)
        native = getattr(voice, "native_speed", False)
        base = getattr(voice, "speed", 1.0)
        try:
            while True:
                part = job.parts.get()
                if part is None:
                    break
                if self.muted or voice is None or voice is not self._voice:  # muted / voice switched
                    continue
                if native:  # these voices take their rate per sentence part
                    ahead = max(0.0, self._play_until - time.monotonic()) if late is None else late
                    voice.speed = base * self._catch_up(ahead + self._waiting_s)
                text = spoken_en(part) if s.their_lang == "en" else part
                for chunk, sr in trim_silence(voice.stream(text, s.their_lang)):
                    if late is None:  # heard once what is already queued has played
                        late = max(0.0, self._play_until - time.monotonic())
                        first_audio = time.time() + late
                    if not native:
                        chunk = time_stretch(chunk, sr, base * self._catch_up(late + self._waiting_s))
                    self._player.put(chunk, sr)
                    self._play_until = max(time.monotonic(), self._play_until) + len(chunk) / sr
        finally:
            if native and voice is not None:
                voice.speed = base
            self._pending_s = max(0.0, self._pending_s - speech_seconds(job.out))
        if not job.out:
            return  # failed or empty: already reported by the translator side
        utt, t_end = job.utt, job.utt.t_end
        spoke_at = job.t_first_text or (job.t0 + job.t_tr)
        translate_s = spoke_at - job.t0
        voice_s = first_audio - spoke_at if first_audio else None  # includes waiting for earlier speech
        latency = (first_audio or time.time()) - t_end
        head = f" 抢跑 {t_end - job.t0:.2f}s" if job.guess else ""
        self.log(f"[me] 排队 {job.waited:.1f}s 翻译 {job.t_tr:.2f}s（先念 {translate_s:.2f}s）"
                 f" 出声 {voice_s if voice_s is not None else -1:.2f}s{head} 说完到出声 {latency:.2f}s:"
                 f" {job.out[:40]!r}")
        self.emit("me", src=utt.text, tgt=job.out, latency=latency, asr_s=utt.asr_s,
                  queue_s=max(0.0, job.waited - utt.asr_s), translate_s=translate_s, voice_s=voice_s,
                  passthrough=False)

    def _do_them(self, tr: Translator, utt: Utterance) -> None:
        s = self.s
        src = utt.lang if utt.lang in LANGS else s.their_lang
        t0 = time.time()
        utt.text = correct_asr(utt.text, tr.glossary, "them")
        try:
            if t0 - utt.t_end > self.STALE_THEM_S:
                raise TranslateError("timeout", "积压太多，已跳过这句")
            ctx = self._context("them", utt)
            out = tr.translate(utt.text, src, s.my_lang, subtitle=True, context=ctx,
                               history=self._history("them", utt, ctx, tr), on_text=self._live_them(utt))
        except TranslateError as e:
            self.log(f"[them] 未翻译 {e.kind}: {e}")
            self.emit("them", src=utt.text, tgt=f"⚠ {e}", latency=time.time() - utt.t_end, asr_s=utt.asr_s,
                      queue_s=max(0.0, t0 - utt.t_end - utt.asr_s), translate_s=time.time() - t0, failed=True)
            return
        t_tr = time.time() - t0
        self.log(f"[them] 排队 {t0 - utt.t_end:.1f}s 翻译 {t_tr:.2f}s: {out[:40]!r}")
        self._remember("them", utt.text, out, utt.t_end)
        self.emit("them", src=utt.text, tgt=out, latency=time.time() - utt.t_end, asr_s=utt.asr_s,
                  queue_s=max(0.0, t0 - utt.t_end - utt.asr_s), translate_s=t_tr)
        self._transcript.add("them", utt.text, out)

    LIVE_EVERY_S, LIVE_MIN_CHARS = 0.12, 4

    def _live_them(self, utt: Utterance) -> Callable[[str], None]:
        """The other side's translation as it is written, a few times a second: the subtitle shows
        the first words ~0.4 s in instead of after the whole sentence (1.75 s on average)."""
        last = [0.0]

        def show(text: str) -> None:
            now = time.monotonic()
            if len(text) >= self.LIVE_MIN_CHARS and now - last[0] >= self.LIVE_EVERY_S:
                last[0] = now
                self.emit("partial", who="them", src=utt.text, tgt=text + " …", live=True)
        return show

    # ------------------------------------------------------------ listener callbacks
    def reload_voice(self) -> None:
        """New sample / 克隆↔标准 / GPU switch while running: rebuild the voice without a restart."""
        if self.running:
            self._voice_q.put(("swap", None))

    def _swap_voice(self) -> None:
        """Runs on the voice thread (which owns every TTS object). Speech is skipped while swapping."""
        s = self.s
        old, self._voice = self._voice, None
        if old is not None:
            if old.close in self._closers:
                self._closers.remove(old.close)
            try:
                old.close()
            except Exception:  # noqa: BLE001
                pass
        if not (s.speak and LANGS[s.their_lang].tts):
            self.emit("voice", voice="不出声")
            return
        try:
            self.emit("status", text="切换音色…")
            backend = models.voice_backend(s)
            tts_model = {"kokoro": models.KOKORO, "pocket": models.POCKET}.get(backend, models.TTS_CLONE[s.quality])
            if not tts_model.ready():
                raise RuntimeError(f"模型未下载：{tts_model.title}（请运行设置向导）")
            voice = make_voice(backend, tts_model.path)
            voice.speed = s.voice_speed
            for _ in voice.stream("Hello.", s.their_lang):  # warm-up, as at start
                pass
            self._voice = voice
            self._voice_key = (backend, str(tts_model.path))
            self._own(voice.close)
            self.emit("voice", voice="你的克隆音色" if voice.clone else "标准音色")
        except Exception as e:  # noqa: BLE001 — keep translating, just without speech
            self.log(traceback.format_exc())
            self.emit("error", message=f"切换音色失败：{e}")
            self.emit("voice", voice="不出声")

    def set_voice_speed(self, speed: float) -> None:
        """Live speaking-rate change (CPU voices read it per sentence; GPU/MLX clones ignore it)."""
        if self._voice is not None:
            self._voice.speed = speed

    def set_monitor(self, name: str) -> None:
        """Switch the headphone monitor while running; other device changes still need a restart."""
        if not self._player:
            return
        try:
            dev = find_device(name, "output") if name else None
        except LookupError:
            self.emit("status", text=f"监听设备 {name} 未连接，已跳过")
            return
        self._player.set_monitor(dev)
        self.emit("status", text=f"运行中 · 监听：{name or '关'}")

    def _asr_key(self, s: Settings, who: str) -> str:
        """阿里云百炼 key when this side uses online streaming recognition, else ''."""
        if s.asr_mode != "online" or not (s.asr_me if who == "me" else s.asr_them):
            return ""
        from . import keystore

        key = keystore.get("dashscope")
        if not key:
            raise RuntimeError("在线语音识别还没填阿里云百炼的 API key（左侧“在线模型”→“语音识别”里设置）")
        return key

    def _notice(self, text: str) -> None:
        self.emit("notice", text=text)

    INTERIM_EVERY_S, INTERIM_MIN_CHARS = 2.5, 12

    def _on_partial(self, who: str, text: str) -> None:
        """Online recognition: the sentence so far. Subtitles show it at once; with online
        translation a long unfinished sentence also gets a provisional translation every few
        seconds (on its own thread, so my speech never waits for it)."""
        self.emit("partial", who=who, src=text, tgt="")
        if who != "them" or self._interim_tr is None or len(text) < self.INTERIM_MIN_CHARS:
            return
        now = time.time()
        if self._interim_busy or now - self._interim_at < self.INTERIM_EVERY_S:
            return
        self._interim_busy, self._interim_at = True, now
        gen, s = self._them_gen, self.s

        def work():
            try:
                out = self._interim_tr.translate(text, s.their_lang, s.my_lang, subtitle=True)
            except TranslateError:
                out = ""
            finally:
                self._interim_busy = False
            if out and gen == self._them_gen and self.running:  # the sentence is still being spoken
                self.emit("partial", who=who, src=text, tgt=out)
        threading.Thread(target=work, daemon=True, name="interim").start()

    def _on_level(self, who: str, rms: float) -> None:
        self.emit("level", who=who, rms=rms)

    def _on_me(self, utt: Utterance) -> None:
        s = self.s
        if utt.lang == s.their_lang and s.passthrough and self._player and not self.muted:
            # I already speak their language: send my real voice, no translation
            self._player.outs[0].put(utt.audio, 16000)
            self.emit("me", src=utt.text, tgt=utt.text, latency=time.time() - utt.t_end,
                      translate_s=0.0, passthrough=True)
            self._transcript.add("me", utt.text, "（原声透传）")
            self._settle_guess(None)
            self._note_level(utt)
            return
        if is_filler(utt.text):  # "嗯。": nothing to say in the other language
            self._settle_guess(None)
            self.log(f"[me] 语气词，不翻译：{utt.text!r}")
            return
        if self._too_quiet(utt):
            self._settle_guess(None)
            return
        self._note_level(utt)
        if not self._settle_guess(utt):
            self._queue_me("me", utt, utt)

    def _queue_me(self, kind: str, utt: Utterance, item) -> None:
        with self._lock:
            self._waiting_s += len(utt.audio) / 16000
        self._jobs.put((0, next(self._seq), kind, item))

    def _settle_guess(self, utt: Utterance | None) -> bool:
        """The open guess is confirmed by `utt` (the sentence ended there, same words: True, and
        the work done on it stands) or void."""
        with self._lock:
            job, self._guess_job = self._guess_job, None
            if job is None or job.state != "open":
                return False
            if utt is not None and utt.guess and utt.guess == job.guess:
                job.utt.t_end, job.utt.asr_s = utt.t_end, utt.asr_s
                job.state = "go"
            else:
                job.state = "void"
            job.decided.set()
            return job.state == "go"

    def _on_me_guess(self, utt: Utterance | None) -> None:
        """I paused, and this may be the whole sentence: translate it now, speak it only if the
        sentence does end there. None: I went on — the guess is void."""
        self._settle_guess(None)
        s = self.s
        if utt is None or s.mode == "ptt" or (utt.lang == s.their_lang and s.passthrough) or is_filler(utt.text) \
                or self._too_quiet(utt, verdict=False):
            return
        job = _Speech(utt, 0.0, 0.0, guess=utt.guess, state="open")
        with self._lock:
            self._guess_job = job
        self._queue_me("guess", utt, job)

    # ---- my microphone: sounds far quieter than my speech are not for the meeting
    # A faint sound (someone talking nearby, a murmur) was once recognised as a Chinese sentence,
    # translated and spoken into the meeting in my voice. My sentences sit within a few dB of each
    # other; what arrives 14 dB below them is not me talking to the meeting.
    QUIET_RATIO = 0.2
    QUIET_STRIKES, QUIET_STRIKE_S = 4, 60.0  # this many in a row: I moved away from the microphone — relearn

    def _usual_level(self) -> float:
        if len(self._levels) >= 3:
            return float(sorted(self._levels)[len(self._levels) // 2])
        s = self.s
        return s.speech_level if s.speech_level > 0 and s.speech_level_mic == s.mic else 0.0

    def _note_level(self, utt: Utterance) -> None:
        if len(re.sub(r"[\W_]+", "", utt.text)) >= 2:
            self._levels.append(speech_level(utt.audio))
            self._quiet_hits = []

    def _too_quiet(self, utt: Utterance, verdict: bool = True) -> bool:
        """verdict=False only asks (a guess: the finished sentence gets the verdict)."""
        usual = self._usual_level()
        if not self.s.quiet_gate or usual <= 0:
            return False
        level = speech_level(utt.audio)
        if level >= self.QUIET_RATIO * usual:
            return False
        if not verdict:
            return True
        now = time.time()
        self._quiet_hits = [t for t in self._quiet_hits if now - t < self.QUIET_STRIKE_S] + [now]
        if len(self._quiet_hits) >= self.QUIET_STRIKES:
            self.log(f"[me] 连续 {self.QUIET_STRIKES} 段都很轻：按新的音量重新学习（{level:.4f}，原来 {usual:.4f}）")
            self._levels.clear()
            self._levels.extend([level] * 3)
            self._quiet_hits = []
            return False
        self.log(f"[me] 声音太轻，不翻译：{utt.text!r}（{level:.4f}，平时 {usual:.4f}）")
        if now - self._quiet_told > 30:
            self._quiet_told = now
            self.emit("notice", text="有一段声音比你平时说话轻很多，没有翻译。如果是你在说话，请靠近麦克风；"
                                     "这项检查可以在“功能”里关闭")
        return True

    def _save_level(self) -> None:
        s = self.s
        if len(self._levels) >= 5:
            s.speech_level, s.speech_level_mic = round(self._usual_level(), 5), s.mic
            try:
                s.save()
            except OSError:
                pass

    # ---- the other side: a short piece of a sentence waits for the rest
    HOLD_S, HOLD_MAX_S = 0.8, 8.0

    def _take_held(self) -> Utterance | None:
        with self._lock:
            held, self._held = self._held, None
            if self._held_timer is not None:
                self._held_timer.cancel()
                self._held_timer = None
        return held

    def _hold(self, utt: Utterance, wait: float | None = None) -> None:
        with self._lock:
            self._held = utt
            self._held_timer = threading.Timer(self.HOLD_S if wait is None else wait, self._release_held, (utt,))
            self._held_timer.daemon = True
            self._held_timer.start()

    def _release_held(self, utt: Utterance) -> None:
        """Nothing followed in time: the piece goes out alone — unless they are still talking."""
        with self._lock:
            if self._held is not utt or not self.running:
                return
            talking = self._them is not None and self._them.speaking()
            if talking and time.time() - utt.t_end < self.HOLD_MAX_S:
                self._hold(utt, 0.3)
                return
            self._held = self._held_timer = None
        self._queue_them(utt)

    def _queue_them(self, utt: Utterance) -> None:
        self._jobs.put((1, next(self._seq), "them", utt))

    def _on_them(self, utt: Utterance) -> None:
        self._them_gen += 1  # provisional translations of this sentence are now out of date
        utt.text = tidy_asr(utt.text)
        same = utt.lang in SIMILAR.get(self.s.my_lang, {self.s.my_lang})
        held = self._take_held()
        if held is not None:
            if same or is_filler(utt.text):
                self._queue_them(held)  # nothing to join it to
            else:
                sep = " " if held.text[-1:].isascii() else ""
                utt = Utterance(held.text.rstrip(" .。,，") + sep + utt.text, utt.lang,
                                np.concatenate([held.audio, utt.audio]), utt.t_end, utt.asr_s)
                self.log(f"[them] 并入上一小段：{utt.text!r}")
        if same:
            self.emit("them", src=utt.text, tgt=utt.text, latency=0.0)  # same language: show as-is
            self._transcript.add("them", utt.text, utt.text)
            return
        if is_filler(utt.text):
            self.log(f"[them] 语气词，不翻译：{utt.text!r}")
            return
        if is_fragment(utt.text):
            self._hold(utt)
            return
        self._queue_them(utt)

    def _on_ptt(self, down: bool) -> None:
        if self.s.mode != "ptt":
            return
        self.ptt_down = down
        self.emit("ptt", down=down)
        if not down and self._me:
            self._me.flush()
