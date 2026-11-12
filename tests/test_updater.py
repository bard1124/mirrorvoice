import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mirrorvoice import APP_NAME, __version__, updater


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


def _release(http_files, tag, installer=b"installer-bytes", good_sum=True):
    """A GitHub-like release feed served over local HTTP (file:// URLs differ per OS)."""
    base, files, _ = http_files
    name = "MirrorVoice-9-macos-arm64.dmg" if sys.platform == "darwin" else "MirrorVoice-9-windows-x64-setup.exe"
    digest = hashlib.sha256(installer if good_sum else b"other").hexdigest()
    (updater.UPDATES / name).unlink(missing_ok=True)  # a finished download from another test would be reused
    files[f"/{name}"] = installer
    files["/SHA256SUMS.txt"] = f"{digest}  {name}\n".encode()
    files["/api/assets/1"], files["/api/assets/2"] = files[f"/{name}"], files["/SHA256SUMS.txt"]
    files["/feed.json"] = json.dumps({"tag_name": tag, "html_url": "https://example.invalid", "body": "notes",
                                      "assets": [{"name": name, "browser_download_url": f"{base}/{name}",
                                                  "url": f"{base}/api/assets/1", "size": len(installer)},
                                                 {"name": "SHA256SUMS.txt", "url": f"{base}/api/assets/2",
                                                  "browser_download_url": f"{base}/SHA256SUMS.txt"}]}).encode()
    return f"{base}/feed.json"


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_check_and_verified_download(http_files, monkeypatch):
    monkeypatch.setattr(updater, "FEED", _release(http_files, "v" + bump(__version__)))
    rel = updater.check()
    assert rel and rel.version == bump(__version__) and len(rel.sha256) == 64
    path = updater.download(rel, lambda d, t: None)
    assert path.read_bytes() == b"installer-bytes"
    monkeypatch.setattr(updater, "FEED", _release(http_files, "v" + __version__))
    assert updater.check() is None  # not newer


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_checksum_mismatch_is_rejected(http_files, monkeypatch):
    monkeypatch.setattr(updater, "FEED", _release(http_files, "v" + bump(__version__), good_sum=False))
    rel = updater.check()
    with pytest.raises(RuntimeError, match="SHA256"):
        updater.download(rel, lambda d, t: None)


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_download_link_504_falls_back_to_api_endpoint(http_files, monkeypatch):
    """Seen in the wild: github.com/…/releases/download/… answered 504 while api.github.com worked."""
    _, files, srv = http_files
    monkeypatch.setattr(updater, "FEED", _release(http_files, "v" + bump(__version__)))
    srv.fail = {p for p in files if p != "/feed.json" and not p.startswith("/api/")}
    rel = updater.check()
    assert rel and len(rel.sha256) == 64  # SHA256SUMS.txt came from the API endpoint
    assert updater.download(rel, lambda d, t: None).read_bytes() == b"installer-bytes"
    assert srv.accepts["/api/assets/1"] == srv.accepts["/api/assets/2"] == "application/octet-stream"
    assert srv.fail <= set(srv.accepts)  # the normal link was tried first


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_normal_link_is_used_when_it_works(http_files, monkeypatch):
    _, _, srv = http_files
    monkeypatch.setattr(updater, "FEED", _release(http_files, "v" + bump(__version__)))
    updater.download(updater.check(), lambda d, t: None)
    assert not any(p.startswith("/api/") for p in srv.accepts)


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_all_sources_failing_reports_the_download_link_error(http_files, monkeypatch):
    _, files, srv = http_files
    monkeypatch.setattr(updater, "FEED", _release(http_files, "v" + bump(__version__)))
    srv.fail = {p for p in files if p != "/feed.json"}
    with pytest.raises(Exception, match="504"):
        updater.check()


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("hdiutil"), reason="macOS swap script")
def test_mac_swap_script_replaces_bundle_and_removes_dmg(tmp_path):
    payload, target = tmp_path / "payload" / "翻了么.app" / "Contents", tmp_path / "Apps" / "翻了么.app" / "Contents"
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


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_only_one_download_at_a_time(http_files, monkeypatch, home):
    """The app opened again during a slow command-line update downloaded into the same .part file;
    the loser failed with "No such file … .part -> .dmg"."""
    monkeypatch.setattr(updater, "FEED", _release(http_files, "v" + bump(__version__)))
    rel = updater.check()
    with updater._download_lock():
        with pytest.raises(updater.UpdateBusy, match="正在下载"):
            updater.download(rel, lambda d, t: None)
    path = updater.download(rel, lambda d, t: None)  # the lock is free again
    _, files, srv = http_files
    hits = len(srv.hits)
    assert updater.download(rel, lambda d, t: None) == path and len(srv.hits) == hits  # already there: reused


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("hdiutil"), reason="macOS swap script")
def test_mac_swap_script_stops_other_running_windows_first(tmp_path):
    payload, target = tmp_path / "payload" / "翻了么.app" / "Contents", tmp_path / "Apps" / "翻了么.app" / "Contents"
    (payload / "MacOS").mkdir(parents=True)
    (target / "MacOS").mkdir(parents=True)
    (payload / "marker").write_text("new")
    (target / "marker").write_text("old")
    shutil.copy("/bin/sleep", target / "MacOS" / "MirrorVoice")
    other = subprocess.Popen([str(target / "MacOS" / "MirrorVoice"), "60"])  # a window opened by hand
    dmg = tmp_path / "MirrorVoice-9.9.9-macos-arm64.dmg"
    subprocess.run(["hdiutil", "create", "-volname", "t", "-srcfolder", str(tmp_path / "payload"), "-ov",
                    "-format", "UDZO", str(dmg)], check=True, capture_output=True)
    script_dir = tmp_path / "script"
    script_dir.mkdir()
    (script_dir / "update.sh").write_text(updater.MAC_SCRIPT)
    dead = subprocess.Popen(["true"])
    dead.wait()
    subprocess.run(["/bin/bash", str(script_dir / "update.sh"), str(dead.pid), str(dmg), str(target.parent),
                    str(tmp_path / "update.log"), "norelaunch"], check=True, timeout=120,
                   env={**os.environ, "MIRRORVOICE_TEST_NO_OSASCRIPT": "1"})
    assert other.wait(timeout=10) != 0  # stopped before the swap
    assert (target / "marker").read_text() == "new"
    assert "other instances running" in (tmp_path / "update.log").read_text()


