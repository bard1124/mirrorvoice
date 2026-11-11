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


def _window(main_window, settings, monkeypatch):
    monkeypatch.setattr(main_window, "list_devices", lambda kind: ["BlackHole 2ch", "BlackHole 16ch", "外置耳机"])
    monkeypatch.setattr(main_window, "default_output_name", lambda: "外置耳机")
    monkeypatch.setattr(main_window, "IS_MAC", False)
    return main_window.MainWindow(settings)


def test_floating_windows_stay_shown_when_the_app_is_in_the_background(app, settings, no_popups, monkeypatch):
    """macOS hid them whenever 镜声 was not frontmost: in the meeting, and when opened from the menu-bar icon."""
    from PySide6.QtCore import Qt

    from mirrorvoice.ui import main_window

    w = _window(main_window, settings, monkeypatch)
    for f in (w.convo, w.overlay):
        assert f.testAttribute(Qt.WidgetAttribute.WA_MacAlwaysShowToolWindow)
    w.tray.act_convo.trigger()
    assert w.convo.isVisible() and w.tray.act_convo.isChecked() and settings.convo_open
    w.tray.act_convo.trigger()
    assert not w.convo.isVisible() and not w.tray.act_convo.isChecked() and not settings.convo_open
    w.engine.stop()
    w.close()


def test_style_dialog_does_not_block_the_floating_windows(app, settings, no_popups, monkeypatch):
    """A modal 字幕样式 window blocked dragging / closing the bar and the conversation window."""
    from mirrorvoice.ui import main_window

    w = _window(main_window, settings, monkeypatch)
    w._style()
    d = w._style_dlg
    assert d is not None and d.isVisible() and not d.isModal()
    assert QtWidgets.QApplication.activeModalWidget() is None
    w._style()  # a second click brings the same window forward
    assert w._style_dlg is d
    d.convo_btn.click()
    assert w.convo.isVisible() and d.convo_btn.text() == "收起对话悬浮窗"
    w.convo.hide()  # its own ✕ while the style window is open: the button follows
    assert d.convo_btn.text() == "弹出对话悬浮窗" and not w.tray.act_convo.isChecked()
    w.overlay.hide()  # the bar's own ✕
    assert d.bar_btn.text() == "显示字幕条（常驻屏幕）"
    d.accept()
    assert w._style_dlg is None and not w.overlay.isVisible()
    w.engine.stop()
    w.close()


def test_online_model_dialog_saves_key_outside_settings_after_consent(app, settings, no_popups, monkeypatch):
    from mirrorvoice import keystore
    from mirrorvoice.config import SETTINGS_FILE
    from mirrorvoice.ui import online

    asked = []
    monkeypatch.setattr(online.QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.StandardButton.Yes)
    d = online.OnlineModelDialog(settings)
    d.rb_online.setChecked(True)
    d.provider.setCurrentIndex(d.provider.findData("dashscope"))
    d.key.setText("sk-ui-0123456789")
    d.key.textEdited.emit("sk-ui-0123456789")
    d.their.setChecked(True)
    d._save()
    assert d.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert asked and "对方说的话" in asked[0] and "通义" in asked[0]
    assert settings.llm_mode == "online" and settings.llm_provider == "dashscope" and settings.llm_model == "qwen-plus"
    assert settings.online_consent and settings.online_their
    assert "sk-ui-0123456789" not in SETTINGS_FILE.read_text(encoding="utf-8")
    keystore.forget_cache()
    assert keystore.get("dashscope") == "sk-ui-0123456789"
    assert online.status_text(settings) == "翻译：在线 · 通义千问 · qwen-plus"
    d2 = online.OnlineModelDialog(settings)  # reopening: no second confirmation, key shown masked
    assert "sk-…6789" in d2.key_hint.text() and d2.key.text() == ""
    d2._save()
    assert len(asked) == 1


def test_online_settings_are_hidden_while_local_is_chosen(app, settings):
    from mirrorvoice.ui import online

    settings.llm_mode = "local"
    d = online.OnlineModelDialog(settings)
    assert d.rb_local.isChecked() and d.box.isHidden()
    d.rb_online.setChecked(True)
    assert not d.box.isHidden()
    d.rb_local.setChecked(True)
    assert d.box.isHidden()


