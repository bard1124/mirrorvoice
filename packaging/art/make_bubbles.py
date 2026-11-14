"""Draw the app icon: two speech bubbles, 中 on the solid one and A on the glass one over it.

Writes into packaging/assets:
  icon.png              the full artwork, 1024 px, on its rounded tile (Windows uses it as it is)
  icon-small.png        the same idea drawn for 16–32 px: larger bubbles, heavier strokes, no glass
  menubar-template.png  the two bubbles alone, as a mask for the macOS menu bar
Then run packaging/mac/make_icon.py for icon-macos.png, icon.icns and icon.ico.

    .venv/bin/python packaging/art/make_bubbles.py
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QGuiApplication, QImage, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parents[1] / "assets"
N, RADIUS = 1024, 230  # the tile, as the artwork before it
INDIGO, INDIGO_D = "#5B4BE8", "#4334C8"


def bubble(x, y, w, h, r, tail, t):
    """One outline for the box and its tail (bl / br): a translucent fill must not overlap itself."""
    top = f"M{x + r} {y}H{x + w - r}A{r} {r} 0 0 1 {x + w} {y + r}V{y + h - r}"
    if tail == "bl":
        return (top + f"A{r} {r} 0 0 1 {x + w - r} {y + h}H{x + r * .7 + t * 1.3}L{x + r * .7} {y + h + t}V{y + h - 2}"
                f"A{r} {r} 0 0 1 {x} {y + h - r}V{y + r}A{r} {r} 0 0 1 {x + r} {y}Z")
    return (top + f"A{r} {r} 0 0 1 {x + w - r * .7} {y + h - 2}V{y + h + t}L{x + w - r * .7 - t * 1.3} {y + h}H{x + r}"
            f"A{r} {r} 0 0 1 {x} {y + h - r}V{y + r}A{r} {r} 0 0 1 {x + r} {y}Z")


def zhong(cx, cy, s, color, w):
    """中, drawn: a wide box and the stroke through it (no font, so no font's licence)."""
    return (f'<g fill="none" stroke="{color}" stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round">'
            f'<rect x="{cx - .42 * s}" y="{cy - .24 * s}" width="{.84 * s}" height="{.44 * s}" rx="{w * .25}"/>'
            f'<path d="M{cx} {cy - .5 * s}V{cy + .5 * s}"/></g>')


def letter_a(cx, cy, s, color, w):
    return (f'<g fill="none" stroke="{color}" stroke-width="{w}" stroke-linejoin="round" stroke-linecap="round">'
            f'<path d="M{cx - .36 * s} {cy + .45 * s}L{cx} {cy - .45 * s}L{cx + .36 * s} {cy + .45 * s}"/>'
            f'<path d="M{cx - .2 * s} {cy + .16 * s}H{cx + .2 * s}"/></g>')


def ground(stops, blobs):
    """A diagonal wash with soft patches of colour on it."""
    n = len(stops) - 1
    defs = '<linearGradient id="g" x1="0" y1="0" x2="1" y2="1">' + "".join(
        f'<stop offset="{i / n:.2f}" stop-color="{c}"/>' for i, c in enumerate(stops)) + "</linearGradient>"
    layers = f'<rect width="{N}" height="{N}" fill="url(#g)"/>'
    for k, (cx, cy, r, color, op) in enumerate(blobs):
        defs += (f'<radialGradient id="b{k}" cx="{cx}" cy="{cy}" r="{r}" gradientUnits="userSpaceOnUse"><stop offset="0" stop-color="{color}" '
                 f'stop-opacity="{op}"/><stop offset="1" stop-color="{color}" stop-opacity="0"/></radialGradient>')
        layers += f'<rect width="{N}" height="{N}" fill="url(#b{k})"/>'
    return defs, layers


def svg(body, defs=""):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {N} {N}" width="{N}" height="{N}"><defs>{defs}</defs>{body}</svg>'


def artwork() -> str:
    defs, body = ground(["#FFFFFF", "#F1EDFF", "#FFEDE2"],
                        [(160, 150, 500, "#B9A6FF", .75), (880, 880, 520, "#FFB38A", .8), (880, 140, 360, "#9AD8FF", .7)])
    defs += ('<linearGradient id="gl" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".78"/>'
             '<stop offset="1" stop-color="#fff" stop-opacity=".42"/></linearGradient>')
    bx, by, fx, fy, w, h, r, t = 140, 150, 404, 404, 480, 390, 124, 104
    back, front = bubble(bx, by, w, h, r, "bl", t), bubble(fx, fy, w, h, r, "br", t)
    body += (f'<path d="{back}" fill="{INDIGO}"/>' + zhong(bx + w / 2, by + h / 2 - 6, 206, "#fff", 46)
             + f'<path d="{front}" fill="{INDIGO_D}" fill-opacity=".13" transform="translate(0 14)"/>'
             f'<path d="{front}" fill="url(#gl)" stroke="#fff" stroke-width="8"/>' + letter_a(fx + w / 2, fy + h / 2 + 8, 206, INDIGO_D, 46)
             # the tile's own edge: it is nearly white, and Windows shows it without a shadow
             + f'<rect x="4" y="4" width="{N - 8}" height="{N - 8}" rx="{RADIUS - 4}" fill="none" stroke="#B9A6FF" stroke-opacity=".5" stroke-width="8"/>')
    return svg(body, defs)


def artwork_small() -> str:
    defs, body = ground(["#EFE9FF", "#E0D6FF", "#FFDCC6"], [(880, 880, 520, "#FFA877", .7), (880, 140, 360, "#8ED2FF", .6)])
    bx, by, fx, fy, w, h, r, t = 84, 96, 372, 392, 560, 450, 150, 110
    back, front = bubble(bx, by, w, h, r, "bl", t), bubble(fx, fy, w, h, r, "br", t)
    body += (f'<path d="{back}" fill="{INDIGO}"/>' + zhong(bx + w / 2 - 24, by + h / 2 - 30, 250, "#fff", 70)
             + f'<path d="{front}" fill="{INDIGO_D}" fill-opacity=".28" transform="translate(0 22)"/>'
             f'<path d="{front}" fill="#fff" stroke="{INDIGO}" stroke-opacity=".35" stroke-width="14"/>'
             + letter_a(fx + w / 2, fy + h / 2 + 8, 260, INDIGO_D, 70)
             + f'<rect x="10" y="10" width="{N - 20}" height="{N - 20}" rx="{RADIUS - 10}" fill="none" stroke="#9A86F0" stroke-opacity=".6" stroke-width="20"/>')
    return svg(body, defs)


def render(source: str, px: int = N) -> QImage:
    img = QImage(px, px, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    QSvgRenderer(QByteArray(source.encode())).render(p, QRectF(0, 0, px, px))
    p.end()
    return img


def on_tile(art: QImage) -> QImage:
    out = QImage(N, N, QImage.Format.Format_ARGB32_Premultiplied)
    out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, N, N), RADIUS, RADIUS)
    p.setClipPath(path)
    p.drawImage(0, 0, art)
    p.end()
    return out


