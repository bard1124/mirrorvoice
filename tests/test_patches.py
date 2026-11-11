"""Code patches: signing, choosing a patch at start-up, falling back, installing, offering."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from mirrorvoice import __version__, ed25519, patches, updater

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packaging"))
import make_patch  # noqa: E402
import patchboot  # noqa: E402
import runtime_files  # noqa: E402

SEED = bytes(range(32))
PUB = ed25519.public_key(SEED)


def bump(v: str, n: int = 1) -> str:
    a, b, c = (int(x) for x in v.split("."))
    return f"{a}.{b}.{c + n}"


def test_ed25519_matches_rfc8032():
    sk = bytes.fromhex("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60")
    pk = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig = bytes.fromhex("e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc"
                        "61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b")
    assert ed25519.public_key(sk) == pk and ed25519.sign(sk, b"") == sig
    assert ed25519.verify(pk, b"", sig) and not ed25519.verify(pk, b"x", sig)
    assert not ed25519.verify(pk, b"", sig[:-1] + bytes([sig[-1] ^ 1]))


def test_boot_finds_the_same_data_folder_as_the_app(monkeypatch, tmp_path):
    import platformdirs

    from mirrorvoice import config

    dirs = {"TongChuan": tmp_path / "TongChuan", "MirrorVoice": tmp_path / "MirrorVoice"}
    monkeypatch.setattr(platformdirs, "user_data_dir", lambda name, appauthor=False: str(dirs[name]))
    for env in ("MIRRORVOICE_HOME", "TONGCHUAN_HOME"):
        monkeypatch.delenv(env, raising=False)
    dirs["TongChuan"].mkdir()  # an install from before the rename
    assert patchboot.home() == config.data_dir() == dirs["TongChuan"]
    shutil.rmtree(dirs["TongChuan"])
    assert patchboot.home() == config.data_dir() == dirs["MirrorVoice"]  # (data_dir creates it)
    monkeypatch.setenv("MIRRORVOICE_HOME", str(tmp_path / "custom"))
    assert patchboot.home() == config.data_dir() == tmp_path / "custom"


def _patch_dir(app: Path, version: str, runtime: str = "rt-a", broken: bool = False) -> Path:
    d = app / version
    shutil.copytree(ROOT / "mirrorvoice", d / "mirrorvoice", ignore=shutil.ignore_patterns("__pycache__"))
    init = d / "mirrorvoice" / "__init__.py"
    init.write_text(init.read_text(encoding="utf-8").replace(f'"{__version__}"', f'"{version}"'), encoding="utf-8")
    if broken:
        (d / "mirrorvoice" / "__main__.py").write_text("this is not python(", encoding="utf-8")
    (d / "patch.json").write_text(json.dumps({"version": version, "runtime": runtime}))
    (app / "active.json").write_text(json.dumps({"version": version}))
    return d


def test_choose_only_a_fitting_newer_unfailed_patch(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    base = {"version": "1.0.0", "runtime": "rt-a"}
    d = _patch_dir(app, "1.0.1")
    assert patchboot.choose(app, base) == d
    assert patchboot.choose(app, {"version": "1.0.0", "runtime": "rt-b"}) is None  # another runtime
    assert patchboot.choose(app, {"version": "1.0.1", "runtime": "rt-a"}) is None  # the bundle is as new
    assert patchboot.choose(app, {}) is None  # a bundle from before patches
    (app / "boot.json").write_text(json.dumps({"version": "1.0.1", "tries": patchboot.MAX_TRIES}))
    assert patchboot.choose(app, base) is None  # never confirmed twice: bad
    assert json.loads((app / "bad.json").read_text()) == ["1.0.1"]
    (app / "boot.json").unlink()
    assert patchboot.choose(app, base) is None  # stays bad


def _boot(tmp_path, home: Path) -> str:
    bundle = tmp_path / "bundle"
    bundle.mkdir(exist_ok=True)
    (bundle / "runtime.json").write_text(json.dumps({"version": __version__, "runtime": "rt-a"}))
    env = {**os.environ, "MIRRORVOICE_HOME": str(home), "MIRRORVOICE_BUNDLE_DIR": str(bundle)}
    code = ("import sys; sys.path.insert(0, 'packaging'); import patchboot, os; "
            "patchboot.boot(lambda: print(__import__('mirrorvoice').__version__, os.environ.get('MIRRORVOICE_PATCH', '-'),"
            " __import__('mirrorvoice').__file__))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_boot_runs_the_patch_and_falls_back_when_it_is_broken(tmp_path):
    home = tmp_path / "home"
    app = home / "app"
    app.mkdir(parents=True)
    new = bump(__version__)
    d = _patch_dir(app, new)
    ver, env, path = _boot(tmp_path, home).split(" ", 2)
    assert (ver, env) == (new, new) and Path(path).resolve().is_relative_to(d.resolve())
    assert json.loads((app / "boot.json").read_text()) == {"version": new, "tries": 1}  # until confirmed
    newer = bump(__version__, 2)
    _patch_dir(app, newer, broken=True)
    ver, env, path = _boot(tmp_path, home).split(" ", 2)
    assert (ver, env) == (__version__, "-")  # the bundled code ran
    assert newer in json.loads((app / "bad.json").read_text())


def _zip(path: Path, version: str, extra: dict | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("mirrorvoice/__init__.py", f'__version__ = "{version}"\n')
        zf.writestr("assets/icon.png", b"png")
        for k, v in (extra or {}).items():
            zf.writestr(k, v)
    return path


def test_install_unpacks_activates_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(patches, "APP", tmp_path / "app")
    new = bump(__version__)
    d = patches.install(_zip(tmp_path / "p.zip", new), new, "rt-a")
    assert (d / "mirrorvoice" / "__init__.py").exists() and (d / "assets" / "icon.png").read_bytes() == b"png"
    assert json.loads((d / "patch.json").read_text()) == {"version": new, "runtime": "rt-a"}
    assert json.loads((tmp_path / "app" / "active.json").read_text()) == {"version": new}
    with pytest.raises(ValueError, match="版本"):
        patches.install(_zip(tmp_path / "q.zip", "0.0.1"), bump(__version__, 2), "rt-a")
    with pytest.raises(ValueError, match="越界"):
        patches.install(_zip(tmp_path / "r.zip", new, {"../evil.py": "x"}), new, "rt-a")
    assert not list((tmp_path / "app").glob(".*"))  # nothing half-unpacked left
    (tmp_path / "app" / "0.0.1").mkdir()
    monkeypatch.setattr(patches, "bundle_info", lambda: {"version": __version__, "runtime": "rt-a"})
    assert patches.cleanup() == ["0.0.1"] and d.exists()
    monkeypatch.setattr(patches, "bundle_info", lambda: {"version": new, "runtime": "rt-a"})  # a full update came
    assert new in patches.cleanup() and not (tmp_path / "app" / "active.json").exists()


def test_confirm_boot_clears_the_start_count(tmp_path, monkeypatch):
    monkeypatch.setattr(patches, "APP", tmp_path)
    (tmp_path / "boot.json").write_text(json.dumps({"version": "9.9.9", "tries": 1}))
    monkeypatch.setenv("MIRRORVOICE_PATCH", "9.9.9")
    patches.confirm_boot()
    assert not (tmp_path / "boot.json").exists()


def test_patch_zip_has_the_app_layer_only(tmp_path):
    z = tmp_path / "p.zip"
    make_patch.build_zip(z, __version__)
    names = zipfile.ZipFile(z).namelist()
    assert "mirrorvoice/__init__.py" in names and "mirrorvoice/ui/mascot.py" in names
    assert "assets/mascot/hello.png" in names and "assets/icon.png" in names and "patch.json" in names
    assert not [n for n in names if "__pycache__" in n or n.startswith("packaging/")]
    with zipfile.ZipFile(z) as zf:
        assert all(n.split("/", 1)[0] in ("mirrorvoice", "assets", "patch.json") for n in zf.namelist())


def test_runtime_id_changes_with_imports_and_dependencies(monkeypatch):
    a = runtime_files.runtime_id("macos-arm64", "6.22.3")
    assert a == runtime_files.runtime_id("macos-arm64", "6.22.3") and a.startswith("rt-")
    assert a != runtime_files.runtime_id("windows-x64", "6.22.3")
    assert a != runtime_files.runtime_id("macos-arm64", "6.23.0")
    monkeypatch.setattr(runtime_files, "external_imports", lambda: ["difflib"])
    assert a != runtime_files.runtime_id("macos-arm64", "6.22.3")


@pytest.fixture
def signed_release(http_files, monkeypatch):
    """A release with an installer, a code patch and a signed update.json, served locally."""
    base, files, srv = http_files
    new = bump(__version__)
    tag = updater.platform_tag() or "macos-arm64"
    inst = "MirrorVoice-9-macos-arm64.dmg" if sys.platform == "darwin" else "MirrorVoice-9-windows-x64-setup.exe"
    zbytes = _zip(Path(os.environ["MIRRORVOICE_HOME"]) / "src.zip", new).read_bytes()
    pname = f"MirrorVoice-{new}-patch.zip"
    man = {"version": new, "patch": {"name": pname, "size": len(zbytes), "sha256": hashlib.sha256(zbytes).hexdigest()},
           "platforms": {tag: {"runtime": "rt-a", "installer": None}}}
    body = json.dumps(man).encode()

    def publish(manifest=body, sig=None):
        files.update({f"/{inst}": b"installer", f"/{pname}": zbytes, "/update.json": manifest,
                      "/update.json.sig": (sig or ed25519.sign(SEED, manifest).hex()).encode()})
        assets = [{"name": n, "browser_download_url": f"{base}/{n}", "size": len(files[f"/{n}"])}
                  for n in (inst, pname, "update.json", "update.json.sig")]
        files["/feed.json"] = json.dumps({"tag_name": "v" + new, "html_url": "x", "body": "notes",
                                          "assets": assets}).encode()
    publish()
    monkeypatch.setattr(updater, "FEED", f"{base}/feed.json")
    monkeypatch.setattr(updater, "PUBLIC_KEY", PUB)
    monkeypatch.setattr(updater, "UPDATES", Path(os.environ["MIRRORVOICE_HOME"]) / "patch-test-updates")
    monkeypatch.setattr(patches, "bundle_info", lambda: {"version": __version__, "runtime": "rt-a"})
    return new, publish


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_patch_is_offered_only_with_a_good_signature_and_the_same_runtime(signed_release, monkeypatch):
    new, publish = signed_release
    rel = updater.check()
    assert rel.kind == "patch" and rel.version == new and rel.runtime == "rt-a" and rel.size < 1000
    publish(sig="00" * 64)
    assert updater.check().kind == "full"  # bad signature: the installer
    publish(manifest=b'{"version": "1.2.3"}')
    assert updater.check().kind == "full"  # signed, but for another version
    publish()
    monkeypatch.setattr(patches, "bundle_info", lambda: {"version": __version__, "runtime": "rt-OTHER"})
    assert updater.check().kind == "full"  # this bundle has another runtime
    monkeypatch.setattr(patches, "bundle_info", lambda: {"version": __version__, "runtime": "rt-a"})
    monkeypatch.setattr(patches, "bad_versions", lambda: [new])
    assert updater.check().kind == "full"  # the patch failed here before


@pytest.mark.skipif(sys.platform not in ("darwin", "win32"), reason="installers exist for macOS / Windows only")
def test_patch_download_and_install(signed_release, monkeypatch, tmp_path):
    new, _ = signed_release
    monkeypatch.setattr(patches, "APP", tmp_path / "app")
    monkeypatch.setattr(updater, "can_self_install", lambda: True)
    monkeypatch.setenv("MIRRORVOICE_UPDATE_NO_RELAUNCH", "1")
    rel = updater.check()
    path = updater.download(rel, lambda d, t: None)
    updater.install(path, rel)
    assert json.loads((tmp_path / "app" / "active.json").read_text()) == {"version": new}
    assert json.loads((tmp_path / "app" / new / "patch.json").read_text())["runtime"] == "rt-a"
    assert not path.exists()  # the zip is gone once unpacked


@pytest.mark.skipif(sys.platform != "darwin", reason="macOS relaunch script")
def test_mac_relaunch_stops_other_windows_then_opens(tmp_path):
    """A command-line patch update while the app window ran: `open` only brought the old window
    forward, still on the old code. The relaunch script stops it first."""
    bundle = tmp_path / "镜声.app"
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    fake = bundle / "Contents" / "MacOS" / "MirrorVoice"
    fake.write_text("#!/bin/sh\nsleep 60\n")
    fake.chmod(0o755)
    old = subprocess.Popen([str(fake)])
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "open").write_text(f"#!/bin/sh\necho opened \"$1\" > {tmp_path / 'opened.txt'}\n")
    (bindir / "open").chmod(0o755)
    gone = subprocess.Popen(["true"])
    gone.wait()
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    subprocess.run(["/bin/sh", "-c", updater.RELAUNCH_MAC, "relaunch", str(gone.pid), str(bundle)], env=env,
                   timeout=30, check=True)
    assert old.wait(timeout=5) is not None  # the old window was stopped
    assert (tmp_path / "opened.txt").read_text().strip() == f"opened {bundle}"
