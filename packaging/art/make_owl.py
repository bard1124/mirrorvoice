"""Make the owl artwork from packaging/art/owl-source.jpg (the owl with headphones on a white
page with a lavender disc):

- packaging/assets/icon.png: the owl on a lavender rounded tile (then run packaging/mac/make_icon.py
  for the Dock / .icns / .ico versions)
- packaging/assets/mascot/{body,lids,headset}.png: the layers the control panel animates
  (mirrorvoice/ui/mascot.py): the owl without its headphones (the head behind the ear cups and
  the face under the microphone filled in), its closed eyes, and the headphones on their own.

    .venv/bin/python packaging/art/make_owl.py      (needs Pillow, NumPy and SciPy)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
from scipy import ndimage

ART = Path(__file__).resolve().parent
ASSETS = ART.parent / "assets"
N = 1024
LAV = np.array([231, 217, 252])
EYES = (400, 615), 450  # eye centres (x) and their height in the source
yy, xx = np.mgrid[0:N, 0:N]


def disk(r: int) -> np.ndarray:
    return (xx[:2 * r + 1, :2 * r + 1] - r) ** 2 + (yy[:2 * r + 1, :2 * r + 1] - r) ** 2 <= r * r


def largest(mask: np.ndarray) -> np.ndarray:
    lab, n = ndimage.label(mask)
    return lab == 1 + int(np.argmax(ndimage.sum(mask, lab, range(1, n + 1))))


def inpaint(rgb: np.ndarray, hole: np.ndarray, src: np.ndarray, sigma: float = 6, rounds: int = 4) -> np.ndarray:
    """Fill `hole` from the nearest `src` pixels, then smooth it so the shading carries on."""
    _, (iy, ix) = ndimage.distance_transform_edt(~src, return_indices=True)
    out = rgb.copy()
    out[hole] = rgb[iy, ix][hole]
    for _ in range(rounds):
        blur = np.stack([ndimage.gaussian_filter(out[..., c], sigma) for c in range(3)], -1)
        out[hole] = blur[hole]
    return out


def rgba(rgb: np.ndarray, mask: np.ndarray, shrink: int) -> Image.Image:
    al = Image.fromarray((mask * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(shrink))
    out = Image.fromarray(np.clip(rgb, 0, 255).astype(np.uint8))
    out.putalpha(al.filter(ImageFilter.GaussianBlur(1.0)))
    return out


def owl_mask(a: np.ndarray) -> np.ndarray:
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    paper = (b >= 244) & (r >= 224) & (g >= 208) & (r >= g)  # white, the disc and the blend between
    lab, _ = ndimage.label(paper)
    edge = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    bg = np.isin(lab, list(edge))
    lav = np.abs(a - LAV).max(axis=2) <= 16  # the disc seen through gaps (under the headband)
    lab2, n2 = ndimage.label(lav)
    sizes = ndimage.sum(lav, lab2, range(1, n2 + 1))
    bg |= np.isin(lab2, [i + 1 for i, s in enumerate(sizes) if s > 400])
    return largest(~bg)  # specks of paper texture dropped


def icon(src: Image.Image, owl: np.ndarray) -> Image.Image:
    ys, xs = np.where(owl)
    al = Image.fromarray((owl * 255).astype(np.uint8)).filter(ImageFilter.MinFilter(5))
    cut = src.copy()
    cut.putalpha(al.filter(ImageFilter.GaussianBlur(1.0)))
    cut = cut.crop((xs.min() - 2, ys.min() - 2, xs.max() + 3, ys.max() + 3))
    top, bot = np.array([238, 229, 254]), np.array([216, 199, 250])
    t = np.linspace(0, 1, N)[:, None, None]
    tile = Image.fromarray((top * (1 - t) + bot * t).repeat(N, axis=1).astype(np.uint8), "RGB").convert("RGBA")
    h = int(N * 0.82)
    w = round(cut.width * h / cut.height)
    tile.alpha_composite(cut.resize((w, h), Image.LANCZOS), ((N - w) // 2, (N - h) // 2 + int(N * 0.02)))
    mask = Image.new("L", (N * 4, N * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, N * 4 - 1, N * 4 - 1), 230 * 4, fill=255)
    out = Image.new("RGBA", (N, N), (0, 0, 0, 0))
    out.paste(tile, (0, 0), mask.resize((N, N), Image.LANCZOS))
    return out


def layers(a: np.ndarray, owl: np.ndarray) -> dict[str, Image.Image]:
    mx, mn = a.max(axis=2), a.min(axis=2)
    lowsat = (mx - mn) < 40
    eyes = np.zeros_like(owl)
    for cx in EYES[0]:
        eyes |= (xx - cx) ** 2 + (yy - EYES[1]) ** 2 < 110 ** 2
    # headphones: dark grey shapes outside the eyes, then their lighter highlights next to them
    dark = (mx < 110) & ((mx - mn) < 28) & owl & ~eyes
    lab, n = ndimage.label(dark)
    sz = ndimage.sum(dark, lab, range(1, n + 1))
    hs = np.isin(lab, [i + 1 for i, s in enumerate(sz) if s > 300])
    hs |= lowsat & ndimage.binary_dilation(hs, disk(10)) & owl & (mx < 215)
    hs = ndimage.binary_fill_holes(ndimage.binary_closing(hs, disk(3))) & owl
    for cx in EYES[0]:
        hs &= ~((xx - cx) ** 2 + (yy - EYES[1]) ** 2 < 100 ** 2)
    body = owl & ~hs
    body &= ~(ndimage.binary_dilation(hs, disk(8)) & lowsat & (mx < 225) & (yy < 330))  # band edges by the tufts
    body = largest(body)
    head = ((xx - 512) / 252.0) ** 2 + ((yy - 440) / 232.0) ** 2 <= 1
    filled = body | (hs & (head | ndimage.binary_closing(np.pad(body, 40), disk(22))[40:-40, 40:-40]))
    src = ndimage.binary_erosion(body, disk(4))
    body_rgb = inpaint(a, filled & ~src, src)
    # closed eyes: the eyeball becomes face (filled from the face around it), plus a lash line
    lid_mask = np.zeros_like(owl)
    arcs = []
    for cx in EYES[0]:
        eye = (xx - cx) ** 2 + (yy - EYES[1]) ** 2 < 105 ** 2
        ball = eye & ((mn > 200) & lowsat | (a[..., 0] - a[..., 2] > 90) & (a[..., 1] < 190) | (mx < 70))
        ball = largest(ndimage.binary_fill_holes(ndimage.binary_closing(ball, disk(4))))
        ys, xs = np.where(ball)
        cxx, cyy, rad = xs.mean(), ys.mean(), max(np.ptp(xs), np.ptp(ys)) / 2
        lid_mask |= (xx - cxx) ** 2 + (yy - cyy) ** 2 < (rad + 18) ** 2
        arcs.append((cxx, cyy, rad))
    lid_rgb = inpaint(body_rgb, lid_mask, owl & ~lid_mask & ~(mx < 120), sigma=10, rounds=6)
    s4 = 4
    lines = Image.new("L", (N * s4, N * s4), 0)
    d = ImageDraw.Draw(lines)
    for cxx, cyy, rad in arcs:
        w = rad * 0.92
        d.arc(((cxx - w) * s4, (cyy - w * 0.5 - 4) * s4, (cxx + w) * s4, (cyy + w * 0.5 - 4) * s4),
              15, 165, fill=255, width=13 * s4)
    k = np.asarray(lines.resize((N, N), Image.LANCZOS)).astype(float)[..., None] / 255
    lid_rgb = lid_rgb * (1 - k) + np.array([66, 38, 26], float) * k
    return {"body": rgba(body_rgb, filled, 5), "headset": rgba(a, hs, 1),
            "lids": rgba(lid_rgb, ndimage.binary_dilation(lid_mask, disk(2)), 1)}


def main() -> None:
    src = Image.open(ART / "owl-source.jpg").convert("RGB")
    a = np.asarray(src).astype(float)
    owl = owl_mask(a)
    icon(src, owl).save(ASSETS / "icon.png")
    ls = layers(a, owl)
    al = np.maximum(np.asarray(ls["body"])[..., 3], np.asarray(ls["headset"])[..., 3])
    ys, xs = np.where(al > 8)
    side = max(np.ptp(xs), np.ptp(ys)) + 9
    cx, bottom = (xs.min() + xs.max() + 1) // 2, ys.max() + 5
    box = (cx - side // 2, bottom - side, cx - side // 2 + side, bottom)  # square, the owl on its bottom edge
    (ASSETS / "mascot").mkdir(exist_ok=True)
    for name, img in ls.items():
        img.crop(box).resize((288, 288), Image.LANCZOS).save(ASSETS / "mascot" / f"{name}.png", optimize=True)
    print("wrote", ASSETS / "icon.png", "and", ASSETS / "mascot")


if __name__ == "__main__":
    main()
