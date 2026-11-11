"""Code patches on the app side: unpack a downloaded patch, make it the one to start next time,
confirm that a patched start went well, tidy up old patch folders.

The folder layout and the start-up rules are packaging/patchboot.py's (it runs before this package
is imported): <data folder>/app/<version>/{mirrorvoice/, assets/, patch.json}, app/active.json,
app/boot.json (unconfirmed starts), app/bad.json (patches that failed).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import zipfile
from pathlib import Path

from . import __version__
from .config import FROZEN, HOME, resource_dir

APP = HOME / "app"
ENV = "MIRRORVOICE_PATCH"


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)


def bundle_info() -> dict:
    """{"version", "runtime", "platform"} of the installed bundle; {} when running from source."""
    if not FROZEN:
        return {}
    return _read(resource_dir() / "runtime.json", {})


def running_patch() -> str:
    """The patch version this process runs, '' when it runs the bundled code."""
    return os.environ.get(ENV, "")


def bad_versions() -> list[str]:
    return _read(APP / "bad.json", [])


def _safe_extract(zf: zipfile.ZipFile, dest: Path) -> None:
    root = dest.resolve()
    for info in zf.infolist():
        target = (dest / info.filename).resolve()
        if root not in target.parents and target != root:
            raise ValueError(f"补丁包里有越界路径：{info.filename}")
        top = info.filename.split("/", 1)[0]
        if top not in ("mirrorvoice", "assets", "patch.json"):
            raise ValueError(f"补丁包里有意外的文件：{info.filename}")
    zf.extractall(dest)


def install(zip_path: Path, version: str, runtime: str) -> Path:
    """Unpack the patch for `version` (made for `runtime`) and start it next time."""
    if not runtime:
        raise RuntimeError("这个安装包不支持补丁更新，请下载完整安装包")
    APP.mkdir(parents=True, exist_ok=True)
    tmp = APP / f".{version}.unpacking"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _safe_extract(zf, tmp)
        init = tmp / "mirrorvoice" / "__init__.py"
        m = re.search(r'__version__\s*=\s*"([^"]+)"', init.read_text(encoding="utf-8")) if init.is_file() else None
        if not m or m.group(1) != version:
            raise ValueError(f"补丁包的版本不是 {version}")
        _write(tmp / "patch.json", {"version": version, "runtime": runtime})
        final = APP / version
        if final.exists():
            if running_patch() == version:
                raise RuntimeError(f"{version} 已经在运行")
            shutil.rmtree(final)
        os.replace(tmp, final)
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    bad = [v for v in bad_versions() if v != version]  # a fresh copy gets a fresh chance
    _write(APP / "bad.json", bad)
    _write(APP / "active.json", {"version": version})
    (APP / "boot.json").unlink(missing_ok=True)
    return final


def confirm_boot() -> None:
    """The patched app came up fine: forget the start attempts that patchboot counts."""
    v = running_patch()
    if v and _read(APP / "boot.json", {}).get("version") == v:
        (APP / "boot.json").unlink(missing_ok=True)


def cleanup() -> list[str]:
    """Remove patch folders that will never run again: older than or as old as the bundle (a full
    update came since), half-unpacked ones, and any that is neither active nor running now."""
    removed: list[str] = []
    if not APP.is_dir():
        return removed
    base = bundle_info().get("version", "")
    active = _read(APP / "active.json", {}).get("version", "")
    if active and base and _ver(active) <= _ver(base):
        (APP / "active.json").unlink(missing_ok=True)
        active = ""
    for d in APP.iterdir():
        if not d.is_dir():
            continue
        stale = d.name.startswith(".") or (base and _ver(d.name) <= _ver(base)) or \
            d.name not in (active, running_patch())
        if stale:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed
