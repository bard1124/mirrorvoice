"""One-click update from GitHub Releases.

check()   -> Release | None     (newer version for this platform, or None)
download() -> Path               (installer, SHA256-verified against the release's SHA256SUMS.txt)
install()                        (hands over to a detached installer, then the app must quit)

macOS:   a helper shell script waits for this process to exit, replaces the .app bundle from the
         DMG, clears quarantine and relaunches.
Windows: runs the Inno Setup installer silently; the installer relaunches the app.
Models, voice sample, transcripts and settings live in the data folder and are untouched.
Release files come from the normal download link (github.com/…/releases/download/…); when that
fails (some networks get 504 there while api.github.com works) the API asset endpoint is used.
Downloaded installers are removed after a successful macOS swap, and on every start any installer
for this version or older (Windows cannot delete its running setup.exe) is cleaned up.

Code patches: a release also carries update.json (signed: update.json.sig, Ed25519) naming the
runtime each platform's installer was built on and a small patch zip with just the app layer
(mirrorvoice package + images, a few MB instead of a 230 MB installer). When this bundle has the
same runtime, check() offers the patch; install() unpacks it into the data folder
(mirrorvoice.patches) and relaunches — the bundle itself is not touched. Anything else (another
runtime, no or a bad signature, a patch that failed here before) gets the full installer.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__, ed25519, patches
from .config import FROZEN, HOME, IS_MAC, IS_WIN
from .net import urlopen

REPO = "bard1124/mirrorvoice"
FEED = os.environ.get("MIRRORVOICE_UPDATE_FEED", f"https://api.github.com/repos/{REPO}/releases/latest")
UPDATES = HOME / "updates"
UA = {"User-Agent": f"MirrorVoice/{__version__} (updater)", "Accept": "application/vnd.github+json"}
MANIFEST = "update.json"
# Ed25519 key that signs update.json in the release workflow (secret PATCH_SIGNING_KEY)
PUBLIC_KEY = bytes.fromhex("118f8a36e11b4dfe2aea177f85bc575b7d7b1632b8fe4016fbdd3dde50dfefa4")


@dataclass
class Release:
    version: str
    notes: str
    page: str
    asset_name: str
    asset_url: str
    size: int
    sha256: str  # "" if the release has no SHA256SUMS.txt
    asset_api_url: str = ""  # API asset endpoint: fallback when the download link fails
    kind: str = "full"  # "full" installer | "patch" (app layer only)
    runtime: str = ""  # patch: the runtime it was made for
    full_size: int = 0  # patch: the installer's size, to show what was saved


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def _asset_pattern() -> re.Pattern:
    if IS_MAC:
        return re.compile(r"macos-arm64\.dmg$")
    if IS_WIN:
        return re.compile(r"windows-x64-setup\.exe$")
    return re.compile(r"$^")


def _get(url: str, timeout: float = 15) -> bytes:
    if url.startswith("file://"):
        return Path(url[7:]).read_bytes()
    with urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def _sources(url: str, api_url: str = "") -> list[tuple[str, dict]]:
    """Where to fetch a release file: the download link first, then the API asset endpoint."""
    out = [(url, {"User-Agent": UA["User-Agent"]})]
    if api_url:
        out.append((api_url, {"User-Agent": UA["User-Agent"], "Accept": "application/octet-stream"}))
    return out


def _open_first(sources: list[tuple[str, dict]], timeout: float):
    """Response of the first source that answers; the first source's error if none does."""
    first: Exception | None = None
    for url, headers in sources:
        try:
            return urlopen(urllib.request.Request(url, headers=headers), timeout=timeout)
        except Exception as e:  # noqa: BLE001 — HTTP 5xx/4xx, timeout, refused: try the next source
            first = first or e
    assert first is not None
    raise first


def _get_asset(asset: dict, timeout: float = 15) -> bytes:
    url = asset["browser_download_url"]
    if url.startswith("file://"):
        return _get(url)
    with _open_first(_sources(url, asset.get("url", "")), timeout) as r:
        return r.read()


