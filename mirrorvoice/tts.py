"""Speech output backends.

- MLXCloneVoice:  Qwen3-TTS voice clone on Apple Silicon (streaming).
- CudaCloneVoice: Qwen3-TTS voice clone on Windows + NVIDIA, served by tts_server.py running in
                  the separately installed "GPU voice pack" (torch + qwen-tts).
- KokoroVoice:    standard English voice on CPU (not your voice) — Windows without NVIDIA.
All yield (float32 mono chunk, sample_rate).
"""
from __future__ import annotations

import json
import math
import re
import struct
import subprocess
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from .config import GPU_PACK_DONE, TTS_SERVER_LOG, VOICE, gpu_pack_python, kill_with_parent
from .languages import LANGS

REF_WAV, REF_TXT, REF_LANG = VOICE / "ref.wav", VOICE / "ref.txt", VOICE / "ref.lang"


_CLAUSE_SPLIT = re.compile(r"(?<=[,;:.!?，。！？；：、])\s*")


def _units(s: str) -> int:
    return len(s.split()) if re.search(r"[A-Za-z]", s) else len(s)


def clauses(text: str, min_units: int = 4) -> list[str]:
    """Cut a sentence at punctuation so the first clause can be voiced while the rest is still
    being synthesised — the non-streaming voices (Pocket / Kokoro / Qwen3-TTS CUDA) otherwise
    stay silent until the whole sentence is done. Tiny fragments are merged into their
    neighbour so the model never gets a one-word prompt."""
    parts = [p.strip() for p in _CLAUSE_SPLIT.split(text) if p and p.strip()]
    out: list[str] = []
    for p in parts:
        if out and (_units(out[-1]) < min_units or _units(p) < 2):
            out[-1] = f"{out[-1]} {p}".strip()
        else:
            out.append(p)
    if len(out) > 1 and _units(out[-1]) < 2:
        out[-2], out[-1] = f"{out[-2]} {out[-1]}".strip(), ""
        out.pop()
    return out or [text]


def has_voice_sample() -> bool:
    return REF_WAV.exists() and REF_TXT.exists() and REF_TXT.read_text(encoding="utf-8").strip() != ""


def _ref() -> tuple[np.ndarray, int, str]:
    if not has_voice_sample():
        raise RuntimeError("还没有录音色样本，请先在“音色”里录一段你的声音")
    audio, sr = sf.read(REF_WAV, dtype="float32")
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    return audio, sr, REF_TXT.read_text(encoding="utf-8").strip()


class MLXCloneVoice:
    clone = True

    def __init__(self, model_path: Path) -> None:
        import mlx.core as mx
        from mlx_audio.tts.utils import load_model

        from .audio import resample

        self.model = load_model(model_path)
        audio, sr, self.ref_text = _ref()
        self.ref_audio = mx.array(resample(audio, sr, self.model.sample_rate))

    # 0.4 s chunks: the first sound ~0.2 s sooner than 0.8 s (0.54 vs 0.74 s on an M1 Pro); 0.2 s
    # gained nothing more and made the whole sentence slower
    def stream(self, text: str, lang: str, interval: float = 0.4) -> Iterator[tuple[np.ndarray, int]]:
        for r in self.model.generate(text=text, ref_audio=self.ref_audio, ref_text=self.ref_text,
                                     lang_code=LANGS[lang].tts or "auto", stream=True,
                                     streaming_interval=interval):
            yield np.asarray(r.audio, dtype=np.float32), self.model.sample_rate

    def close(self) -> None:
        pass


class KokoroVoice:
    clone = False
    speed = 1.0  # set from Settings.voice_speed by the engine
    native_speed = True  # the model takes the rate itself; the engine must not stretch again

    def __init__(self, model_path: Path, sid: int = 0) -> None:
        import sherpa_onnx as so

        d = model_path
        cfg = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
            kokoro=so.OfflineTtsKokoroModelConfig(
                model=str(d / "model.int8.onnx"), voices=str(d / "voices.bin"),
                tokens=str(d / "tokens.txt"), data_dir=str(d / "espeak-ng-data"),
                dict_dir=str(d / "dict"), lexicon=f"{d / 'lexicon-us-en.txt'},{d / 'lexicon-zh.txt'}"),
            num_threads=4))
        self.tts, self.sid = so.OfflineTts(cfg), sid

    def stream(self, text: str, lang: str, interval: float = 0.8) -> Iterator[tuple[np.ndarray, int]]:
        for part in clauses(text):
            a = self.tts.generate(part, sid=self.sid, speed=self.speed)
            yield np.asarray(a.samples, dtype=np.float32), a.sample_rate

    def close(self) -> None:
        pass


def sample_language() -> str:
    """Language the voice sample was read in ('' = unknown, recorded before this was tracked)."""
    return REF_LANG.read_text(encoding="utf-8").strip() if REF_LANG.exists() else ""


