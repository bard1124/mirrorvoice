"""The sidebar girl wears each theme's set: black hair for 静场 and 赛博, silver for 调音台, lilac for
对话 — six poses each, on one canvas per set so a pose change neither jumps nor rescales her."""
import pytest
from PySide6 import QtWidgets


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def test_every_theme_has_a_full_set_on_one_canvas(app):
    from mirrorvoice.ui import mascot, theme
    from mirrorvoice.ui.icons import assets_dir

    assert set(mascot.OUTFITS) == set(theme.THEMES)
    for outfit in set(mascot.OUTFITS.values()):
        from PySide6.QtGui import QPixmap

        sizes = {QPixmap(str(assets_dir() / "mascot" / outfit / f"{p}.png")).size().toTuple() for p in mascot.POSES}
        assert len(sizes) == 1 and min(sizes.pop()) > 200, outfit


def test_she_changes_with_the_theme(app):
    from mirrorvoice.ui import mascot, theme

    try:
        theme.use(app, "quiet")
        m = mascot.Mascot(100, 120)
        assert m.outfit == "quiet" and not any(pm.isNull() for pm in m.poses.values())
        for key, outfit in (("studio", "studio"), ("chat", "chat"), ("cyber", "quiet")):
            theme.use(app, key)
            m.dress()
            assert m.outfit == outfit and not m.poses["hello"].isNull()
        m.grab()  # draws without error
    finally:
        theme.use(app, "quiet")
