"""Model registry per platform + resumable downloader (ModelScope / HuggingFace / GitHub).

LLM/TTS weights exist under the same repo id on ModelScope (fast in mainland China) and HuggingFace
(fast abroad, often unreachable in China): both are speed-probed and the faster one is used. Small
speech models come from GitHub releases. Big files are fetched over several connections at once.
"""
from __future__ import annotations

import json
import os
import re
import tarfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import IS_MAC, MODELS, cuda_voice_ok
from .net import urlopen

SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download"
MS_API = "https://modelscope.cn/api/v1/models/{repo}/repo/files?Revision=master&Recursive=true"
MS_FILE = "https://modelscope.cn/models/{repo}/resolve/master/{path}"
UA = {"User-Agent": "MirrorVoice/0.2 (model downloader)"}


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
        "turbo": Model("llm", "翻译 Qwen3-1.7B (MLX)", "Qwen3-1.7B-MLX-4bit", modelscope="Qwen/Qwen3-1.7B-MLX-4bit",
                       approx_mb=900),
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
        "turbo": Model("llm", "翻译 Qwen3-1.7B (GGUF)", "Qwen3-1.7B-Q8_0.gguf", modelscope="Qwen/Qwen3-1.7B-GGUF",
                       files=["Qwen3-1.7B-Q8_0.gguf"], approx_mb=1750),
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

TTS_CLONE["turbo"] = TTS_CLONE["fast"]  # 极速 only swaps the translation model for a smaller one

# 极速 / 快速 / 高质量, in the order the UI lists them
QUALITIES = ("turbo", "fast", "quality")
QUALITY_LABEL = {"turbo": "极速", "fast": "快速", "quality": "高质量"}

# CPU voice clone (English output only; reference must be read in English): Pocket TTS via sherpa-onnx.
# Note: this ONNX export is licensed for non-commercial use.
POCKET = Model("tts", "克隆音色 Pocket TTS (CPU，英文)", "sherpa-onnx-pocket-tts-int8-2026-01-26",
               url=f"{SHERPA}/tts-models/sherpa-onnx-pocket-tts-int8-2026-01-26.tar.bz2", approx_mb=93)

# CPU-only fallback voice (not your voice): Kokoro via sherpa-onnx
KOKORO = Model("tts", "标准音色 Kokoro (CPU)", "kokoro-int8-multi-lang-v1_1",
               url=f"{SHERPA}/tts-models/kokoro-int8-multi-lang-v1_1.tar.bz2", approx_mb=140)


def voice_backend(s=None) -> str:
    """'mlx' (Apple Silicon) | 'cuda' (Windows + NVIDIA >= Ampere) | 'pocket' (CPU clone, English) | 'kokoro'."""
    if IS_MAC:
        return "mlx"
    from .config import Settings

    s = s or Settings.load()
    if cuda_voice_ok(s.gpu_voice):
        return "cuda"
    return "pocket" if (s.cpu_voice == "clone" and s.their_lang == "en") else "kokoro"


def required(quality: str) -> list[Model]:
    tts = {"pocket": POCKET, "kokoro": KOKORO}.get(voice_backend(), TTS_CLONE[quality])
    return [VAD, ASR, LLM[quality], tts]


def llm_name(quality: str) -> str:
    """'Qwen3-1.7B' / 'Qwen3-4B' / 'Qwen3-8B' — what the 极速 / 快速 / 高质量 choice actually selects."""
    m = re.search(r"Qwen3-\d+(?:\.\d+)?B", LLM[quality].title)
    return m.group() if m else LLM[quality].title


def summary(quality: str) -> str:
    """'翻译 Qwen3-4B · 语音识别 SenseVoice · 克隆音色 Pocket TTS · 约 2.7 GB' for this machine's backend."""
    ms = required(quality)
    parts = [re.sub(r"\s*\(.*?\)\s*$", "", m.title) for m in ms if m.key != "vad"]
    return " · ".join(parts) + f" · 约 {sum(m.approx_mb for m in ms) / 1024:.1f} GB"


# ------------------------------------------------------------------ downloader
Progress = Callable[[str, int, int], None]  # (title, done_bytes, total_bytes)


class Cancelled(Exception):
    pass


def _get_json(url: str):
    with urlopen(urllib.request.Request(url, headers=UA), timeout=30) as r:
        return json.load(r)


# ---- sources: the same repo id exists on ModelScope (fast in mainland China) and HuggingFace (fast abroad)
HF_API = "https://huggingface.co/api/models/{repo}/tree/main?recursive=1"
HF_FILE = "https://huggingface.co/{repo}/resolve/main/{path}"
SOURCES = ("modelscope", "huggingface")
SOURCE_LABEL = {"modelscope": "ModelScope", "huggingface": "HuggingFace"}
_chosen_source: dict[str, str] = {}  # repo -> source picked by the speed probe (per run)