def platform_tag() -> str:
    return "macos-arm64" if IS_MAC else "windows-x64" if IS_WIN else ""


def manifest(assets: list[dict]) -> dict | None:
    """The release's update.json when its signature checks out, else None."""
    man = next((a for a in assets if a["name"] == MANIFEST), None)
    sig = next((a for a in assets if a["name"] == MANIFEST + ".sig"), None)
    if not man or not sig:
        return None
    body = _get_asset(man)
    try:
        signature = bytes.fromhex(_get_asset(sig).decode().strip())
    except ValueError:
        return None
    if not ed25519.verify(PUBLIC_KEY, body, signature):
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def _patch_offer(data: dict, version: str, full: dict) -> Release | None:
    """The release's code patch when it fits this bundle's runtime."""
    runtime = patches.bundle_info().get("runtime", "")
    if not runtime or version in patches.bad_versions():
        return None
    assets = data.get("assets", [])
    m = manifest(assets)
    if not m or m.get("version") != version:
        return None
    plat = (m.get("platforms") or {}).get(platform_tag()) or {}
    patch = m.get("patch") or {}
    if plat.get("runtime") != runtime or not patch.get("name") or len(patch.get("sha256", "")) != 64:
        return None
    a = next((x for x in assets if x["name"] == patch["name"]), None)
    if not a:
        return None
    return Release(version, data.get("body", "") or "", data.get("html_url", ""), a["name"],
                   a["browser_download_url"], int(patch.get("size") or a.get("size", 0)), patch["sha256"].lower(),
                   a.get("url", ""), kind="patch", runtime=runtime, full_size=int(full.get("size", 0)))


def check() -> Release | None:
    """Newest release if it is newer than this build: its code patch when that fits this bundle,
    else its installer for this platform."""
    data = json.loads(_get(FEED))
    tag = data.get("tag_name", "")
    if _ver(tag) <= _ver(__version__):
        return None
    pat = _asset_pattern()
    assets = data.get("assets", [])
    inst = next((a for a in assets if pat.search(a["name"])), None)
    if not inst:
        return None
    try:
        rel = _patch_offer(data, tag.lstrip("v"), inst)
    except Exception:  # noqa: BLE001 — any trouble with the patch: offer the installer
        rel = None
    if rel:
        return rel
    sha = ""
    sums = next((a for a in assets if a["name"] == "SHA256SUMS.txt"), None)
    if sums:
        for line in _get_asset(sums).decode().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1].lstrip("*") == inst["name"]:
                sha = parts[0].lower()
    return Release(tag.lstrip("v"), data.get("body", "") or "", data.get("html_url", ""),
                   inst["name"], inst["browser_download_url"], int(inst.get("size", 0)), sha,
                   inst.get("url", ""))


def check_async(cb: Callable[[Release | None, str], None]) -> None:
    """cb(release, error) on a worker thread; offline / rate-limited -> (None, message)."""
    def work():
        try:
            cb(check(), "")
        except Exception as e:  # noqa: BLE001 — offline is normal for this app
            cb(None, str(e))
    threading.Thread(target=work, daemon=True, name="update-check").start()


class UpdateBusy(RuntimeError):
    """Another MirrorVoice process is downloading the update right now."""


@contextlib.contextmanager
def _download_lock():
    """One download at a time across processes. Two (the app opened again during a slow command-line
    update) wrote the same .part file; the loser failed with "No such file … .part -> .dmg"."""
    UPDATES.mkdir(parents=True, exist_ok=True)
    f = open(UPDATES / "download.lock", "a+")
    try:
        if IS_WIN:
            import msvcrt

            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        raise UpdateBusy("另一个镜声窗口正在下载这个更新，下载完会自动安装并重新打开镜声，请稍候。") from None
    try:
        yield
    finally:
        try:
            if IS_WIN:
                import msvcrt

                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
        f.close()  # closing releases flock on macOS


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(rel: Release, progress: Callable[[int, int], None], cancel: threading.Event | None = None) -> Path:
    with _download_lock():
        dest = UPDATES / rel.asset_name
        if dest.exists() and rel.sha256 and _sha256(dest) == rel.sha256:
            progress(dest.stat().st_size, dest.stat().st_size)  # finished by an earlier run
            return dest
        return _download(rel, dest, progress, cancel)


