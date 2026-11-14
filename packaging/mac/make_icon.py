"""Build the macOS icon from packaging/assets/icon.png (the full-bleed artwork, also used on Windows).

With an icon-small.png beside it (the same picture drawn for small sizes, packaging/art/make_bubbles.py),
the 16–32 px entries of the .icns and the .ico are made from that one: scaled down, the full artwork's
glass and thin strokes turn to a blur.

macOS icons follow Apple's grid: the rounded tile is 824 x 824 centred on a 1024 canvas with a soft
shadow below it. A full-bleed tile shows one size larger than every other icon in the Dock and
Launchpad. Writes packaging/assets/icon-macos.png (runtime Dock icon), icon.icns and the Windows
icon.ico (Pillow).

    .venv/bin/python packaging/mac/make_icon.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QGraphicsPixmapItem, QGraphicsScene

ASSETS = Path(__file__).resolve().parents[1] / "assets"
CANVAS, TILE = 1024, 824  # Apple macOS app icon grid
SHADOW = dict(blur=28.0, dy=10.0, alpha=0.30)


def padded(src: QImage) -> QImage:
    tile = QPixmap.fromImage(src.scaled(TILE, TILE, Qt.AspectRatioMode.IgnoreAspectRatio,
                                        Qt.TransformationMode.SmoothTransformation))
    scene = QGraphicsScene(0, 0, CANVAS, CANVAS)
    item = QGraphicsPixmapItem(tile)
    item.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
    off = (CANVAS - TILE) / 2
    item.setPos(off, off)
    fx = QGraphicsDropShadowEffect()
    fx.setBlurRadius(SHADOW["blur"])
    fx.setOffset(0, SHADOW["dy"])
    fx.setColor(QColor(0, 0, 0, int(255 * SHADOW["alpha"])))
    item.setGraphicsEffect(fx)
    scene.addItem(item)
    out = QImage(CANVAS, CANVAS, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    scene.render(p, QRectF(0, 0, CANVAS, CANVAS), QRectF(0, 0, CANVAS, CANVAS))
    p.end()
    return out


SMALL_UP_TO = 32  # px: at and below this, the small drawing


def write_icns(img: QImage, dest: Path, small: QImage | None = None) -> None:
    sizes = {"16x16": 16, "16x16@2x": 32, "32x32": 32, "32x32@2x": 64, "128x128": 128, "128x128@2x": 256,
             "256x256": 256, "256x256@2x": 512, "512x512": 512, "512x512@2x": 1024}
    tmp = Path(tempfile.mkdtemp())
    iconset = tmp / "icon.iconset"
    iconset.mkdir()
    for name, px in sizes.items():
        src = small if small is not None and px <= SMALL_UP_TO else img
        src.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio,
                   Qt.TransformationMode.SmoothTransformation).save(str(iconset / f"icon_{name}.png"))
    subprocess.run(["iconutil", "-c", "icns", "-o", str(dest), str(iconset)], check=True)
    shutil.rmtree(tmp)


def write_ico(src: Path, dest: Path, small: Path | None = None) -> None:
    """Windows: the full-bleed artwork at every size Explorer and the taskbar ask for (Pillow)."""
    from PIL import Image

    sizes = (16, 24, 32, 48, 64, 128, 256)
    big = Image.open(src).convert("RGBA")
    drawn = []
    if small is not None:
        art = Image.open(small).convert("RGBA")
        drawn = [art.resize((n, n), Image.LANCZOS) for n in sizes if n <= SMALL_UP_TO]
    big.save(dest, sizes=[(n, n) for n in sizes], append_images=drawn)


def main() -> None:
    QApplication.instance() or QApplication(sys.argv[:1])
    src = QImage(str(ASSETS / "icon.png"))
    if src.isNull() or src.width() != CANVAS:
        sys.exit(f"need a {CANVAS} px {ASSETS / 'icon.png'}")
    img = padded(src)
    img.save(str(ASSETS / "icon-macos.png"))
    small = ASSETS / "icon-small.png"
    write_icns(img, ASSETS / "icon.icns", padded(QImage(str(small))) if small.exists() else None)
    write_ico(ASSETS / "icon.png", ASSETS / "icon.ico", small if small.exists() else None)
    print("wrote", ASSETS / "icon-macos.png", ASSETS / "icon.icns", "and", ASSETS / "icon.ico")


if __name__ == "__main__":
    main()
