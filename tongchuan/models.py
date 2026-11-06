"""Model registry per platform + resumable downloader (ModelScope / GitHub).

HuggingFace is often unreachable from mainland China, so LLM/TTS weights come
from ModelScope's CDN and the small speech models from GitHub releases.
"""
from __future__ import annotations

import json
import tarfile
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import IS_MAC, MODELS, has_nvidia_gpu

SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MS_API = "https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Recursive=true"
MS_FILE = "https://modelscope.cn/models/{repo}/resolve/master/{path}"
UA = {"User-Agent": "TongChuan/0.2 (model downloader)"}


@dataclass
class Model:
    key: str
    title: str
    dest: str                   # folder (or file) name under MODELS
    modelscope: str = ""        # repo id, downloads the whole repo (or `files`)
    files: list[str] = field(default_factory=list)  # subset of the repo; empty = all
    url: str = ""               # single file / tar.bz2 archive
    approx_mb: int = 0

    @property
    def path(self) -> Path:
        return MODELS / self.dest

    def ready(self) -> bool:
        p = self.path
        if not p.exists():
            return False
        return (p / ".complete").exists() if p.is_dir() else p.stat().st_size > 0


ASR = Model("asr", "语音识别 SenseVoice", "sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17",
            url=f"{SHERPA}/asr-models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17.tar.bz2",
            approx_mb=230)
VAD = Model("vad", "断句 Silero VAD", "silero_vad.onnx", url=f"{SHERPA}/asr-models/silero_vad.onnx",
            approx_mb=1)

if IS_MAC:
    LLM = {
        "fast": Model("llm", "翻译 Qwen3-4B (MLX)", "Qwen3-4B-MLX-4bit", modelscope="Qwen/Qwen3-4B-MLX-4bit",
                      approx_mb=2100),
        "quality": Model("llm", "翻译 Qwen3-8B (MLX)", "Qwen3-8B-MLX-4bit", modelscope="Qwen/Qwen3-8B-MLX-4bit",
                         approx_mb=4300),
    }
    TTS_CLONE = {
        "fast": Model("tts", "克隆音色 Qwen3-TTS 0.6B (MLX)", "Qwen3-TTS-12Hz-0.6B-Base-8bit",
                      modelscope="mlx-community/Qwen3-TTS-12Hz-0.6B-Base-8bit", approx_mb=1900),
        "quality": Model("tts", "克隆音色 Qwen3-TTS 1.7B (MLX)", "Qwen3-TTS-12Hz-1.7B-Base-8bit",
                         modelscope="mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit", approx_mb=3000),
    }
else:
    LLM = {
        "fast": Model("llm", "翻译 Qwen3-4B (GGUF)", "Qwen3-4B-Q4_K_M.gguf", modelscope="Qwen/Qwen3-4B-GGUF",
                      files=["Qwen3-4B-Q4_K_M.gguf"], approx_mb=2400),
        "quality": Model("llm", "翻译 Qwen3-8B (GGUF)", "Qwen3-8B-Q4_K_M.gguf", modelscope="Qwen/Qwen3-8B-GGUF",
                         files=["Qwen3-8B-Q4_K_M.gguf"], approx_mb=4800),
    }
    TTS_CLONE = {
        "fast": Model("tts", "克隆音色 Qwen3-TTS 0.6B (CUDA)", "Qwen3-TTS-12Hz-0.6B-Base",
                      modelscope="Qwen/Qwen3-TTS-12Hz-0.6B-Base", approx_mb=2400),
        "quality": Model("tts", "克隆音色 Qwen3-TTS 1.7B (CUDA)", "Qwen3-TTS-12Hz-1.7B-Base",
                         modelscope="Qwen/Qwen3-TTS-12Hz-1.7B-Base", approx_mb=4300),
    }

# CPU-only fallback voice (not your voice): Kokoro via sherpa-onnx
KOKORO = Model("tts", "标准音色 Kokoro (CPU)", "kokoro-int8-multi-lang-v1_1",
               url=f"{SHERPA}/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2", approx_mb=140)


def voice_backend() -> str:
    """'mlx' (Apple Silicon), 'cuda' (Windows + NVIDIA) or 'kokoro' (CPU fallback)."""
    if IS_MAC:
        return "mlx"
    return "cuda" if has_nvidia_gpu() else "kokoro"


def required(quality: str) -> list[Model]:
    tts = KOKORO if voice_backend() == "kokoro" else TTS_CLONE[quality]
    return [VAD, ASR, LLM[quality], tts]


# ------------------------------------------------------------------ downloader
Progress = Callable[[str, int, int], None]  # (title, done_bytes, total_bytes)


class Cancelled(Exception):
    pass


def _get_json(url: str):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


def _fetch(url: str, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event) -> None:
    """Download with resume (.part + HTTP Range)."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have:
        on_bytes(have)
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        if have and r.status != 206:  # server ignored Range: start over
            on_bytes(-have)
            have = 0
        with open(part, "ab" if have else "wb") as f:
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                on_bytes(len(chunk))
    part.rename(dest)


def download(model: Model, progress: Progress, cancel: threading.Event | None = None) -> None:
    cancel = cancel or threading.Event()
    if model.ready():
        return
    lock = threading.Lock()
    state = {"done": 0, "total": model.approx_mb << 20}

    def bump(n: int) -> None:
        with lock:
            state["done"] += n
            progress(model.title, state["done"], state["total"])

    if model.url and model.url.endswith(".tar.bz2"):
        tar = MODELS / (model.dest + ".tar.bz2")
        _fetch(model.url, tar, bump, cancel)
        progress(model.title + "（解压中）", state["done"], state["total"])
        with tarfile.open(tar) as t:
            t.extractall(MODELS, filter="data")
        tar.unlink()
        (model.path / ".complete").touch()
    elif model.url:
        _fetch(model.url, model.path, bump, cancel)
    else:
        listing = _get_json(MS_API.format(repo=model.modelscope))
        items = [f for f in listing["Data"]["Files"] if f.get("Type") != "tree"]
        if model.files:
            items = [f for f in items if f["Path"] in model.files]
        state["total"] = sum(int(f["Size"]) for f in items) or state["total"]
        single_file = len(model.files) == 1
        def one(f):
            dest = model.path if single_file else model.path / f["Path"]
            _fetch(MS_FILE.format(repo=model.modelscope, path=f["Path"]), dest, bump, cancel)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, items))
        if not single_file:
            (model.path / ".complete").touch()
    progress(model.title, state["total"], state["total"])


def adopt_existing(src_dir: Path) -> list[str]:
    """Move models from an older install (e.g. the zh2en-voice prototype) into MODELS."""
    moved = []
    for m in [VAD, ASR, *LLM.values(), *TTS_CLONE.values(), KOKORO]:
        src = src_dir / m.dest
        if src.exists() and not m.path.exists():
            src.rename(m.path)
            if m.path.is_dir():
                (m.path / ".complete").touch()
            moved.append(m.dest)
    return moved
