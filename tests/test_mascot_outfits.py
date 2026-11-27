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


def test_cyber_has_code_rain_instead_of_her_and_it_shows_the_state(app):
    import time

    from mirrorvoice.ui import coderain, mascot, theme

    try:
        theme.use(app, "quiet")
        m = mascot.Mascot(100, 120)
        assert m.rain is None
        theme.use(app, "cyber")
        m.dress()
        assert m.rain is not None and m.rain_mode() == "asleep"
        m.set_state("busy")
        assert m.rain_mode() == "waking"
        m.set_state("running")
        assert m.rain_mode() == "cheer"  # the burst when translation starts
        m._cheer_until = 0
        assert m.rain_mode() == "idle"
        m.set_level(0.2, "them")
        assert m.rain_mode() == "them"
        m._voice["them"] = 0
        m.set_level(0.2, "me")
        assert m.rain_mode() == "me"
        m.set_muted(True)
        assert m.rain_mode() == "muted"
        m.grab()  # draws without error
        theme.use(app, "studio")
        m.dress()
        assert m.rain is None  # the other looks keep her
    finally:
        theme.use(app, "quiet")


def test_the_rain_falls_bursts_and_stands_still_when_asked(app):
    from mirrorvoice.ui import coderain

    r = coderain.CodeRain(rows=10, seed=1)
    r.set_mode("idle")
    for c in r.cols:
        c.head = 0.0
    r.step(0.2)
    assert all(c.head > 0 for c in r.cols)  # every column moves down
    r.set_mode("cheer")
    assert all(c.falling and c.head <= 0 for c in r.cols)  # all of them, from the top at once
    r.set_mode("muted")
    frozen = [c.head for c in r.cols]
    r.step(0.5)
    assert [c.head for c in r.cols] == frozen
    still = coderain.CodeRain(rows=10, still=True, seed=1)  # the system asks for less motion: a still screen
    heads = [c.head for c in still.cols]
    still.set_mode("cheer")
    still.step(1.0)
    assert [c.head for c in still.cols] == heads and any(0 < h for h in heads)


def test_a_stopped_app_still_shows_a_screen_of_rain(app):
    """2026-11-27: stopped, the rain was a few faint glyphs: an empty corner on the glass."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from mirrorvoice.ui import coderain

    r = coderain.CodeRain(rows=10, seed=3)
    for c in r.cols:
        c.falling = False  # nothing falling at all: the still screen alone
    img = QImage(100, 120, QImage.Format.Format_ARGB32)
    img.fill(0)
    p = QPainter(img)
    r.paint(p, QRectF(0, 0, 100, 120))
    p.end()
    drawn = sum(1 for x in range(0, 100, 2) for y in range(0, 120, 2) if img.pixelColor(x, y).alpha())
    assert coderain.FIELD["asleep"] > 0 and drawn > 100  # glyphs across the whole screen
    r.set_mode("idle")
    assert coderain.FIELD.get("idle", 0) == 0  # running: only the rain
