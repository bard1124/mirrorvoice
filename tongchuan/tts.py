"""Speech output backends.

- MLXCloneVoice:  Qwen3-TTS voice clone on Apple Silicon (streaming).
- CudaCloneVoice: Qwen3-TTS voice clone on Windows + NVIDIA, served by tts_server.py running in
                  the separately installed "GPU voice pack" (torch + qwen-tts).
- KokoroVoice:    standard English voice on CPU (not your voice) — Windows without NVIDIA.
All yield (float32 mono chunk, sample_rate).
"""
from __future__ import annotations

import json
import struct
import subprocess
import sys
from pathlib import Path
from typing import Iterator

import numpy as np
import soundfile as sf

from .config import HOME, IS_WIN, VOICE
from .languages import LANGS

REF_WAV, REF_TXT = VOICE / "ref.wav", VOICE / "ref.txt"
GPU_PACK = HOME / "gpu-voice"  # venv with torch + qwen-tts (Windows)


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

    def stream(self, text: str, lang: str, interval: float = 0.8) -> Iterator[tuple[np.ndarray, int]]:
        for r in self.model.generate(text=text, ref_audio=self.ref_audio, ref_text=self.ref_text,
                                     lang_code=LANGS[lang].tts or "auto", stream=True,
                                     streaming_interval=interval):
            yield np.asarray(r.audio, dtype=np.float32), self.model.sample_rate

    def close(self) -> None:
        pass


class KokoroVoice:
    clone = False

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
        a = self.tts.generate(text, sid=self.sid, speed=1.0)
        yield np.asarray(a.samples, dtype=np.float32), a.sample_rate

    def close(self) -> None:
        pass


def gpu_pack_python() -> Path:
    return GPU_PACK / ("Scripts/python.exe" if IS_WIN else "bin/python")


class CudaCloneVoice:
    """Talks to tts_server.py over stdin/stdout (JSON request -> header + raw float32 PCM)."""

    clone = True

    def __init__(self, model_path: Path) -> None:
        py = gpu_pack_python()
        if not py.exists():
            raise RuntimeError("未安装 GPU 音色包，请在设置向导里安装（需要 NVIDIA 显卡）")
        _, _, _ = _ref()
        server = Path(__file__).with_name("tts_server.py")
        self.proc = subprocess.Popen(
            [str(py), str(server), str(model_path), str(REF_WAV), str(REF_TXT)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=sys.stderr,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        hello = self.proc.stdout.readline()
        if not hello.startswith(b"READY"):
            raise RuntimeError(f"GPU 音色服务启动失败：{hello[:300]!r}")

    def stream(self, text: str, lang: str, interval: float = 0.8) -> Iterator[tuple[np.ndarray, int]]:
        req = json.dumps({"text": text, "lang": LANGS[lang].tts or "auto"}, ensure_ascii=False)
        self.proc.stdin.write(req.encode("utf-8") + b"\n")
        self.proc.stdin.flush()
        header = self.proc.stdout.read(8)
        sr, n = struct.unpack("<II", header)
        data = self.proc.stdout.read(n * 4)
        yield np.frombuffer(data, dtype=np.float32), sr

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()


def make_voice(backend: str, model_path: Path):
    if backend == "mlx":
        return MLXCloneVoice(model_path)
    if backend == "cuda":
        return CudaCloneVoice(model_path)
    return KokoroVoice(model_path)