def test_online_dialog_explains_hosts_without_a_model_list(app, settings):
    from mirrorvoice.online_llm import TranslateError
    from mirrorvoice.ui import online

    d = online.OnlineModelDialog(settings)
    d._done("models", TranslateError("model", "模型不存在或地址不对（404）"))
    assert "不提供模型列表" in d.test_out.text()
    d.provider.setCurrentIndex(d.provider.findData("doubao"))
    assert d.model.currentText() == "doubao-seed-2-0-mini-260428"
    d.model.setEditText("ep-20260101-abcde")
    assert "接入点" in d.model_hint.text()


def test_online_dialog_refuses_to_save_without_a_key(app, settings, no_popups):
    from mirrorvoice import keystore
    from mirrorvoice.ui import online

    keystore.save("openai", "")
    d = online.OnlineModelDialog(settings)
    d.rb_online.setChecked(True)
    d.provider.setCurrentIndex(d.provider.findData("openai"))
    d._save()
    assert d.result() != QtWidgets.QDialog.DialogCode.Accepted and settings.llm_mode == "local"
    assert no_popups and "API key" in no_popups[-1][1][1]


def test_question_without_buttons_asks_yes_or_no(app, monkeypatch):
    """The themed QMessageBox.question showed a single 好的 that never counted as 是 (consent, 命令行风格)."""
    from mirrorvoice.ui import theme

    seen = []

    def fake_exec(self):
        btns = [b for b in self.findChildren(QtWidgets.QPushButton)]
        seen.append([b.text() for b in btns])
        btns[0].click()
    monkeypatch.setattr(theme.MessageDialog, "exec", fake_exec)
    assert QtWidgets.QMessageBox.question(None, "t", "x") == QtWidgets.QMessageBox.StandardButton.Yes
    assert seen[-1] == ["是", "否"]
    QtWidgets.QMessageBox.information(None, "t", "x")
    assert seen[-1] == ["好的"]


def test_saving_online_settings_closes_and_confirms(app, settings, monkeypatch):
    from mirrorvoice.ui import online

    monkeypatch.setattr(online.QMessageBox, "question", lambda *a, **k: QtWidgets.QMessageBox.StandardButton.Yes)
    host = QtWidgets.QWidget()
    host.resize(600, 400)
    d = online.OnlineModelDialog(settings, host)
    d.rb_online.setChecked(True)
    d.provider.setCurrentIndex(d.provider.findData("custom"))
    d.url.setText("127.0.0.1:8765")
    d.model.setEditText("local-qwen")
    d._save()
    assert d.result() == QtWidgets.QDialog.DialogCode.Accepted
    toasts = host.findChildren(QtWidgets.QLabel, "toast")
    assert toasts and "已保存" in toasts[0].text() and "在线" in toasts[0].text()


def test_online_recognition_tab_asks_before_uploading_audio(app, settings, monkeypatch):
    from mirrorvoice import keystore
    from mirrorvoice.ui import online

    asked = []
    monkeypatch.setattr(online.QMessageBox, "question",
                        lambda *a, **k: asked.append(a[2]) or QtWidgets.QMessageBox.StandardButton.Yes)
    d = online.OnlineModelDialog(settings)
    d.asr_online.setChecked(True)
    d.asr_me.setChecked(False)
    d.asr_key.setText("sk-asr-123456789")
    d.asr_key.textEdited.emit("sk-asr-123456789")
    d._save()
    assert d.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert asked and "对方的声音" in asked[-1] and "你的声音" not in asked[-1] and "上传" in asked[-1]
    assert settings.asr_mode == "online" and settings.asr_them and not settings.asr_me and settings.asr_consent
    keystore.forget_cache()
    assert keystore.get("dashscope") == "sk-asr-123456789"
    assert "识别：在线（对方）" in online.asr_status_text(settings)


def test_online_recognition_needs_a_side_and_a_key(app, settings, no_popups):
    from mirrorvoice import keystore
    from mirrorvoice.ui import online

    keystore.save("dashscope", "")
    d = online.OnlineModelDialog(settings)
    d.asr_online.setChecked(True)
    d._save()
    assert d.result() != QtWidgets.QDialog.DialogCode.Accepted and "API key" in no_popups[-1][1][1]
    d.asr_me.setChecked(False)
    d.asr_them.setChecked(False)
    d._save()
    assert "至少选一方" in no_popups[-1][1][1]


