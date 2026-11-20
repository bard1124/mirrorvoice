"""The update window's picture when 虚拟形象 is off: the robot a light runs down to while the update
comes in, waking (every cable lit, its eye on) when it is in — and the app restarts after that."""
import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    from mirrorvoice.ui import theme

    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(a)
    return a


def _robot(still=False):
    from mirrorvoice.ui import mascot

    r = mascot.UpdateRobot()
    t = [100.0]
    r.clock, r._t0, r.still = (lambda: t[0]), 100.0, still
    return r, t


def test_its_frames_are_in_the_app_and_small(app):
    from pathlib import Path

    from mirrorvoice.ui.icons import assets_dir

    r, _ = _robot()
    assert not r.base.isNull() and len(r.layers["loop"]) == 24 and len(r.layers["wake"]) == 12
    empty = [i for i, (pm, _, _) in enumerate(r.layers["loop"]) if pm.isNull()]
    assert empty == [23] and all(not pm.isNull() for pm, _, _ in r.layers["wake"])  # (the base is the last loop frame)
    d = Path(assets_dir()) / "mascot" / "robot"
    assert sum(f.stat().st_size for f in d.iterdir()) < 700_000  # (a patch stays a patch: 2 MB, not 3.4)


def test_it_loops_while_the_update_comes_in_and_wakes_once_when_it_is_in(app):
    from mirrorvoice.ui import mascot

    r, t = _robot()
    assert r.frame() == ("loop", 0)
    t[0] += 0.95
    assert r.frame() == ("loop", 9)
    t[0] += 2.4  # one round later: the same frame
    assert r.frame() == ("loop", 9)
    took = r.finish()
    assert took == pytest.approx(12 * mascot.ROBOT_STEP_MS / 1000 + mascot.ROBOT_HOLD_S)
    assert r.frame() == ("wake", 0)
    t[0] += 0.55
    assert r.frame() == ("wake", 5)
    t[0] += 10
    assert r.frame() == ("wake", 11) and r.finish() == 0.0  # held on the last frame, nothing more to wait for


def test_with_less_motion_asked_for_it_shows_one_frame_of_each(app):
    r, t = _robot(still=True)
    first = r.frame()
    t[0] += 1.3
    assert r.frame() == first
    r.finish()
    assert r.frame() == ("wake", 11)


