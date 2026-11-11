"""Start-up of the frozen app: run a downloaded code patch when there is a good one.

A code patch (see packaging/runtime_files.py) is the mirrorvoice package plus its images, unpacked
in <data folder>/app/<version>/. This file is part of the bundle and is never patched itself; it
picks the patch named in app/active.json only when
- it was made for the runtime this bundle carries (runtime.json next to the executable),
- it is newer than the bundled code (after a full update older patches are ignored),
- it has not failed before (app/bad.json) and has not failed to start twice in a row
  (app/boot.json counts starts the app has not confirmed yet — mirrorvoice.patches.confirm_boot).
If importing the patched code fails, the patch is marked bad and the bundled code runs instead.
Nothing here may raise: any trouble means "run the bundled code".
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

MAX_TRIES = 2  # starts in a row that never got confirmed -> the patch is bad
ENV = "MIRRORVOICE_PATCH"  # set to the patch version while running one


def ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3]) or (0,)


def home() -> Path:
    """The data folder — the same rule as mirrorvoice.config.data_dir (kept in step by a test)."""
    env = os.environ.get("MIRRORVOICE_HOME") or os.environ.get("TONGCHUAN_HOME")
    if env:
        return Path(env)
    from platformdirs import user_data_dir

    old = Path(user_data_dir("TongChuan", appauthor=False))
    new = Path(user_data_dir("MirrorVoice", appauthor=False))
    return old if not new.exists() and old.is_dir() else new


def bundle_dir() -> Path:
    if os.environ.get("MIRRORVOICE_BUNDLE_DIR"):  # tests
        return Path(os.environ["MIRRORVOICE_BUNDLE_DIR"])
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))


def bundle_info() -> dict:
    """{"version", "runtime", "platform"} written into the bundle at build time ({} if missing)."""
    try:
        return json.loads((bundle_dir() / "runtime.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _read(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(p: Path, data) -> None:
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    os.replace(tmp, p)


def log(app: Path, msg: str) -> None:
    try:
        with open(app / "boot.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def mark_bad(app: Path, version: str, why: str) -> None:
    bad = _read(app / "bad.json", [])
    if version not in bad:
        _write(app / "bad.json", bad + [version])
    log(app, f"patch {version} marked bad: {why}")


def choose(app: Path, base: dict) -> Path | None:
    """The patch folder to run, or None for the bundled code."""
    version = _read(app / "active.json", {}).get("version", "")
    if not version or not base.get("runtime"):
        return None
    d = app / version
    meta = _read(d / "patch.json", {})
    if not (d / "mirrorvoice" / "__init__.py").is_file() or meta.get("version") != version:
        return None
    if meta.get("runtime") != base["runtime"]:
        log(app, f"patch {version} is for runtime {meta.get('runtime')}, this bundle is {base['runtime']}")
        return None
    if ver(version) <= ver(base.get("version", "0")):
        return None  # the bundle is as new (a full update came after the patch)
    if version in _read(app / "bad.json", []):
        return None
    tries = _read(app / "boot.json", {})
    if tries.get("version") == version and tries.get("tries", 0) >= MAX_TRIES:
        mark_bad(app, version, f"did not start {MAX_TRIES} times in a row")
        return None
    return d


def _drop_loaded() -> None:
    for name in [m for m in sys.modules if m == "mirrorvoice" or m.startswith("mirrorvoice.")]:
        del sys.modules[name]


def boot(run=None) -> None:
    """Import mirrorvoice (patched when possible) and run its main()."""
    patch = app = None
    os.environ.pop(ENV, None)  # inherited from the app that relaunched us: decided afresh here
    try:
        app = home() / "app"
        patch = choose(app, bundle_info()) if app.is_dir() else None
        if patch is not None:
            tries = _read(app / "boot.json", {})
            n = tries.get("tries", 0) + 1 if tries.get("version") == patch.name else 1
            _write(app / "boot.json", {"version": patch.name, "tries": n})
            sys.path.insert(0, str(patch))
            os.environ[ENV] = patch.name
    except Exception:  # noqa: BLE001 — a broken data folder must not stop the app
        patch = None
    try:
        from mirrorvoice.__main__ import main
    except Exception as e:  # noqa: BLE001
        if patch is None:
            raise
        mark_bad(app, patch.name, f"import failed: {e!r}")
        sys.path.remove(str(patch))
        os.environ.pop(ENV, None)
        _drop_loaded()
        import importlib

        importlib.invalidate_caches()
        from mirrorvoice.__main__ import main
    (run or main)()