def file_url(source: str, repo: str, path: str) -> str:
    return (MS_FILE if source == "modelscope" else HF_FILE).format(repo=repo, path=path)


def list_files(source: str, repo: str) -> list[tuple[str, int]]:
    """[(path, size)] of every file in the repo."""
    if source == "modelscope":
        data = _get_json(MS_API.format(repo=repo))["Data"]["Files"]
        return [(f["Path"], int(f["Size"])) for f in data if f.get("Type") != "tree"]
    data = _get_json(HF_API.format(repo=repo))
    return [(f["path"], int(f.get("size") or 0)) for f in data if f.get("type") == "file"]


def _probe_speed(url: str, nbytes: int = 2 << 20, timeout: float = 10) -> float:
    """Bytes/s for the first `nbytes` of `url` (connection setup included); 0 on any failure."""
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes=0-{nbytes - 1}"})
        got = 0
        with urlopen(req, timeout=timeout) as r:
            while got < nbytes and time.time() - t0 < timeout:
                chunk = r.read(256 << 10)
                if not chunk:
                    break
                got += len(chunk)
        return got / max(time.time() - t0, 1e-3)
    except Exception:  # noqa: BLE001 — unreachable source
        return 0.0


def pick_source(repo: str, probe_path: str) -> str:
    """The faster of ModelScope / HuggingFace for this repo (MIRRORVOICE_MODEL_SOURCE overrides)."""
    forced = os.environ.get("MIRRORVOICE_MODEL_SOURCE", "")
    if forced in SOURCES:
        return forced
    if repo in _chosen_source:
        return _chosen_source[repo]
    speeds: dict[str, float] = {}
    threads = [threading.Thread(target=lambda src=src: speeds.__setitem__(
        src, _probe_speed(file_url(src, repo, probe_path)))) for src in SOURCES]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    best = max(SOURCES, key=lambda src: speeds.get(src, 0.0))
    if speeds.get(best, 0.0) <= 0:
        best = "modelscope"  # both probes failed: keep the old default, the real download reports the error
    _chosen_source[repo] = best
    return best


# ---- resumable, optionally segmented download
SEGMENTS = 4              # parallel connections for one big file
SEGMENT_MIN = 64 << 20    # files smaller than this use a single connection


def _range_size(url: str) -> int:
    """Total size if the server honours Range requests, else 0."""
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    with urlopen(req, timeout=30) as r:
        m = re.search(r"/(\d+)\s*$", r.headers.get("Content-Range", ""))
        return int(m.group(1)) if r.status == 206 and m else 0


def _copy(r, f, on_bytes: Callable[[int], None], cancel: threading.Event, limit: int | None = None) -> int:
    n = 0
    while limit is None or n < limit:
        if cancel.is_set():
            raise Cancelled()
        chunk = r.read(1 << 20 if limit is None else min(1 << 20, limit - n))
        if not chunk:
            break
        f.write(chunk)
        n += len(chunk)
        on_bytes(len(chunk))
    return n


def _fetch_single(url: str, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event) -> None:
    part = dest.with_name(dest.name + ".part")
    have = part.stat().st_size if part.exists() else 0
    if have:
        on_bytes(have)
    headers = dict(UA)
    if have:
        headers["Range"] = f"bytes={have}-"
    with urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
        if have and r.status != 206:  # server ignored Range: start over
            on_bytes(-have)
            have = 0
        with open(part, "ab" if have else "wb") as f:
            _copy(r, f, on_bytes, cancel)
    part.rename(dest)


