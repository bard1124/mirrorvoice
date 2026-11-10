"""Paths, platform facts and persisted user settings."""
from __future__ import annotations

import functools
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import APP_ID

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
IS_APPLE_SILICON = IS_MAC and platform.machine() == "arm64"
FROZEN = getattr(sys, "frozen", False)  # running from the PyInstaller bundle


LEGACY_APP_ID = "TongChuan"  # data folder name before the rename to 镜声 / MirrorVoice


def data_dir() -> Path:
    """Per-user writable home: models, voice sample, transcripts, settings."""
    env = os.environ.get("MIRRORVOICE_HOME") or os.environ.get("TONGCHUAN_HOME")
    if env:
        p = Path(env)
    else:
        from platformdirs import user_data_dir

        p = _pick_home(Path(user_data_dir(LEGACY_APP_ID, appauthor=False)),
                       Path(user_data_dir(APP_ID, appauthor=False)))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pick_home(old: Path, new: Path) -> Path:
    """Keep using the pre-rename data folder when it exists (models, voice sample, settings and the
    GPU voice pack stay where they are — nothing to download or record again). Renaming a 10 GB
    tree in place is refused whenever an indexer or antivirus is inside it, so it is not attempted;
    fresh installs get the new name."""
    if new.exists() or not old.is_dir():
        return new
    return old


def resource_dir() -> Path:
    """Read-only files shipped with the app (bundled helpers, driver installers)."""
    if FROZEN:
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent / "packaging" / "vendor"


HOME = data_dir()
MODELS = HOME / "models"
VOICE = HOME / "voice"
TRANSCRIPTS = HOME / "transcripts"
SETTINGS_FILE = HOME / "settings.json"
PID_FILE = HOME / "cli.pid"
GLOSSARY_FILE = HOME / "glossary.csv"
LOG_FILE = HOME / "debug.log"
GPU_PACK = HOME / "gpu-voice"            # Windows: venv with CUDA torch + qwen-tts
GPU_PACK_DONE = GPU_PACK / ".complete"   # written only after every install step succeeded
TTS_SERVER_LOG = HOME / "tts_server.log"  # stderr of tts_server.py (the GUI has no console)
for _p in (MODELS, VOICE, TRANSCRIPTS):
    _p.mkdir(parents=True, exist_ok=True)

# models are all local at runtime; never let HF libraries reach for the network
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")


@functools.lru_cache(maxsize=1)
def nvidia_gpu() -> tuple[str, float] | None:
    """(name, compute capability) of the first NVIDIA GPU, via nvidia-smi; None without one.
    Capability is 0.0 when the driver is too old to report it (then too old for CUDA torch too)."""
    if not IS_WIN:
        return None
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--query-gpu=name,compute_cap", "--format=csv,noheader"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        line = out.stdout.strip().splitlines()[0]
        name, _, cap = line.rpartition(",")
        return name.strip(), float(cap)
    except Exception:  # noqa: BLE001 — no GPU, or a driver without the compute_cap field
        try:
            out = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=5,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            line = out.stdout.strip().splitlines()[0] if "GPU" in out.stdout else ""
            if not line:
                return None
            name = line.partition(": ")[2].partition(" (UUID")[0] or line  # "GPU 0: NAME (UUID: ...)"
            return name, 0.0
        except Exception:  # noqa: BLE001
            return None


def has_nvidia_gpu() -> bool:
    return nvidia_gpu() is not None


# Qwen3-TTS runs in bf16 (fp16 overflows to NaN). bf16 is native from Ampere (RTX 30, 8.0) on;
# Turing (RTX 20 / GTX 16, 7.5) emulates it ~6x slower: a 3 s sentence took 10 s on an RTX 2060.
CUDA_VOICE_MIN_CAP = 8.0


def cuda_voice_ok(gpu_voice: str = "auto") -> bool:
    """Use the CUDA voice pack? "auto" needs a capable GPU; "cuda"/"cpu" force it on/off."""
    gpu = nvidia_gpu()
    if gpu is None or gpu_voice == "cpu":
        return False
    return gpu_voice == "cuda" or gpu[1] >= CUDA_VOICE_MIN_CAP


def cpu_voice_reason() -> str:
    """Why the CPU voice path is in use (for wizard / status text)."""
    gpu = nvidia_gpu()
    if gpu is None:
        return "未检测到 NVIDIA 显卡"
    if gpu[1] == 0.0:
        return f"{gpu[0]} 的驱动过旧，无法判断显卡能力（请更新 NVIDIA 驱动）"
    if gpu[1] < CUDA_VOICE_MIN_CAP:
        return f"{gpu[0]}（算力 {gpu[1]:.1f}）不原生支持 bf16，克隆音色无法实时"
    return "已设置为不使用 GPU 音色"


_JOB = None  # Windows Job Object handle; kept alive for the life of the process