def test_it_paints_head_and_cables_and_fades_out_at_the_bottom(app):
    r, t = _robot()
    t[0] += 0.95
    img = r.grab().toImage()
    w, h = img.width(), img.height()
    top = [img.pixelColor(x, 2) for x in range(w)]
    assert any(c.alpha() > 200 for c in top)  # the cables run in from the top edge
    assert all(img.pixelColor(x, h - 1).alpha() < 20 for x in range(w))  # gone at the bottom edge
    mid = [img.pixelColor(x, h // 2) for x in range(w)]
    assert sum(c.alpha() > 200 for c in mid) > w // 3  # the head, in the middle
    assert all(img.pixelColor(w - 1, y).alpha() < 20 for y in range(h))  # no hard edge on the right either
    shoulder = [img.pixelColor(x, int(h * 0.62)) for x in range(int(w * 0.55), int(w * 0.8))]
    assert sum(c.alpha() > 200 for c in shoulder) > len(shoulder) // 2  # the right shoulder is there


def test_the_window_shows_her_or_the_robot_by_the_switch_and_waits_for_either(app):
    from mirrorvoice.ui import update_ui

    off = update_ui.UpdateProgress(None, "v0.8.38 → v0.8.39", False)
    on = update_ui.UpdateProgress(None, "v0.8.38 → v0.8.39", True)
    assert off.mascot.isHidden() and not off.robot.isHidden() and off.picture() is off.robot
    assert on.robot.isHidden() and not on.mascot.isHidden() and on.picture() is on.mascot
    assert off.bar.isHidden() and not on.bar.isHidden()  # (the robot's cables are its bar)
    assert off.robot.progress == 0.0 and on.robot.progress is None
    assert off.finish() > 1.0 and off.robot.frame()[0] == "wake"


def test_the_restart_waits_for_the_robot_to_wake(app, monkeypatch, tmp_path):
    from mirrorvoice import updater
    from mirrorvoice.config import Settings
    from mirrorvoice.ui import mascot, update_ui

    timers = []
    monkeypatch.setattr(updater, "install", lambda path, rel: None)
    monkeypatch.setattr(update_ui.QTimer, "singleShot", lambda ms, fn: timers.append(ms))
    c = update_ui.UpdateController(None, Settings(mascot=True), lambda: None)
    monkeypatch.setattr(update_ui.threading, "Thread", lambda **kw: type("T", (), {"start": lambda self: None})())
    rel = updater.Release(version="9.9.9", notes="", page="", asset_name="MirrorVoice-9.9.9-patch.zip",
                          asset_url="", size=2 << 20, sha256="", kind="patch")
    c._start(rel)  # with the sidebar girl on, too, the update is the robot's
    assert c._dlg.picture() is c._dlg.robot and c._dlg.mascot.isHidden()
    c._rel, c._path = rel, tmp_path / "MirrorVoice-9.9.9-patch.zip"
    c._on_progress(1 << 20, 2 << 20)
    assert c._dlg.head.text() == update_ui.SAY_LINK
    assert "1.0 MB / 2.0 MB" in c._dlg.label.text() and "9.9.9" in c._dlg.label.text()
    c._on_progress(-1, 0)
    assert c._dlg.head.text() == "v9.9.9 已上线" and c._dlg.label.text() == update_ui.SAY_WAKE
    for words in ("正在下载", "正在安装", "取消"):  # (the plain words it no longer says)
        assert words not in c._dlg.head.text() + c._dlg.label.text() + c._dlg.cancel.text()
    wake = 12 * mascot.ROBOT_STEP_MS + 1000 * mascot.ROBOT_HOLD_S
    assert len(timers) == 1 and timers[0] >= wake  # the app restarts once it has woken


def _blue(img, box):
    """How much bluer than red the drawn pixels in box (x0, y0, x1, y1) are, summed."""
    x0, y0, x1, y1 = box
    return sum(max(0, c.blue() - c.red()) * c.alpha() / 255
               for x in range(x0, x1) for y in range(y0, y1) for c in [img.pixelColor(x, y)])


def test_its_own_cables_are_the_progress_lit_from_the_top_and_the_eye_last(app):
    from mirrorvoice.ui import mascot, update_ui

    d = update_ui.UpdateProgress(None, "…", False)
    r = d.robot
    r.still = True  # (one picture per state: no band running down)
    t = [100.0]
    r.clock = lambda: t[0]
    w = r.width()
    vx, vy, vw = mascot.ROBOT_VIEW
    s = w / vw
    top = (0, 0, w, 24)                                    # the cables at the top edge
    neck = (int(110 * s), int((300 - vy) * s), int(160 * s), int((330 - vy) * s))  # where they reach the neck
    ex, ey = mascot.UpdateRobot.ROBOT_EYE
    eye = (int((ex - 6) * s), int((ey - vy - 6) * s), int((ex + 6) * s), int((ey - vy + 6) * s))

    def at(v):
        d.bar.setValue(v)
        return r.grab().toImage()

    none, half, full = at(0), at(450), at(1000)
    assert _blue(none, top) < _blue(half, top) / 4             # grey until the update comes in, lit from the top
    assert _blue(half, neck) < _blue(full, neck) / 2            # not down to the neck before it is all there
    assert _blue(full, eye) < 50                                # the eye stays dark …
    r.finish()
    t[0] += 5
    woke = r.grab().toImage()
    assert _blue(woke, eye) > 4 * max(1.0, _blue(full, eye))    # … until it is in
    assert _blue(woke, neck) >= _blue(full, neck) * 0.9         # (and the cables stay lit meanwhile)
    d.dismiss()


def test_a_band_of_light_runs_down_the_lit_part(app):
    from mirrorvoice.ui import update_ui

    d = update_ui.UpdateProgress(None, "…", False)
    r = d.robot
    r.still = False
    t = [100.0]
    r.clock, r._t0 = (lambda: t[0]), 100.0
    d.bar.setValue(1000)
    shots = []
    for dt in (0.1, 0.5, 0.9):
        t[0] = 100.0 + dt
        shots.append(r.grab().toImage())
    assert shots[0] != shots[1] != shots[2]  # it moves
    d.dismiss()
