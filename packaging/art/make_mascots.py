"""Cut the sidebar mascot's six poses out of each theme's atlas (packaging/art/mascots/*-atlas.png)
and write them to packaging/assets/mascot/<set>/ for mirrorvoice/ui/mascot.py.

Each atlas is a 3 x 2 grid of the same girl (the order is the one in <name>.json), cut out from a
black backdrop. Where her lines were near black the cut-out punched pinholes into her (hair
strands, eyelashes, the bow) and slits along her ponytail: on a light window they show as white
specks and streaks. Every small hole she surrounds and every slit a few pixels wide is put back as
it looked on black; the gaps between her arms and legs are wider and stay open. Specks around her are dropped. All six poses of a set share one canvas, her head at the
same spot and her lowest point on the bottom edge, so a pose change neither jumps nor rescales her.

    .venv/bin/python packaging/art/make_mascots.py      (needs Pillow, NumPy and SciPy)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ART = Path(__file__).resolve().parent / "mascots"
OUT = Path(__file__).resolve().parents[1] / "assets" / "mascot"
# atlas -> the set's folder (the themes using each set are in mascot.py)
SETS = {"quiet": "quiet", "console": "studio", "talk": "chat"}
# the grid, row by row, as the app calls the poses
GRID = (("hello", "smile", "wave"), ("laptop", "sleepy", "cheer"))
HOLE_MAX = 400    # px: a hole at most this big inside her is a pinhole, not a gap between limbs
CRACK = 2         # px: slits narrower than about twice this (open to the outside, along the ponytail) close
SPECK_MIN = 30    # px: smaller loose bits are dust
HEIGHT = 360      # px of the canvas (the sidebar draws her ~120 pt high: room for a 3x screen)
PAD = 6


def poses(atlas: Path) -> dict[str, np.ndarray]:
    """The six poses, each the atlas with only her pieces left in."""
    a = np.asarray(Image.open(atlas).convert("RGBA"))
    h, w = a.shape[:2]
    lab, n = ndimage.label(a[..., 3] > 20)
    out = {}
    for row, names in enumerate(GRID):
        for col, name in enumerate(names):
            out[name] = np.zeros_like(a)
    sizes = ndimage.sum(np.ones(lab.shape), lab, range(1, n + 1))
    centres = ndimage.center_of_mass(np.ones(lab.shape), lab, range(1, n + 1))
    for i, (size, (cy, cx)) in enumerate(zip(sizes, centres), start=1):
        if size < SPECK_MIN:
            continue
        name = GRID[min(1, int(cy * 2 // h))][min(2, int(cx * 3 // w))]
        piece = lab == i
        out[name][piece] = a[piece]
    return out


def mend(im: np.ndarray) -> np.ndarray:
    """Fill her pinholes as they looked on the black backdrop (colour times what alpha was there)."""
    rgb, alpha = im[..., :3].astype(float), im[..., 3].astype(float)
    solid = alpha > 200
    inside = ndimage.binary_fill_holes(solid) & ~solid
    lab, n = ndimage.label(inside)
    sizes = ndimage.sum(np.ones(lab.shape), lab, range(1, n + 1))
    small = np.isin(lab, [i + 1 for i, s in enumerate(sizes) if s <= HOLE_MAX])
    disk = ndimage.iterate_structure(ndimage.generate_binary_structure(2, 1), CRACK)
    small |= ndimage.binary_closing(solid, structure=disk) & ~solid
    rgb = np.where(small[..., None], rgb * alpha[..., None] / 255, rgb)
    alpha = np.where(small, 255, alpha)
    return np.dstack([rgb, alpha]).clip(0, 255).astype(np.uint8)


def write_set(atlas: Path, folder: Path) -> tuple[int, int]:
    imgs = {k: mend(v) for k, v in poses(atlas).items()}
    marks = {}
    for k, im in imgs.items():
        al = im[..., 3] > 20
        ys, xs = np.where(al)
        top, bottom = ys.min(), ys.max()
        head = al[top: top + int((bottom - top) * 0.45)]
        marks[k] = (np.where(head)[1].mean(), xs.min(), xs.max(), top, bottom)  # head centre: what the eye follows
    left = max(m[0] - m[1] for m in marks.values()) + PAD
    right = max(m[2] - m[0] for m in marks.values()) + PAD
    tall = max(m[4] - m[3] for m in marks.values()) + PAD
    w, h = int(round(left + right)), int(tall)
    scale = HEIGHT / h
    size = (int(round(w * scale)), HEIGHT)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("*.png"):
        old.unlink()
    for k, im in imgs.items():
        hx, x0, x1, top, bottom = marks[k]
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.alpha_composite(Image.fromarray(im).crop((0, top, im.shape[1], bottom + 1)),
                               (int(round(left - hx)), int(h - 1 - (bottom - top))))
        small = canvas.resize(size, Image.LANCZOS)  # 256 colours: a fifth of the bytes, the same at sidebar size
        small.quantize(256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE).save(
            folder / f"{k}.png", optimize=True)
    return size


def main() -> None:
    for old in OUT.glob("*.png"):  # the poses of the first girl lay in the folder itself
        old.unlink()
    for atlas, name in SETS.items():
        size = write_set(ART / f"{atlas}-atlas.png", OUT / name)
        print(f"wrote {name}: {', '.join(p for row in GRID for p in row)} ({size[0]}x{size[1]})")


if __name__ == "__main__":
    main()