class PocketCloneVoice:
    """Pocket TTS voice clone on CPU (sherpa-onnx). English output only.

    With a Chinese reference recording the model often never stops (30-40 s of noise), so the
    sample must be read in English; output is also capped by text length as a safety net.
    """

    clone = True
    speed = 1.0  # set from Settings.voice_speed by the engine

    def __init__(self, model_path: Path) -> None:
        import sherpa_onnx as so

        if sample_language() != "en":
            raise RuntimeError("CPU 版克隆音色需要“用英文朗读”的样本，请在“音色”里用英文重录一次"
                               "（或在设置里改用标准音色）")
        d = model_path
        cfg = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
            pocket=so.OfflineTtsPocketModelConfig(
                lm_flow=str(d / "lm_flow.int8.onnx"), lm_main=str(d / "lm_main.int8.onnx"),
                encoder=str(d / "encoder.onnx"), decoder=str(d / "decoder.int8.onnx"),
                text_conditioner=str(d / "text_conditioner.onnx"), vocab_json=str(d / "vocab.json"),
                token_scores_json=str(d / "token_scores.json")),
            num_threads=4))
        self.tts = so.OfflineTts(cfg)
        audio, sr, _ = _ref()
        self.g = so.GenerationConfig()
        self.g.reference_audio = audio[: sr * 10]
        self.g.reference_sample_rate = sr
        self.g.num_steps = 5

    def stream(self, text: str, lang: str, interval: float = 0.8) -> Iterator[tuple[np.ndarray, int]]:
        for part in clauses(text):
            yield self._one(part)

    def _one(self, text: str) -> tuple[np.ndarray, int]:
        cap_s = 2.0 + 0.1 * len(text)  # runaway guard
        # Stop the language model at the cap instead of only trimming afterwards: with a reference
        # that is not English it often never emits EOS and spent ~15 s producing 40 s of noise
        # (the LM runs to the end before the first audio callback). Mimi codec: 12.5 frames/s.
        self.g.extra = {"max_frames": str(math.ceil(cap_s * 12.5))}
        got = [0]

        def cb(samples, _progress) -> int:
            got[0] += len(samples)
            return 0 if got[0] > cap_s * 24000 else 1

        a = self.tts.generate(text, self.g, cb)
        w = np.asarray(a.samples, dtype=np.float32)[: int(cap_s * a.sample_rate)]
        return w, a.sample_rate

    def close(self) -> None:
        pass


class CudaCloneVoice:
    """Talks to tts_server.py over stdin/stdout (JSON request -> header + raw float32 PCM)."""

    clone = True

    def __init__(self, model_path: Path) -> None:
        py = gpu_pack_python()
        if not py.exists():
            raise RuntimeError("未安装 GPU 音色包，请在设置向导里安装（需要 NVIDIA 显卡）")
        if not GPU_PACK_DONE.exists():
            raise RuntimeError("GPU 音色包尚未安装完成（仍在安装中，或上次安装中断），"
                               "请在设置向导里等待安装结束或重新安装")
        _, _, _ = _ref()
        server = Path(__file__).with_name("tts_server.py")
        # stderr goes to a file, not the parent's: the windowed app has no console, so a crash in
        # tts_server.py used to surface as an empty b'' with nothing to debug from.
        self._err = open(TTS_SERVER_LOG, "wb")
        self.proc = subprocess.Popen(
            [str(py), str(server), str(model_path), str(REF_WAV), str(REF_TXT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._err,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        kill_with_parent(self.proc)
        hello = self.proc.stdout.readline()
        if not hello.startswith(b"READY"):
            self.close()
            raise RuntimeError(f"GPU 音色服务启动失败：{hello[:300]!r}\n{self._stderr_tail()}\n"
                               f"完整输出见 {TTS_SERVER_LOG}")

    def _stderr_tail(self, n: int = 1500) -> str:
        try:
            data = TTS_SERVER_LOG.read_bytes()
        except OSError:
            return ""
        return data[-n:].decode("utf-8", errors="replace").strip()

    def stream(self, text: str, lang: str, interval: float = 0.8) -> Iterator[tuple[np.ndarray, int]]:
        for part in clauses(text):
            req = json.dumps({"text": part, "lang": LANGS[lang].tts or "auto"}, ensure_ascii=False)
            self.proc.stdin.write(req.encode("utf-8") + b"\n")
            self.proc.stdin.flush()
            header = self.proc.stdout.read(8)
            sr, n = struct.unpack("<II", header)
            data = self.proc.stdout.read(n * 4)
            yield np.frombuffer(data, dtype=np.float32), sr

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if not self._err.closed:
            self._err.close()


def make_voice(backend: str, model_path: Path):
    if backend == "mlx":
        return MLXCloneVoice(model_path)
    if backend == "cuda":
        return CudaCloneVoice(model_path)
    if backend == "pocket":
        return PocketCloneVoice(model_path)
    return KokoroVoice(model_path)
