"""The control panel and dialogs build and behave without audio hardware (offscreen Qt)."""
import pytest

QtWidgets = pytest.importorskip("PySide6.QtWidgets")


@pytest.fixture(scope="module")
def app():
    from mirrorvoice.ui import theme

    a = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    theme.apply(a)
    return a


@pytest.fixture
def settings(home):
    from mirrorvoice.config import Settings

    # macOS device names on every OS: set the routing explicitly (Windows defaults to CABLE / loopback)
    s = Settings(auto_check_update=False, setup_done=True, call_out="BlackHole 2ch", their_source="BlackHole 16ch",
                 monitor="外置耳机")
    s.save()
    return s


@pytest.fixture
def no_popups(monkeypatch):
    shown = []
    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QtWidgets.QMessageBox, kind, lambda *a, kind=kind, **k: shown.append((kind, a[1:3])))
    monkeypatch.setattr(QtWidgets.QMessageBox, "question",
                        lambda *a, **k: QtWidgets.QMessageBox.StandardButton.No)
    return shown


def test_main_window_builds(app, settings, no_popups, monkeypatch):
    from mirrorvoice.ui import main_window

    monkeypatch.setattr(main_window, "list_devices", lambda kind: (
        ["外置麦克风", "BlackHole 2ch", "BlackHole 16ch"] if kind == "input"
        else ["外置耳机", "BlackHole 2ch", "BlackHole 16ch", "Teams 扬声器 (耳机+字幕)"]))
    monkeypatch.setattr(main_window, "default_output_name", lambda: "外置耳机")
    monkeypatch.setattr(main_window, "IS_MAC", False)  # skip the multi-output helper here
    w = main_window.MainWindow(settings)
    assert [w.quality.itemData(i) for i in range(w.quality.count())] == ["turbo", "fast", "quality"]
    assert w.call.itemText(0) == "BlackHole 2ch（推荐）"
    assert not w.dev_warn.isVisible()
    i = w.call.findData("Teams 扬声器 (耳机+字幕)")
    w.call.setCurrentIndex(i)  # a user picking the echo setup
    assert w.dev_warn.property("level") == "conflict" and "送进会议" in w.dev_warn.text()
    assert no_popups and no_popups[-1][0] == "warning"
    assert settings.call_out == "Teams 扬声器 (耳机+字幕)"  # still allowed, just explained
    w._on_event("me", dict(src="你好", tgt="Hello", latency=1.2, asr_s=0.2, queue_s=0.0, translate_s=0.6,
                           voice_s=0.4, passthrough=False))
    assert "翻译 0.6s" in w.log.item(w.log.count() - 1).text()
    assert "平均延迟" in w.lat_label.text()
    w.engine.stop()
    w.close()


def test_glossary_dialog_saves_terms_and_background(app, settings):
    from mirrorvoice.translate import load_glossary
    from mirrorvoice.ui.dialogs import GlossaryDialog

    d = GlossaryDialog(settings)
    d.meeting.setText("支付系统评审")
    d.table.item(d.table.rowCount() - 1, 0).setText("OneForma")
    d.table.item(d.table.rowCount() - 1, 1).setText("OneForma")
    d.table.item(d.table.rowCount() - 1, 2).setText("万佛马")
    d._save()
    assert ("OneForma", "OneForma", "万佛马") in load_glossary()
    from mirrorvoice.config import Settings

    assert Settings.load().meeting_context == "支付系统评审"


def test_checkup_dialogs_build(app, settings):
    from mirrorvoice.ui.checkup import MeetingGuideDialog, SelfTestDialog
    from mirrorvoice.selftest import Check

    d = SelfTestDialog(settings)
    d._show([Check("call", "送进会议", True, "ok", 0.05), Check("them", "对方声音", False, "bad")])
    assert d.now.text().startswith("❌")
    g = MeetingGuideDialog()
    assert g.findChild(QtWidgets.QTabWidget).count() == 4
