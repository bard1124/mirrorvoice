"""The app's first icon, from packaging/art/owl-source.jpg (the owl with headphones on a white
page with a lavender disc): the owl on a lavender rounded tile, written to packaging/art/owl-icon.png.

The icon in use is the two speech bubbles of packaging/art/make_bubbles.py; the owl stays as the
mascot inside the app. This script no longer writes into packaging/assets.

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


def largest(mask: np.ndarray) -> np.ndarray:
    lab, n = ndimage.label(mask)
    return lab == 1 + int(np.argmax(ndimage.sum(mask, lab, range(1, n + 1))))


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


def main() -> None:
    src = Image.open(ART / "owl-source.jpg").convert("RGB")
    a = np.asarray(src).astype(float)
    owl = owl_mask(a)
    icon(src, owl).save(ART / "owl-icon.png")
    print("wrote", ART / "owl-icon.png")


if __name__ == "__main__":
    main()