def _download(rel: Release, dest: Path, progress: Callable[[int, int], None], cancel: threading.Event | None) -> Path:
    part = dest.with_name(dest.name + ".part")
    h = hashlib.sha256()
    if rel.asset_url.startswith("file://"):
        src = Path(rel.asset_url[7:])
        total = src.stat().st_size
        with open(src, "rb") as fi, open(part, "wb") as fo:
            done = 0
            while chunk := fi.read(1 << 20):
                fo.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(done, total)
    else:
        with _open_first(_sources(rel.asset_url, rel.asset_api_url), 60) as r, open(part, "wb") as fo:
            total = int(r.headers.get("Content-Length") or rel.size or 0)
            done = 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise RuntimeError("已取消")
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                fo.write(chunk)
                h.update(chunk)
                done += len(chunk)
                progress(done, total)
    if rel.sha256 and h.hexdigest() != rel.sha256:
        part.unlink(missing_ok=True)
        raise RuntimeError("下载的安装包校验失败（SHA256 不一致），已删除，请重试")
    part.replace(dest)
    return dest


INSTALLER_SUFFIXES = (".dmg", ".exe", ".zip")


def cleanup_downloads(stale_part_hours: float = 24) -> list[str]:
    """Delete installers for this version or older, and abandoned partial downloads."""
    removed: list[str] = []
    if not UPDATES.is_dir():
        return removed
    now = time.time()
    for f in UPDATES.iterdir():
        try:
            if f.suffix.lower() in INSTALLER_SUFFIXES and _ver(f.name) <= _ver(__version__):
                f.unlink()
            elif f.name.endswith(".part") and now - f.stat().st_mtime > stale_part_hours * 3600:
                f.unlink()
            else:
                continue
            removed.append(f.name)
        except OSError:  # still open (e.g. the Windows installer is finishing): next start
            pass
    return removed


# after a patch: wait for this process, stop any other window of the app still running the old code
# (asking it to quit is refused while it lives in the menu bar; `open` would only bring it forward),
# then start the app again
RELAUNCH_MAC = r"""
while kill -0 "$1" 2>/dev/null; do sleep 0.3; done
BIN="$2/Contents/MacOS/"
if pgrep -f "$BIN" >/dev/null; then
  pkill -TERM -f "$BIN"
  for i in $(seq 1 20); do pgrep -f "$BIN" >/dev/null || break; sleep 0.5; done
  pkill -KILL -f "$BIN"
  sleep 0.5
fi
open "$2"
"""


def _relaunch_after_exit() -> None:
    """Start the app again once this process has quit (a patch takes effect on the next start)."""
    pid = os.getpid()
    env = {k: v for k, v in os.environ.items() if k != patches.ENV}  # the new start decides for itself
    if IS_MAC:
        target = current_app_bundle()
        if target is None:
            return
        subprocess.Popen(["/bin/sh", "-c", RELAUNCH_MAC, "relaunch", str(pid), str(target)], env=env,
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    elif IS_WIN:
        gui = str(Path(sys.executable).with_name("MirrorVoice.exe")).replace("'", "''")  # not the CLI twin
        flags = (getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                 | getattr(subprocess, "CREATE_NO_WINDOW", 0))
        subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command",
                          f"Wait-Process -Id {pid} -ErrorAction SilentlyContinue; "
                          f"Get-Process MirrorVoice -ErrorAction SilentlyContinue | Where-Object {{ $_.Path -eq '{gui}' }}"
                          f" | Stop-Process -Force; Start-Sleep -Milliseconds 500; Start-Process -FilePath '{gui}'"],
                         creationflags=flags, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)


def can_self_install() -> bool:
    return FROZEN and (IS_MAC or IS_WIN)


def current_app_bundle() -> Path | None:
    """…/镜声.app when running from the macOS bundle."""
    p = Path(sys.executable).resolve()
    for parent in p.parents:
        if parent.suffix == ".app":
            return parent
    return None


