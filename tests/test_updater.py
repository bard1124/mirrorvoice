import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

import pytest

from mirrorvoice import __version__, updater


def bump(v: str, n: int = 1) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + n}"


def test_version_parsing():
    assert updater._ver("v0.5.10") > updater._ver("0.5.9")
    assert updater._ver("MirrorVoice-0.5.4-macos-arm64.dmg") == (0, 5, 4)
    assert updater._ver("no-version") == (0,)


def test_cleanup_keeps_newer_and_fresh_files(home):
    up = updater.UPDATES
    up.mkdir(parents=True, exist_ok=True)
    old, cur, new = f"MirrorVoice-0.0.1-macos-arm64.dmg", f"MirrorVoice-{__version__}-windows-x64-setup.exe", \
        f"MirrorVoice-{bump(__version__)}-macos-arm64.dmg"
    for n in (old, cur, new, "TongChuan-0.3.2-macos-arm64.dmg", "fresh.dmg.part", "stale.dmg.part", "update.log"):
        (up / n).write_bytes(b"x")
    past = time.time() - 3 * 86400
    os.utime(up / "stale.dmg.part", (past, past))
    removed = updater.cleanup_downloads()
    assert sorted(removed) == sorted([old, cur, "TongChuan-0.3.2-macos-arm64.dmg", "stale.dmg.part"])
    assert sorted(p.name for p in up.iterdir()) == sorted([new, "fresh.dmg.part", "update.log"])


def _release(tmp_path, tag, installer=b"installer-bytes", good_sum=True):
    name = "MirrorVoice-9-macos-arm64.dmg" if sys.platform == "darwin" else "MirrorVoice-9-windows-x64-setup.exe"
    (tmp_path / name).write_bytes(installer)
    digest = hashlib.sha256(installer if good_sum else b"other").hexdigest()
    (tmp_path / "SHA256SUMS.txt").write_text(f"{digest}  {name}\n")
    feed = {"tag_name": tag, "html_url": "https://example.invalid", "body": "notes", "assets": [
        {"name": name, "browser_download_url": (tmp_path / name).as_uri(), "size": len(installer)},
        {"name": "SHA256SUMS.txt", "browser_download_url": (tmp_path / "SHA256SUMS.txt").as_uri()}]}
    (tmp_path / "feed.json").write_text(json.dumps(feed))
    return (tmp_path / "feed.json").as_uri()


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_check_and_verified_download(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "FEED", _release(tmp_path, "v" + bump(__version__)))
    rel = updater.check()
    assert rel and rel.version == bump(__version__) and len(rel.sha256) == 64
    path = updater.download(rel, lambda d, t: None)
    assert path.read_bytes() == b"installer-bytes"
    monkeypatch.setattr(updater, "FEED", _release(tmp_path, "v" + __version__))
    assert updater.check() is None  # not newer


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_checksum_mismatch_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(updater, "FEED", _release(tmp_path, "v" + bump(__version__), good_sum=False))
    rel = updater.check()
    with pytest.raises(RuntimeError, match="SHA256"):
        updater.download(rel, lambda d, t: None)


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("hdiutil"), reason="macOS swap script")
def test_mac_swap_script_replaces_bundle_and_removes_dmg(tmp_path):
    payload, target = tmp_path / "payload" / "镜声.app" / "Contents", tmp_path / "Apps" / "镜声.app" / "Contents"
    payload.mkdir(parents=True)
    target.mkdir(parents=True)
    (payload / "marker").write_text("new")
    (target / "marker").write_text("old")
    dmg = tmp_path / "MirrorVoice-9.9.9-macos-arm64.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "t", "-srcfolder", str(tmp_path / "payload"), "-ov",
                    "-format", "UDZO", str(dmg)], check=True, capture_output=True)
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    (script_dir / "update.sh").write_text(updater.MAC_SCRIPT)
    dead = subprocess.Popen(["true"])
    dead.wait()
    subprocess.run(["/bin/bash", str(script_dir / "update.sh"), str(dead.pid), str(dmg),
                    str(target.parent), str(tmp_path / "update.log"), "norelaunch"], check=True, timeout=120)
    assert (target / "marker").read_text() == "new"
    assert not dmg.exists() and not script_dir.exists()
