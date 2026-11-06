"""Virtual audio cable detection / installation and the Windows GPU voice pack.

macOS:  BlackHole 2ch  = virtual mic the meeting app listens to (my translated voice)
        BlackHole 16ch = receives the meeting's speaker audio for subtitles, via a
                         Multi-Output device "Teams 扬声器 (耳机+字幕)" = headphones + BlackHole 16ch
Windows: VB-CABLE      = virtual mic; the other side is captured with WASAPI loopback (no cable)
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Callable

import sounddevice as sd

from .config import GPU_PACK, GPU_PACK_DONE, IS_MAC, resource_dir
from .net import urlopen

MULTI_OUTPUT_NAME = "Teams 扬声器 (耳机+字幕)"
VBCABLE_URL = "https://download.vb-audio.com/Download_CABLE/VBCABLE_Driver_Pack45.zip"


def _names() -> list[str]:
    sd._terminate()  # re-scan devices (new drivers / aggregate devices)
    sd._initialize()
    return [d["name"] for d in sd.query_devices()]


def status() -> dict[str, bool]:
    names = _names()
    has = lambda s: any(s.lower() in n.lower() for n in names)  # noqa: E731
    if IS_MAC:
        return {"BlackHole 2ch": has("BlackHole 2ch"), "BlackHole 16ch": has("BlackHole 16ch"),
                MULTI_OUTPUT_NAME: has(MULTI_OUTPUT_NAME)}
    return {"VB-CABLE": has("CABLE Input")}


# ------------------------------------------------------------------ macOS
def mac_install_blackhole(channels: int) -> None:
    """Open the bundled BlackHole installer package (the user confirms + enters password)."""
    pkgs = sorted(resource_dir().glob(f"BlackHole{channels}ch*.pkg"))
    if not pkgs:
        subprocess.run(["open", "https://existential.audio/blackhole/"])
        return
    subprocess.run(["open", "-W", str(pkgs[-1])])


def mac_restart_coreaudio() -> bool:
    """New audio drivers load after coreaudiod restarts; macOS asks for the admin password."""
    r = subprocess.run(["osascript", "-e",
                        'do shell script "killall coreaudiod" with administrator privileges'],
                       capture_output=True, text=True)
    return r.returncode == 0


def mac_create_multi_output(listen_device: str) -> str:
    helper = resource_dir() / "multi_output"
    if not helper.exists():
        helper = Path(__file__).resolve().parent.parent / "multi_output"
    r = subprocess.run([str(helper), listen_device], capture_output=True, text=True)
    return (r.stdout or r.stderr).strip()


# ------------------------------------------------------------------ Windows
def win_install_vbcable(log: Callable[[str], None]) -> None:
    """Download VB-CABLE from vb-audio.com and run its installer elevated (UAC prompt)."""
    tmp = Path(tempfile.mkdtemp(prefix="vbcable_"))
    z = tmp / "vbcable.zip"
    log("下载 VB-CABLE …")
    with urlopen(VBCABLE_URL, timeout=120) as r, open(z, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    with zipfile.ZipFile(z) as f:
        f.extractall(tmp)
    exe = tmp / ("VBCABLE_Setup_x64.exe" if sys.maxsize > 2**32 else "VBCABLE_Setup.exe")
    log("请在弹出的窗口中点“Install Driver”，完成后重启电脑")
    import ctypes

    ctypes.windll.shell32.ShellExecuteW(None, "runas", str(exe), None, str(tmp), 1)


TORCH_INDEX = "https://download.pytorch.org/whl/cu124"


def win_install_gpu_pack(log: Callable[[str], None]) -> bool:
    """Create the gpu-voice venv with CUDA torch + qwen-tts using the bundled uv.exe (~3 GB)."""
    uv = resource_dir() / "uv.exe"
    if not uv.exists():
        log("缺少 uv.exe（安装包不完整）")
        return False
    GPU_PACK_DONE.unlink(missing_ok=True)  # a half-finished venv must not count as installed
    py = GPU_PACK / "Scripts" / "python.exe"
    steps = [
        [str(uv), "venv", str(GPU_PACK), "--python", "3.12", "--seed"],
        [str(uv), "pip", "install", "--python", str(py), "torch", "torchaudio", "--index-url", TORCH_INDEX],
        [str(uv), "pip", "install", "--python", str(py), "--no-deps", "qwen-tts"],
        [str(uv), "pip", "install", "--python", str(py), "transformers==4.57.3", "accelerate==1.12.0",
         "librosa", "soundfile", "einops", "onnxruntime", "sox", "numpy"],
    ]
    for cmd in steps:
        log("$ " + " ".join(Path(c).name if i == 0 else c for i, c in enumerate(cmd)))
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env={**os.environ, "UV_NATIVE_TLS": "1"},  # trust company root certs in the Windows store
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for line in p.stdout:
            log(line.rstrip())
        if p.wait() != 0:
            log("失败，请检查网络后重试")
            return False
    GPU_PACK_DONE.touch()
    log("GPU 音色包安装完成")
    return True
