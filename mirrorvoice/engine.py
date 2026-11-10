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
import traceback
from typing import Callable

from . import models
from .asr import Listener, Utterance
from .audio import Player, find_device, time_stretch
from .config import LOG_FILE, Settings
from . import hotkeys
from .hotkeys import Hotkeys
from .languages import LANGS
from .transcript import Transcript
from .online_llm import FailoverChat, OnlineChat, TranslateError
from .translate import Translator, correct_asr, local_backend, spoken_en
from .tts import make_voice

Emit = Callable[..., None]  # emit(kind: str, **data)

SIMILAR = {"zh": {"zh", "yue"}, "yue": {"yue", "zh"}}


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


class Engine:
    def __init__(self, emit: Emit) -> None:
        self.emit = emit
        self.running = False
        self.muted = False
        # exist before the first start(): the UI may call set_voice_speed / set_monitor / reload_voice early
        self._player = self._voice = self._transcript = self._me = None
        self._prev = {"me": None, "them": None}
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
        self.s = s
        self.running = True
        self.muted = False
        self._player = self._voice = self._transcript = self._me = None
        self._ready = False
        self._prev = {"me": None, "them": None}  # (source, translation, t_end) of the last part
        # A fresh queue per session: the previous worker may still be busy (and the previous
        # stop() left a "stop" job behind). Sharing the queue let a new worker swallow that stale
        # "stop" and exit while its listeners kept running — "nothing happens" until restart.
        self._jobs = queue.PriorityQueue()
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
        threading.Thread(target=self._boot, daemon=True, name="engine-boot").start()

    def stop(self) -> None:
        if not self.running:
            return
        self.running = False
        self._jobs.put((99, next(self._seq), "stop", None))
        for close in reversed(self._closers):
            try:
                close()
            except Exception:  # noqa: BLE001 — best-effort teardown
                pass
        self._closers.clear()
        self.emit("stopped")

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        self.emit("mute", muted=muted)

    def reload_glossary(self) -> None:
        self._jobs.put((0, next(self._seq), "glossary", None))

    # ------------------------------------------------------------ internals
    def log(self, msg: str) -> None:
        if self._log:
            self._log.write(f"{time.strftime('%H:%M:%S')} {msg}\n")

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

    def _boot(self) -> None:
        try:
            self._worker()
        except Exception as e:  # noqa: BLE001 — surface to UI instead of dying silently
            self.log(traceback.format_exc())
            self.emit("error", message=str(e))
            if self.running:
                self.stop()

    def _worker(self) -> None:
        s = self.s
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
        if s.speak and their_tts:
            self.emit("status", text="加载语音合成…")
            tts_model = {"kokoro": models.KOKORO, "pocket": models.POCKET}.get(backend, models.TTS_CLONE[s.quality])
            voice = make_voice(backend, tts_model.path)
            voice.speed = s.voice_speed
            self._closers.append(voice.close)
        if s.speak or s.passthrough:
            self.emit("status", text="打开音频设备…")
            mon = None
            if s.monitor:
                try:
                    mon = find_device(s.monitor, "output")
                except LookupError:
                    self.emit("status", text=f"监听设备 {s.monitor} 未连接，已跳过")
            player = Player(find_device(s.call_out, "output"), mon, s.gain)
            self._closers.append(player.close)

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

        transcript = Transcript(s.my_lang, s.their_lang)
        self._transcript = transcript
        self._player, self._voice = player, voice

        me = Listener("me", s.mic, "auto", s.pause, self._on_me, self._on_level, self._gate, self.log)
        me.start()
        self._closers.append(me.close)
        self._me = me
        them = None
        if s.subtitles and s.their_source:
            try:
                them = Listener("them", s.their_source, "auto", s.pause, self._on_them,
                                self._on_level, self._them_gate, self.log)
                them.start()
                self._closers.append(them.close)
            except Exception as e:  # noqa: BLE001 — subtitles are optional
                self.emit("status", text=f"对方声音来源不可用（{e}），字幕关闭")

        hk = Hotkeys(s.ptt_key, s.mute_hotkey, self._on_ptt, lambda: self.set_muted(not self.muted))
        try:
            hk.start()
            self._closers.append(hk.stop)
        except Exception as e:  # noqa: BLE001 — no permission: hotkeys just don't work
            self.emit("status", text=f"全局快捷键不可用：{e}")

        self._ready = True
        self.emit("ready", mic=me.label, call=s.call_out, them=them.label if them else "",
                  voice="你的克隆音色" if (voice and voice.clone) else ("标准音色" if voice else "不出声"),
                  transcript=str(transcript.md), llm=tr.label)

        jobs = self._jobs  # bind this session's queue: start() replaces the attribute
        while self.running:
            prio, _, kind, utt = jobs.get()
            if kind == "stop":
                break
            if kind == "glossary":
                for t in (tr, tr_them):
                    t.reload_glossary()
                    t.meeting = s.meeting_context
                continue
            if kind == "voice":
                self._swap_voice()
                continue
            if kind == "me":
                self._do_me(tr, utt)
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
                self.emit("notice" if self._ready else "status", text="加载本地翻译模型…")
                shared["b"] = local_backend(local_path)
                self._closers.append(shared["b"].close)
            return shared["b"]

        if s.llm_mode != "online":
            self.emit("status", text="加载翻译模型…")
            t = Translator(local(), s.meeting_context)
            return t, t
        self.emit("status", text="连接在线翻译…")
        try:
            online = OnlineChat.from_settings(s)
        except TranslateError as e:
            raise RuntimeError(f"在线翻译设置不完整：{e}（左侧“在线模型”里设置）") from None
        fallback = local if models.LLM[s.quality].ready() else None
        mine = Translator(FailoverChat(online, fallback, lambda m: self.emit("notice", text=m)), s.meeting_context)
        if s.online_their or not (s.subtitles and s.their_source):
            return mine, mine
        self.emit("status", text="加载本地翻译模型（对方字幕）…")
        return mine, Translator(local(), s.meeting_context)

    CONTINUATION_GAP_S = 1.5  # a part starting this soon after the previous one continues it
    STALE_THEM_S = 6.0        # subtitles this far behind are skipped instead of piling up behind my speech

    def _context(self, who: str, utt: Utterance) -> tuple[str, str] | None:
        prev = self._prev.get(who)
        if not prev:
            return None
        start = utt.t_end - len(utt.audio) / 16000
        return (prev[0], prev[1]) if start - prev[2] < self.CONTINUATION_GAP_S else None

    def _do_me(self, tr: Translator, utt: Utterance) -> None:
        s = self.s
        t0 = time.time()
        waited = t0 - utt.t_end  # time the sentence sat in the queue behind earlier work
        utt.text = correct_asr(utt.text, tr.glossary, "me")
        try:
            out = tr.translate(utt.text, s.my_lang, s.their_lang, context=self._context("me", utt))
        except TranslateError as e:  # nothing is spoken; the sentence shows as failed
            self.log(f"[me] 在线翻译失败 {e.kind}: {e}")
            self.emit("me", src=utt.text, tgt=f"⚠ 翻译失败：{e}", latency=time.time() - utt.t_end,
                      asr_s=utt.asr_s, queue_s=max(0.0, waited - utt.asr_s), translate_s=time.time() - t0,
                      voice_s=None, passthrough=False, failed=True)
            return
        t_tr = time.time() - t0
        if not out:
            return
        self._prev["me"] = (utt.text, out, utt.t_end)
        latency = voice_s = None
        if self._voice and not self.muted:  # muted: drop speech that was already queued
            text = spoken_en(out) if s.their_lang == "en" else out
            for chunk, sr in self._voice.stream(text, s.their_lang):
                if latency is None:
                    latency = time.time() - utt.t_end
                    voice_s = time.time() - t0 - t_tr
                if not getattr(self._voice, "native_speed", False):
                    chunk = time_stretch(chunk, sr, self._voice.speed)
                self._player.put(chunk, sr)
        self.log(f"[me] 排队 {waited:.1f}s 翻译 {t_tr:.2f}s 合成 {time.time() - t0 - t_tr:.2f}s: {out[:40]!r}")
        self.emit("me", src=utt.text, tgt=out, latency=latency or (time.time() - utt.t_end),
                  asr_s=utt.asr_s, queue_s=max(0.0, waited - utt.asr_s), translate_s=t_tr, voice_s=voice_s,
                  passthrough=False)
        self._transcript.add("me", utt.text, out)

    def _do_them(self, tr: Translator, utt: Utterance) -> None:
        s = self.s
        src = utt.lang if utt.lang in LANGS else s.their_lang
        t0 = time.time()
        utt.text = correct_asr(utt.text, tr.glossary, "them")
        try:
            if t0 - utt.t_end > self.STALE_THEM_S:
                raise TranslateError("timeout", "积压太多，已跳过这句")
            out = tr.translate(utt.text, src, s.my_lang, subtitle=True, context=self._context("them", utt))
        except TranslateError as e:
            self.log(f"[them] 未翻译 {e.kind}: {e}")
            self.emit("them", src=utt.text, tgt=f"⚠ {e}", latency=time.time() - utt.t_end, asr_s=utt.asr_s,
                      queue_s=max(0.0, t0 - utt.t_end - utt.asr_s), translate_s=time.time() - t0, failed=True)
            return
        t_tr = time.time() - t0
        self.log(f"[them] 排队 {t0 - utt.t_end:.1f}s 翻译 {t_tr:.2f}s: {out[:40]!r}")
        self._prev["them"] = (utt.text, out, utt.t_end)
        self.emit("them", src=utt.text, tgt=out, latency=time.time() - utt.t_end, asr_s=utt.asr_s,
                  queue_s=max(0.0, t0 - utt.t_end - utt.asr_s), translate_s=t_tr)
        self._transcript.add("them", utt.text, out)

    # ------------------------------------------------------------ listener callbacks
    def reload_voice(self) -> None:
        """New sample / 克隆↔标准 / GPU switch while running: rebuild the voice without a restart."""
        if self.running:
            self._jobs.put((0, next(self._seq), "voice", None))

    def _swap_voice(self) -> None:
        """Runs on the model thread (which owns every TTS object). Speech is skipped while swapping."""
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
            self._closers.append(voice.close)
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
            return
        self._jobs.put((0, next(self._seq), "me", utt))

    def _on_them(self, utt: Utterance) -> None:
        if utt.lang in SIMILAR.get(self.s.my_lang, {self.s.my_lang}):
            self.emit("them", src=utt.text, tgt=utt.text, latency=0.0)  # same language: show as-is
            self._transcript.add("them", utt.text, utt.text)
            return
        self._jobs.put((1, next(self._seq), "them", utt))

    def _on_ptt(self, down: bool) -> None:
        if self.s.mode != "ptt":
            return
        self.ptt_down = down
        self.emit("ptt", down=down)
        if not down and self._me:
            self._me.flush()
