"""Clean up the owl-girl pose pictures (packaging/art/owl-girl/*.png) for the control panel mascot
(mirrorvoice/ui/mascot.py) and write them to packaging/assets/mascot/, and the six frames of her
update routine (owl-update-*.png) to packaging/assets/mascot/update/.

The cut-outs came with holes punched into the eyes and headphones (their whites and highlights
were taken for background) and a few stray specks. Here the holes are filled from the nearest
solid colours, specks are dropped, and every pose is placed on one canvas with its head at the
same spot and its lowest point on the bottom edge, so switching poses does not jump.

The update frames were cut from a black backdrop, so whatever was dark went transparent with it:
the closed eyes, the headset, the microphone. Those holes are put back as they looked on black
(the gaps between her legs, arms and tail stay open). The glow under her boots is taken out —
on a light window it turns into a dark smudge — and drawn by the app instead, where it can pulse.
These frames share the top edge (she floats, so her feet move, not her head).

    .venv/bin/python packaging/art/make_girl.py      (needs Pillow, NumPy and SciPy)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ART = Path(__file__).resolve().parent / "owl-girl"
OUT = Path(__file__).resolve().parents[1] / "assets" / "mascot"
POSES = {"hello": "01-hello", "smile": "02-smile", "wave": "03-wave", "laptop": "04-laptop",
         "sleepy": "05-shy", "cheer": "06-cheer"}
UPDATE = ("01-lift", "02-cross", "03-curl", "04-link", "05-rise", "06-land")
PAD = 6


def clean(path: Path) -> np.ndarray:
    a = np.asarray(Image.open(path).convert("RGBA")).astype(float)
    alpha = a[..., 3]
    solid = alpha > 200
    lab, n = ndimage.label(alpha > 20)
    sizes = ndimage.sum(alpha > 20, lab, range(1, n + 1))
    keep = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s >= 0.02 * sizes.max()])  # specks out
    body = ndimage.binary_fill_holes(keep)
    holes = body & ~solid & ~ndimage.binary_erosion(~body, iterations=1)  # inside, not on the outline
    holes &= ndimage.binary_erosion(body, iterations=2)
    _, (iy, ix) = ndimage.distance_transform_edt(~solid, return_indices=True)
    rgb = a[..., :3].copy()
    rgb[holes] = a[iy, ix, :3][holes]
    blur = np.stack([ndimage.gaussian_filter(rgb[..., c], 0.8) for c in range(3)], -1)
    rgb[holes] = blur[holes]
    alpha = np.where(holes, 255, np.where(keep, alpha, 0))
    return np.dstack([rgb, alpha]).clip(0, 255).astype(np.uint8)


def clean_update(path: Path) -> np.ndarray:
    a = np.asarray(Image.open(path).convert("RGBA")).astype(float)
    rgb, alpha = a[..., :3], a[..., 3]
    lab, n = ndimage.label(alpha > 20)
    sizes = ndimage.sum(alpha > 20, lab, range(1, n + 1))
    alpha = np.where(np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s >= 0.02 * sizes.max()]), alpha, 0)
    ys = np.where(alpha > 20)[0]
    top, bottom = ys.min(), ys.max()
    # the glow under the boots: teal around and below them, and its faint rim
    low = np.zeros(alpha.shape, bool)
    low[int(bottom - 0.13 * (bottom - top)):] = True
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    teal = low & (b > r + 18) & (g > r + 8)
    boots = ndimage.binary_dilation(low & (alpha > 200) & ~teal, iterations=1)
    alpha = np.where(teal | (low & (alpha <= 200) & ~boots), 0, alpha)
    # holes keyed out with the black backdrop: in the head, or ringed by the dark headset
    solid = alpha > 200
    holes = ndimage.binary_fill_holes(solid) & ~solid
    ys = np.where(alpha > 20)[0]
    top, bottom = ys.min(), ys.max()
    luma = r * 0.3 + g * 0.59 + b * 0.11
    lab, n = ndimage.label(holes)
    fill = np.zeros(alpha.shape, bool)
    for i in range(1, n + 1):
        hole = lab == i
        ring = ndimage.binary_dilation(hole, iterations=2) & solid
        if (np.where(hole)[0].mean() - top) / (bottom - top) < 0.56 or np.median(luma[ring]) < 60:
            fill |= hole
    rgb = np.where(fill[..., None], rgb * alpha[..., None] / 255, rgb)  # as it looked on black
    alpha = np.where(fill, 255, alpha)
    return np.dstack([rgb, alpha]).clip(0, 255).astype(np.uint8)


def head_mark(im: np.ndarray) -> tuple:
    al = im[..., 3] > 20
    ys, xs = np.where(al)
    top, bottom = ys.min(), ys.max()
    head = al[top: top + int((bottom - top) * 0.45)]
    return np.where(head)[1].mean(), xs.min(), xs.max(), top, bottom  # head centre: what the eye follows


def write_update() -> None:
    imgs = {k.split("-", 1)[1]: clean_update(ART / f"owl-update-{k}.png") for k in UPDATE}
    marks = {k: head_mark(im) for k, im in imgs.items()}
    left = max(m[0] - m[1] for m in marks.values()) + PAD
    right = max(m[2] - m[0] for m in marks.values()) + PAD
    w, h = int(round(left + right)), int(max(m[4] - m[3] for m in marks.values()) + 2 * PAD)
    out = OUT / "update"
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("*.png"):
        old.unlink()
    for k, im in imgs.items():
        hx, x0, x1, top, bottom = marks[k]
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.alpha_composite(Image.fromarray(im), (int(round(left - hx)), int(PAD - top)))
        canvas.save(out / f"{k}.png", optimize=True)
    print("wrote", ", ".join(imgs), "to", out, f"({w}x{h})")


def main() -> None:
    imgs = {k: clean(ART / f"owl-girl-{v}.png") for k, v in POSES.items()}
    marks = {}
    for k, im in imgs.items():
        al = im[..., 3] > 20
        ys, xs = np.where(al)
        top, bottom = ys.min(), ys.max()
        head = al[top: top + int((bottom - top) * 0.45)]
        hx = np.where(head)[1].mean()  # the head's centre: what the eye follows between poses
        marks[k] = (hx, xs.min(), xs.max(), top, bottom)
    left = max(m[0] - m[1] for m in marks.values()) + PAD
    right = max(m[2] - m[0] for m in marks.values()) + PAD
    height = max(m[4] - m[3] for m in marks.values()) + PAD
    w, h = int(round(left + right)), int(height)
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.png"):
        old.unlink()
    for k, im in imgs.items():
        hx, x0, x1, top, bottom = marks[k]
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.alpha_composite(Image.fromarray(im), (int(round(left - hx)), int(h - 1 - bottom)))
        canvas.save(OUT / f"{k}.png", optimize=True)
    print("wrote", ", ".join(POSES), "to", OUT, f"({w}x{h})")
    write_update()


if __name__ == "__main__":
    main()
