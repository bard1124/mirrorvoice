"""What the app is made of, for the PyInstaller spec, the patch builder and the tests.

Two layers:
- the runtime: Python, the libraries, the bundled helpers — everything PyInstaller collects. It
  changes only when dependencies or the packaging change, and it is named by runtime_id().
- the app layer: the mirrorvoice package and the images under packaging/assets (ASSETS). A code
  patch replaces just this layer (packaging/make_patch.py); the frozen app loads it at start-up
  when the patch was made for the same runtime (packaging/patchboot.py).

    python packaging/runtime_files.py macos-arm64     -> prints the runtime id
"""
from __future__ import annotations

import ast
import hashlib
import platform
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# packaging/assets entries the running app reads (files or folders), via mirrorvoice.ui.icons.assets_dir()
ASSETS = ("icon.png", "icon-macos.png", "chevron-down.svg", "menubar-template.png", "check-white.svg", "mascot")

PLATFORMS = {"macos-arm64": "requirements-mac.txt", "windows-x64": "requirements-windows.txt"}

# files whose content shapes the runtime (what gets collected and how the app starts)
RUNTIME_INPUTS = ("packaging/mirrorvoice.spec", "packaging/entry.py", "packaging/patchboot.py",
                  "packaging/runtime_files.py")

# run by another interpreter (the Windows CUDA voice pack), not part of the bundle's imports
OUTSIDE_RUNTIME = {"tts_server.py"}


def platform_tag() -> str:
    if sys.platform == "darwin":
        return "macos-arm64"
    if sys.platform == "win32":
        return "windows-x64"
    return f"{sys.platform}-{platform.machine().lower()}"


def app_files() -> list[Path]:
    """The app layer, relative to ROOT: every source file of the package, and the assets."""
    pkg = sorted(p.relative_to(ROOT) for p in (ROOT / "mirrorvoice").rglob("*.py") if "__pycache__" not in p.parts)
    assets = []
    for name in ASSETS:
        p = ROOT / "packaging" / "assets" / name
        assets += sorted(f.relative_to(ROOT) for f in p.rglob("*") if f.is_file()) if p.is_dir() else [p.relative_to(ROOT)]
    return pkg + assets


def external_imports() -> list[str]:
    """Every module the app imports from outside itself (statically). A new one — even from the
    standard library — may not be in an older bundle, so it makes a new runtime."""
    mods: set[str] = set()
    for f in (ROOT / "mirrorvoice").rglob("*.py"):
        if "__pycache__" in f.parts or f.name in OUTSIDE_RUNTIME:
            continue
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"), str(f))):
            if isinstance(node, ast.Import):
                mods.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods.add(node.module)
    return sorted(m for m in mods if m != "mirrorvoice" and not m.startswith("mirrorvoice."))


def runtime_id(tag: str | None = None, pyinstaller: str | None = None) -> str:
    tag = tag or platform_tag()
    h = hashlib.sha256()

    def add(label: str, data: bytes) -> None:
        h.update(label.encode() + b"\0" + data + b"\0")

    add("platform", tag.encode())
    add("python", f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    if pyinstaller is None:
        try:
            import PyInstaller

            pyinstaller = PyInstaller.__version__
        except ImportError:
            pyinstaller = ""
    add("pyinstaller", pyinstaller.encode())
    req = PLATFORMS.get(tag)
    if req:
        add(req, (ROOT / req).read_bytes().replace(b"\r\n", b"\n"))
    for rel in RUNTIME_INPUTS:
        add(rel, (ROOT / rel).read_bytes().replace(b"\r\n", b"\n"))
    add("imports", "\n".join(external_imports()).encode())
    return "rt-" + h.hexdigest()[:16]


if __name__ == "__main__":
    print(runtime_id(sys.argv[1] if len(sys.argv) > 1 else None))
