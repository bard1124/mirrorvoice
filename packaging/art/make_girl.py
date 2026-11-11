"""Clean up the owl-girl pose pictures (packaging/art/owl-girl/*.png) for the control panel mascot
(mirrorvoice/ui/mascot.py) and write them to packaging/assets/mascot/.

The cut-outs came with holes punched into the eyes and headphones (their whites and highlights
were taken for background) and a few stray specks. Here the holes are filled from the nearest
solid colours, specks are dropped, and every pose is placed on one canvas with its head at the
same spot and its lowest point on the bottom edge, so switching poses does not jump.

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


if __name__ == "__main__":
    main()