def test_local_recognizer_choice_is_saved(app, settings, monkeypatch):
    from mirrorvoice.ui import online

    monkeypatch.setattr(online, "IS_MAC", True)
    d = online.OnlineModelDialog(settings)
    d.local_model.setCurrentIndex(d.local_model.findData("qwen3"))
    d._save()
    assert settings.asr_local == "qwen3" and "Qwen3-ASR" in online.asr_status_text(settings)
    monkeypatch.setattr(online, "IS_MAC", False)
    d = online.OnlineModelDialog(settings)
    assert d.local_row.isHidden()
    d._save()
    assert settings.asr_local == "sensevoice"  # Windows: SenseVoice only


def test_device_change_while_running_switches_live(app, settings, no_popups, monkeypatch):
    import time

    from mirrorvoice.ui import main_window

    w = _window(main_window, settings, monkeypatch)
    calls = []
    monkeypatch.setattr(w.engine, "set_call_out", lambda name: calls.append(name) or name)
    w.engine.running = True
    try:
        w.call.setCurrentIndex(w.call.findData("BlackHole 16ch"))
        deadline = time.monotonic() + 2
        while not calls and time.monotonic() < deadline:
            time.sleep(0.02)
        assert calls == ["BlackHole 16ch"]
        assert "再“开始”后生效" not in w.status.text()  # no "stop and start again" for a device any more
    finally:
        w.engine.running = False
        w.close()


def test_feature_change_while_running_applies_live(app, settings, no_popups, monkeypatch):
    from mirrorvoice.ui import main_window

    w = _window(main_window, settings, monkeypatch)
    got = []
    monkeypatch.setattr(w.engine, "apply", lambda changed: got.append(changed))
    w.engine.running = True
    try:
        w.speak.setChecked(not w.speak.isChecked())
        assert got and "speak" in got[-1]
        assert "再“开始”后生效" not in w.status.text()
    finally:
        w.engine.running = False
        w.close()


def test_their_live_translation_row_is_replaced_by_the_final_one(app, settings, no_popups, monkeypatch):
    from mirrorvoice.ui import main_window

    w = _window(main_window, settings, monkeypatch)
    try:
        n = w.log.count()
        w._on_event("partial", {"who": "them", "src": "Hello all", "tgt": "大家 …"})
        w._on_event("me", {"src": "你好", "tgt": "Hi", "passthrough": False, "latency": 1.0, "asr_s": 0.1,
                           "queue_s": 0.0, "translate_s": 0.4, "voice_s": 0.5})
        w._on_event("partial", {"who": "them", "src": "Hello all", "tgt": "大家好 …"})
        assert w.log.count() == n + 2 and w.log.item(n).text().endswith("大家好 …")
        w._on_event("them", {"src": "Hello all", "tgt": "大家好", "latency": 1.0, "asr_s": 0.1, "queue_s": 0.0,
                             "translate_s": 0.5})
        assert w.log.count() == n + 2 and "→ 大家好   (" in w.log.item(n).text()
    finally:
        w.close()


def test_owl_puts_headphones_on_while_running_and_sleeps_when_stopped(app, settings, no_popups, monkeypatch):
    import time

    from mirrorvoice.ui import main_window
    from mirrorvoice.ui.icons import assets_dir

    for n in ("body", "lids", "headset"):
        assert (assets_dir() / "mascot" / f"{n}.png").exists()
    w = _window(main_window, settings, monkeypatch)
    m = w.mascot
    try:
        assert m.state == "stopped" and m.asleep() and m._headset_pos() == 0
        assert all(not px.isNull() for px in m.layers.values())
        w._status_state("busy")  # starting: awake, headphones not on yet
        assert not m.asleep() and m._on_to == 0
        w._status_state("running")
        time.sleep(0.7)
        assert abs(m._headset_pos() - 1.0) < 1e-6
        w.engine.running = True
        w._status_state("busy")  # a notice while translating keeps them on
        assert m.state == "running"
        w.engine.running = False
        w._status_state("stopped")
        time.sleep(0.5)
        assert m.asleep() and m._headset_pos() == 0
        img = m.grab().toImage()
        assert img.width() == 96 and not img.isNull()
    finally:
        w.close()