MAC_SCRIPT = r"""#!/bin/bash
# MirrorVoice updater: wait for the app to quit, swap the bundle from the DMG, relaunch.
PID="$1"; DMG="$2"; TARGET="$3"; LOG="$4"
exec >>"$LOG" 2>&1
echo "$(date) update start pid=$PID target=$TARGET"
for i in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
# Any other window of the app still running would keep the old code in memory after the swap
# (and `open` would only bring it to the front): ask it to quit, then stop it.
BIN="$TARGET/Contents/MacOS/"
if pgrep -f "$BIN" >/dev/null; then
  echo "$(date) other instances running: asking them to quit"
  [ -z "$MIRRORVOICE_TEST_NO_OSASCRIPT" ] && osascript -e 'tell application id "io.github.mirrorvoice" to quit' >/dev/null 2>&1
  for i in $(seq 1 40); do pgrep -f "$BIN" >/dev/null || break; sleep 0.5; done
  if pgrep -f "$BIN" >/dev/null; then echo "still running (a dialog open?): stopping"; pkill -TERM -f "$BIN"; sleep 2; pkill -KILL -f "$BIN"; fi
fi
MNT=$(hdiutil attach -nobrowse -readonly "$DMG" | awk -F'\t' '/\/Volumes\//{print $NF}' | tail -1)
SRC=$(ls -d "$MNT"/*.app | head -1)
if [ -z "$SRC" ]; then echo "no .app in dmg"; hdiutil detach "$MNT" -quiet; exit 1; fi
STAGE="$TARGET.new"
rm -rf "$STAGE" && ditto "$SRC" "$STAGE" || { echo "copy failed"; hdiutil detach "$MNT" -quiet; exit 1; }
hdiutil detach "$MNT" -quiet
xattr -dr com.apple.quarantine "$STAGE" 2>/dev/null
rm -rf "$TARGET.old" && mv "$TARGET" "$TARGET.old" && mv "$STAGE" "$TARGET" && rm -rf "$TARGET.old" || { echo "swap failed"; exit 1; }
rm -f "$DMG"   # installed: the 200 MB installer is no longer needed
rm -rf "$(dirname "$0")"
if [ "$5" = "norelaunch" ]; then echo "$(date) replaced (no relaunch)"; exit 0; fi
echo "$(date) replaced; relaunching"
open "$TARGET"
"""


def install(installer: Path, rel: Release | None = None) -> None:
    """Start the hand-over; the caller must quit the app right after this returns."""
    if not can_self_install():
        raise RuntimeError("从源码运行时不能自动安装，请用 git pull 更新")
    log = UPDATES / "update.log"
    if installer.suffix.lower() == ".zip":  # code patch: unpack it, start again (the bundle stays)
        version = rel.version if rel else ".".join(map(str, _ver(installer.name)))
        runtime = rel.runtime if rel and rel.runtime else patches.bundle_info().get("runtime", "")
        patches.install(installer, version, runtime)
        installer.unlink(missing_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%c')} patch {version} unpacked for runtime {runtime}\n")
        if not os.environ.get("MIRRORVOICE_UPDATE_NO_RELAUNCH"):
            _relaunch_after_exit()
        return
    if IS_MAC:
        target = current_app_bundle()
        if target is None:
            raise RuntimeError("找不到当前应用位置")
        script = Path(tempfile.mkdtemp(prefix="tc-update-")) / "update.sh"
        script.write_text(MAC_SCRIPT)
        script.chmod(0o755)
        relaunch = "norelaunch" if os.environ.get("MIRRORVOICE_UPDATE_NO_RELAUNCH") else "relaunch"
        subprocess.Popen(["/bin/bash", str(script), str(os.getpid()), str(installer), str(target), str(log),
                          relaunch],
                         start_new_session=True, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
    elif IS_WIN:
        # /SILENT shows a small progress window; the installer closes us and relaunches the app
        subprocess.Popen([str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
                          "/CLOSEAPPLICATIONS", f"/LOG={log}"],
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
