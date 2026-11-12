#!/bin/bash
# Build dist/MirrorVoice-<version>-macos-arm64.dmg (drag-to-Applications installer).
# Prereqs: python venv with requirements-mac.txt + pyinstaller; Xcode command line tools.
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${PYTHON:-.venv/bin/python}
VERSION=$($PY -c "import mirrorvoice; print(mirrorvoice.__version__)")

packaging/mac/fetch_vendor.sh
$PY -m PyInstaller --noconfirm --clean --log-level WARN --distpath dist --workpath build packaging/mirrorvoice.spec

APP="dist/翻了么.app"
codesign --force --deep -s - "$APP"   # ad-hoc signature (not notarized)

STAGE=build/dmg
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp packaging/mac/README-安装说明.txt "$STAGE/"
DMG="dist/MirrorVoice-${VERSION}-macos-arm64.dmg"
rm -f "$DMG"
hdiutil create -volname "翻了么 ${VERSION}" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
echo "built $DMG ($(du -h "$DMG" | cut -f1))"