def test_a_bundle_with_the_old_name_is_renamed_once(tmp_path):
    """After the app got a new name, a full update left its new contents in the folder of the old name."""
    apps = tmp_path / "Apps"
    old = apps / updater.OLD_BUNDLE_NAMES[0]
    (old / "Contents").mkdir(parents=True)
    marker = tmp_path / "bundle-renamed"
    new = updater.renamed_bundle(old, marker)
    assert new == apps / f"{APP_NAME}.app"
    assert updater.renamed_bundle(apps / "My Interpreter.app", marker) is None  # the user's own name stays
    assert updater.renamed_bundle(None, marker) is None  # run from source
    marker.write_text("tried")
    assert updater.renamed_bundle(old, marker) is None
    marker.unlink()
    new.mkdir()
    assert updater.renamed_bundle(old, marker) is None  # never on top of another copy
    new.rmdir()
    if sys.platform != "win32":
        apps.chmod(0o555)
        try:
            assert updater.renamed_bundle(old, marker) is None  # read-only: opened from the disk image
        finally:
            apps.chmod(0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="shell script")
def test_rename_script_moves_the_bundle_after_the_app_quit(tmp_path):
    old, new = tmp_path / updater.OLD_BUNDLE_NAMES[0], tmp_path / f"{APP_NAME}.app"
    (old / "Contents").mkdir(parents=True)
    (old / "Contents" / "marker").write_text("app")
    dead = subprocess.Popen(["true"])
    dead.wait()
    subprocess.run(["/bin/sh", "-c", updater.RENAME_MAC, "rename", str(dead.pid), str(old), str(new), "norelaunch"],
                   check=True, timeout=60)
    assert not old.exists() and (new / "Contents" / "marker").read_text() == "app"
    # the new name is taken: nothing is moved or overwritten
    (old / "Contents").mkdir(parents=True)
    subprocess.run(["/bin/sh", "-c", updater.RENAME_MAC, "rename", str(dead.pid), str(old), str(new), "norelaunch"],
                   check=True, timeout=60)
    assert old.exists() and (new / "Contents" / "marker").read_text() == "app"


def test_the_old_name_is_gone_from_what_users_see():
    """Only the glossary samples in the tests and the notes on the rename may still say it."""
    root = Path(updater.__file__).resolve().parent.parent
    allowed = {"mirrorvoice/updater.py", "packaging/windows/installer.iss", "tests/test_translate.py",
               "tests/test_updater.py"}
    old = updater.OLD_BUNDLE_NAMES[0].removesuffix(".app")
    found = set()
    for folder, pattern in (("mirrorvoice", "*.py"), ("packaging", "*"), ("docs", "*.md"), ("", "*.md")):
        walk = (root / folder).rglob(pattern) if folder else root.glob(pattern)
        for f in walk:
            if f.is_file() and f.suffix in (".py", ".md", ".txt", ".iss", ".spec", ".sh", ".yml") \
                    and old in f.read_text(encoding="utf-8", errors="ignore"):
                found.add(f.relative_to(root).as_posix())
    assert found <= allowed
