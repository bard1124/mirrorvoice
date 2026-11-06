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


def data_dir() -> Path:
    """Per-user writable home: models, voice sample, transcripts, settings."""
    if os.environ.get("TONGCHUAN_HOME"):
        p = Path(os.environ["TONGCHUAN_HOME"])
    else:
        from platformdirs import user_data_dir

        p = Path(user_data_dir(APP_ID, appauthor=False))
    p.mkdir(parents=True, exist_ok=True)
    return p


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
def has_nvidia_gpu() -> bool:
    if not IS_WIN:
        return False
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=5,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return "GPU" in out.stdout
    except Exception:  # noqa: BLE001
        return False


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
    quality: str = "fast"           # "fast" | "quality" (bigger models)
    cpu_voice: str = "clone"        # Windows without NVIDIA: "clone" (Pocket TTS, English) | "standard" (Kokoro)
    gain: float = 1.0
    overlay_font: int = 26
    overlay_opacity: float = 0.86
    overlay_show_original: bool = True
    overlay_x: int = -1
    overlay_y: int = -1
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
