"""赛博's frosted glass: the window is only see-through when the theme wants it and the system can, the
white veil only goes on once the blur is really there, and 透明度 thins the veil."""
import re

import pytest
from PySide6 import QtWidgets
from PySide6.QtCore import Qt


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _alphas(css: str) -> list[float]:
    found = [float(a) for a in re.findall(r"rgba\(255,255,255,([0-9.]+)\)", css)]
    return [found[i] for i in (0, 3, 4)]  # content, log, card (the sidebar's and top bar's are fixed)


def test_clearer_means_a_thinner_veil():
    from mirrorvoice.ui import glass

    thick, default, clear = _alphas(glass.qss(0)), _alphas(glass.qss()), _alphas(glass.qss(100))
    assert all(a > b > c for a, b, c in zip(thick, default, clear))
    assert clear[0] == 0 and max(thick) <= 0.9  # all the way: nothing over the ground but the panels
    assert glass.ground(0) > glass.ground() > glass.ground(100) >= 0.2  # the ground never goes bare
    assert "rgba(255,255,255,0.300)" in glass.qss(100)  # nor does the sidebar under its words
    assert glass.qss(-5) == glass.qss(0) and glass.qss(150) == glass.qss(100)


def test_only_the_glass_theme_frosts_and_the_veil_waits_for_the_blur(app, monkeypatch):
    from mirrorvoice.ui import glass, theme

    monkeypatch.setattr(glass, "supported", lambda: True)
    try:
        theme.use(app, "quiet")
        plain = QtWidgets.QWidget()
        assert not glass.frost(plain) and not plain.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        theme.use(app, "cyber")
        glass.allow(False)
        assert not glass.frost(QtWidgets.QWidget())  # the user turned it off
        glass.allow(True, 80)
        w = QtWidgets.QWidget()
        assert glass.frost(w) and w.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        monkeypatch.setattr(glass, "_attach", lambda _w: False)  # the system would not: stays opaque white
        assert not glass.refresh(w) and w.styleSheet() == ""
        monkeypatch.setattr(glass, "_attach", lambda _w: True)
        assert glass.refresh(w) and w.styleSheet() == glass.qss(80) and w.property("veil") == glass.ground(80)
        w.resize(40, 30)
        assert w.grab().toImage().pixelColor(5, 5).alpha() > 0  # the window's own white ground is painted

        theme.use(app, "studio")  # another look: the veil comes off again
        assert not glass.refresh(w) and w.styleSheet() == "" and not w.property("veil")
    finally:
        glass.allow(True)
        theme.use(app, "quiet")