def kill_with_parent(proc) -> None:
    """Windows: put a child (llama-server, tts_server) in a Job Object that kills it when this
    process ends for any reason. Without it a crashed / force-closed GUI left llama-server running,
    holding ~3 GB of VRAM that the next session then lacked."""
    if not IS_WIN:
        return
    import ctypes
    from ctypes import wintypes

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                      "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    global _JOB
    k32 = ctypes.windll.kernel32
    try:
        if _JOB is None:
            job = k32.CreateJobObjectW(None, None)
            info = EXTENDED_LIMIT()
            info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info)):  # 9 = ExtendedLimit
                return
            _JOB = job
        k32.AssignProcessToJobObject(_JOB, wintypes.HANDLE(int(proc._handle)))
    except Exception:  # noqa: BLE001 — best effort; the app still works without the job
        pass


def gpu_pack_python() -> Path:
    return GPU_PACK / ("Scripts/python.exe" if IS_WIN else "bin/python")


def gpu_pack_installed() -> bool:
    """The venv exists after install step 1 of 4; only the marker proves torch + qwen-tts landed."""
    return gpu_pack_python().exists() and GPU_PACK_DONE.exists()


DEFAULT_CALL_OUT = "BlackHole 2ch" if IS_MAC else "CABLE Input"
DEFAULT_THEIR_SOURCE = "BlackHole 16ch" if IS_MAC else "loopback:default"


@dataclass
class Settings:
    my_lang: str = "zh"             # language I speak
    their_lang: str = "en"          # language the other side speaks
    mic: str = ""                   # "" = system default
    call_out: str = DEFAULT_CALL_OUT  # virtual mic the meeting app listens to
    monitor: str = ""               # also play my translation here (headphones), "" = off
    their_source: str = DEFAULT_THEIR_SOURCE  # where the other side's audio comes from
    speak: bool = True              # voice my translation into the call
    subtitles: bool = True          # translate the other side into on-screen subtitles
    passthrough: bool = True        # if I already speak their language, send my real voice
    mode: str = "continuous"        # "continuous" | "ptt"
    ptt_key: str = "alt_r"          # pynput key name held for push-to-talk
    mute_hotkey: str = "<ctrl>+<alt>+m"
    pause: float = 0.5              # silence that ends a sentence
    quality: str = "fast"           # "turbo" (smallest, weak machines) | "fast" | "quality" (bigger models)
    meeting_context: str = ""       # free-text background of the meeting, helps the translator pick terms
    llm_mode: str = "local"         # translation: "local" (downloaded Qwen3) | "online" (API key, providers.py)
    llm_provider: str = "deepseek"  # providers.PRESETS id
    llm_model: str = ""             # "" = the provider's default model
    llm_base_url: str = ""          # only for llm_provider == "custom"
    online_their: bool = False      # also send the other side's speech (subtitles) to the online model
    online_consent: bool = False    # the user confirmed what online translation sends out
    asr_mode: str = "local"         # speech recognition: "local" (SenseVoice) | "online" (阿里云百炼 streaming)
    asr_me: bool = True             # online recognition for my speech …
    asr_them: bool = True           # … and for the other side's (live subtitles)
    asr_consent: bool = False       # the user confirmed that audio goes to the service
    asr_local: str = "sensevoice"   # local recognition: "sensevoice" | "qwen3_them" | "qwen3" (macOS, Qwen3-ASR 1.7B)
    cpu_voice: str = "clone"        # Windows without CUDA voice: "clone" (Pocket TTS, English) | "standard" (Kokoro)
    gpu_voice: str = "auto"         # NVIDIA clone voice: "auto" (compute capability >= 8.0 only) | "cuda" | "cpu"
    gain: float = 1.0
    voice_speed: float = 1.0        # CPU voices (Pocket / Kokoro): 0.7 slower … 1.3 faster
    overlay_font: int = 26
    overlay_opacity: float = 0.86
    overlay_show_original: bool = True
    overlay_x: int = -1
    overlay_y: int = -1
    overlay_pinned: bool = False    # keep the other side's subtitle bar on screen even when stopped
    convo_open: bool = False        # floating two-way conversation window shown
    convo_font: int = 15
    convo_x: int = -1
    convo_y: int = -1
    convo_w: int = 840
    convo_h: int = 300
    display: str = "ui"            # "ui" control panel | "cli" terminal style
    auto_check_update: bool = True  # look for a newer release on startup (silent when offline)
    skip_version: str = ""         # "跳过此版本"
    setup_done: bool = False
    extra: dict = field(default_factory=dict)

    @classmethod
    def load(cls) -> "Settings":
        if SETTINGS_FILE.exists():
            try:
                raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                known = {f.name for f in fields(cls)}
                return cls(**{k: v for k, v in raw.items() if k in known})
            except Exception:  # noqa: BLE001 — corrupt settings fall back to defaults
                pass
        return cls()

    def save(self) -> None:
        SETTINGS_FILE.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                                 encoding="utf-8")
