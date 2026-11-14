"""macOS icons sit on Apple's grid (824 px tile on a 1024 canvas); the full-bleed one looked a size too big."""
import pytest

QtGui = pytest.importorskip("PySide6.QtGui")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _tile_bbox(img):
    w = img.width()
    xs = [x for x in range(0, w, 2) if any(img.pixelColor(x, y).alpha() > 200 for y in range(0, w, 8))]
    ys = [y for y in range(0, w, 2) if any(img.pixelColor(x, y).alpha() > 200 for x in range(0, w, 8))]
    return xs[0], ys[0], xs[-1], ys[-1]


def test_macos_icon_follows_the_apple_grid(app):
    from mirrorvoice.ui.icons import assets_dir

    img = QtGui.QImage(str(assets_dir() / "icon-macos.png"))
    assert (img.width(), img.height()) == (1024, 1024)
    x0, y0, x1, y1 = _tile_bbox(img)
    assert abs(x0 - 100) <= 4 and abs(y0 - 100) <= 4 and abs(x1 - 922) <= 4 and abs(y1 - 922) <= 4
    assert img.pixelColor(512, 4).alpha() == 0  # transparent margin


def test_dock_icon_is_the_padded_one_on_macos_only(app, monkeypatch):
    from mirrorvoice.ui import icons

    monkeypatch.setattr(icons, "_ICONS", {})
    monkeypatch.setattr(icons, "IS_MAC", True)
    mac = icons.app_icon().pixmap(256, 256).toImage()
    monkeypatch.setattr(icons, "_ICONS", {})
    monkeypatch.setattr(icons, "IS_MAC", False)
    win = icons.app_icon().pixmap(256, 256).toImage()
    assert mac.pixelColor(128, 2).alpha() == 0  # margin
    assert win.pixelColor(128, 2).alpha() == 255  # Windows keeps the full-bleed tile


def test_the_artwork_and_its_small_drawing_share_the_tile_and_the_menu_bar_mask_is_only_a_shape(app):
    """The 16–32 px entries of the .icns / .ico come from icon-small.png (packaging/mac/make_icon.py):
    the glass and thin strokes of the full artwork blur when scaled that far."""
    from mirrorvoice.ui.icons import assets_dir

    for name in ("icon.png", "icon-small.png"):
        img = QtGui.QImage(str(assets_dir() / name))
        assert (img.width(), img.height()) == (1024, 1024), name
        assert img.pixelColor(6, 6).alpha() == 0 and img.pixelColor(512, 6).alpha() == 255, name  # the rounded tile, full-bleed
    mask = QtGui.QImage(str(assets_dir() / "menubar-template.png"))
    assert mask.width() == mask.height() == 64 and mask.pixelColor(1, 1).alpha() == 0
    opaque = sum(mask.pixelColor(x, y).alpha() > 128 for x in range(64) for y in range(64))
    assert 0.15 < opaque / 64 ** 2 < 0.6  # two bubbles on nothing: macOS tints it for a light or a dark bar