def segment_ranges(total: int, n: int = SEGMENTS) -> list[tuple[int, int]]:
    """Inclusive byte ranges splitting `total` into `n` nearly equal parts."""
    step = -(-total // n)
    return [(i, min(i + step, total) - 1) for i in range(0, total, step)]


def _fetch_segmented(url: str, dest: Path, total: int, on_bytes: Callable[[int], None],
                     cancel: threading.Event) -> None:
    """SEGMENTS connections, each resumable on its own (.partN files), joined at the end."""
    ranges = segment_ranges(total)
    parts = [dest.with_name(f"{dest.name}.part{i}") for i in range(len(ranges))]
    stop = threading.Event()

    def watch() -> bool:
        return cancel.is_set() or stop.is_set()

    class _Either:  # cancel() from the user or a failed sibling segment stops every segment
        def is_set(self) -> bool:
            return watch()

    def one(i: int) -> None:
        start, end = ranges[i]
        have = parts[i].stat().st_size if parts[i].exists() else 0
        if have:
            on_bytes(have)
        if start + have > end:
            return
        req = urllib.request.Request(url, headers={**UA, "Range": f"bytes={start + have}-{end}"})
        try:
            with urlopen(req, timeout=60) as r:
                if r.status != 206:
                    raise RuntimeError("服务器不支持分段下载")
                with open(parts[i], "ab") as f:
                    _copy(r, f, on_bytes, _Either(), limit=end - start - have + 1)
        except BaseException:
            stop.set()
            raise

    with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
        futures = [pool.submit(one, i) for i in range(len(ranges))]
        errors = [f.exception() for f in futures]
    if cancel.is_set():
        raise Cancelled()
    first = next((e for e in errors if e is not None), None)
    if first is not None:
        raise first
    sizes = [p.stat().st_size for p in parts]
    if sizes != [e - s + 1 for s, e in ranges]:
        raise RuntimeError(f"分段下载不完整：{sizes}")
    joined = dest.with_name(dest.name + ".part")
    with open(joined, "wb") as out:
        for p in parts:
            with open(p, "rb") as fi:
                while chunk := fi.read(8 << 20):
                    out.write(chunk)
    joined.rename(dest)
    for p in parts:
        p.unlink(missing_ok=True)


def _fetch(url: str, dest: Path, on_bytes: Callable[[int], None], cancel: threading.Event,
           size_hint: int = 0) -> None:
    """Download with resume; files over SEGMENT_MIN use SEGMENTS parallel connections when possible."""
    if dest.exists():
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    single_part = dest.with_name(dest.name + ".part")
    segmented = any(dest.parent.glob(dest.name + ".part[0-9]*"))
    if not single_part.exists() and (segmented or size_hint >= SEGMENT_MIN or not size_hint):
        try:
            total = _range_size(url)
        except Exception:  # noqa: BLE001 — fall back to one connection
            total = 0
        if total >= SEGMENT_MIN:
            _fetch_segmented(url, dest, total, on_bytes, cancel)
            return
    _fetch_single(url, dest, on_bytes, cancel)


def download(model: Model, progress: Progress, cancel: threading.Event | None = None) -> None:
    cancel = cancel or threading.Event()
    if model.ready():
        return
    lock = threading.Lock()
    state = {"done": 0, "total": model.approx_mb << 20, "title": model.title}

    def bump(n: int) -> None:
        with lock:
            state["done"] += n
            progress(state["title"], state["done"], state["total"])

    if model.url and model.url.endswith(".tar.bz2"):
        tar = MODELS / (model.dest + ".tar.bz2")
        _fetch(model.url, tar, bump, cancel, size_hint=state["total"])
        progress(model.title + "（解压中）", state["done"], state["total"])
        with tarfile.open(tar) as t:
            t.extractall(MODELS, filter="data")
        tar.unlink()
        (model.path / ".complete").touch()
    elif model.url:
        _fetch(model.url, model.path, bump, cancel, size_hint=state["total"])
    else:
        listed = "modelscope"
        try:
            items = list_files(listed, model.modelscope)
        except Exception:  # noqa: BLE001 — ModelScope unreachable: list from HuggingFace
            listed = "huggingface"
            items = list_files(listed, model.modelscope)
        biggest = max(items, key=lambda f: f[1])[0] if items else ""
        source = pick_source(model.modelscope, biggest) if biggest else listed
        if source != listed:  # the two hubs differ in small files (ModelScope adds configuration.json)
            try:
                items = list_files(source, model.modelscope)
            except Exception:  # noqa: BLE001 — keep the first listing; missing files fall back per file
                pass
        if model.files:
            items = [f for f in items if f[0] in model.files]
        state["total"] = sum(size for _, size in items) or state["total"]
        state["title"] = f"{model.title}（来源 {SOURCE_LABEL[source]}）"
        progress(state["title"], state["done"], state["total"])
        single_file = len(model.files) == 1
        other = next(x for x in SOURCES if x != source)

        def one(item: tuple[str, int]) -> None:
            path, size = item
            dest = model.path if single_file else model.path / path
            try:
                _fetch(file_url(source, model.modelscope, path), dest, bump, cancel, size_hint=size)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                _fetch(file_url(other, model.modelscope, path), dest, bump, cancel, size_hint=size)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(one, items))
        if not single_file:
            (model.path / ".complete").touch()
    progress(model.title, state["total"], state["total"])


def adopt_existing(src_dir: Path) -> list[str]:
    """Move models from an older install (e.g. the zh2en-voice prototype) into MODELS."""
    moved = []
    for m in {id(m): m for m in [VAD, ASR, *LLM.values(), *TTS_CLONE.values(), KOKORO, POCKET]}.values():
        src = src_dir / m.dest
        if src.exists() and not m.path.exists():
            src.rename(m.path)
            if m.path.is_dir():
                (m.path / ".complete").touch()
            moved.append(m.dest)
    return moved
