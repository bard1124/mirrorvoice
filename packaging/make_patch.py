"""Release step: build the code patch and the signed update manifest.

    python packaging/make_patch.py --runtimes out/ --out rel/

- rel/MirrorVoice-<version>-patch.zip: the app layer (mirrorvoice/ + assets/, see runtime_files.py)
- rel/update.json: version, the patch, and per platform the runtime its installer was built on
  (from dist/runtime-<platform>.json of each build job) plus the installer's name / size / sha256
- rel/update.json.sig: hex Ed25519 signature of update.json with the key in $PATCH_SIGNING_KEY
  (base64 of the 32-byte private key). Without the key no manifest is written, so the release
  only offers full installers — the app never trusts an unsigned patch.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import runtime_files  # noqa: E402

from mirrorvoice import __version__, ed25519  # noqa: E402

STAMP = (2020, 1, 1, 0, 0, 0)  # fixed times: the same sources give the same zip


def build_zip(dest: Path, version: str) -> None:
    root = runtime_files.ROOT
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for rel in runtime_files.app_files():
            parts = rel.parts
            arc = "/".join(("assets",) + parts[2:]) if parts[:2] == ("packaging", "assets") else "/".join(parts)
            info = zipfile.ZipInfo(arc, STAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, (root / rel).read_bytes())
        info = zipfile.ZipInfo("patch.json", STAMP)
        zf.writestr(info, json.dumps({"version": version}))


def describe(p: Path) -> dict:
    return {"name": p.name, "size": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runtimes", type=Path, required=True, help="folder with runtime-<platform>.json files")
    ap.add_argument("--out", type=Path, required=True, help="release folder (installers already in it)")
    ap.add_argument("--version", default=__version__)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    zpath = a.out / f"MirrorVoice-{a.version}-patch.zip"
    build_zip(zpath, a.version)
    platforms = {}
    for rt in sorted(a.runtimes.rglob("runtime-*.json")):
        info = json.loads(rt.read_text())
        if info.get("version") != a.version:
            sys.exit(f"{rt} is for {info.get('version')}, not {a.version}")
        tag = info["platform"]
        pattern = "*macos-arm64.dmg" if tag == "macos-arm64" else "*windows-x64-setup.exe"
        inst = next(iter(sorted(a.out.glob(pattern))), None)
        platforms[tag] = {"runtime": info["runtime"], "installer": describe(inst) if inst else None}
    manifest = {"version": a.version, "patch": describe(zpath), "platforms": platforms}
    body = json.dumps(manifest, indent=1, sort_keys=True).encode()
    print(body.decode())
    key = os.environ.get("PATCH_SIGNING_KEY", "").strip()
    if not key:
        print("PATCH_SIGNING_KEY not set: no update.json, this release offers full installers only")
        return 0
    seed = base64.b64decode(key)
    (a.out / "update.json").write_bytes(body)
    (a.out / "update.json.sig").write_text(ed25519.sign(seed, body).hex())
    print("signed with key", ed25519.public_key(seed).hex())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