def menubar(px: int = 64) -> QImage:
    """The two bubbles as a mask: the back one solid, the front one an outline, a clear gap between."""
    bx, by, fx, fy, w, h, r, t = 60, 90, 400, 400, 560, 440, 150, 120
    back, front = bubble(bx, by, w, h, r, "bl", t), bubble(fx, fy, w, h, r, "br", t)
    img = render(svg(f'<path d="{back}" fill="#000"/>'))
    p = QPainter(img)
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationOut)
    p.drawImage(0, 0, render(svg(f'<path d="{front}" fill="#000" stroke="#000" stroke-width="150" stroke-linejoin="round"/>')))
    p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    p.drawImage(0, 0, render(svg(f'<path d="{bubble(fx + 40, fy + 40, w - 80, h - 80, r - 40, "br", t - 30)}" fill="none" stroke="#000" '
                                 f'stroke-width="80" stroke-linejoin="round"/>')))
    p.end()
    while img.width() > px * 2:
        img = img.scaled(img.width() // 2, img.height() // 2, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return img.scaled(px, px, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation)


def main() -> None:
    QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    on_tile(render(artwork())).save(str(ASSETS / "icon.png"))
    on_tile(render(artwork_small())).save(str(ASSETS / "icon-small.png"))
    menubar().save(str(ASSETS / "menubar-template.png"))
    print("wrote icon.png, icon-small.png and menubar-template.png in", ASSETS)


if __name__ == "__main__":
    main()
