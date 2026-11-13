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
    """macOS hid them whenever 翻了么 was not frontmost: in the meeting, and when opened from the menu-bar icon."""
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


def test_owl_girl_dozes_when_stopped_cheers_on_start_and_reacts_to_voices(app, settings, no_popups, monkeypatch):
    import time

    from mirrorvoice.ui import main_window, mascot
    from mirrorvoice.ui.icons import assets_dir

    for n in mascot.POSES:
        assert (assets_dir() / "mascot" / f"{n}.png").exists()
    w = _window(main_window, settings, monkeypatch)
    m = w.mascot
    try:
        assert all(not px.isNull() for px in m.poses.values())
        assert m.state == "stopped" and m.pose() == "sleepy"
        w._status_state("busy")
        assert m.pose() == "smile"  # waking up
        w._status_state("running")
        assert m.pose() == "cheer"
        m._cheer_until = 0
        m._blink_at = time.monotonic() + 60
        assert m.pose() == "hello"
        w._on_event("level", {"who": "them", "rms": 0.1})
        assert m.pose() == "laptop"
        m._voice["them"] = 0
        w._on_event("level", {"who": "me", "rms": 0.1})
        assert m.pose() == "wave"
        w.engine.running = True
        w._status_state("busy")  # a notice while translating: still running
        assert m.state == "running"
        w.engine.running = False
        w._status_state("stopped")
        assert m.pose() == "sleepy"
        img = m.grab().toImage()
        assert (img.width(), img.height()) == (100, 120)
    finally:
        w.close()


def test_clicking_check_for_updates_twice_asks_once(app, settings, no_popups, monkeypatch):
    from mirrorvoice import __version__, updater
    from mirrorvoice.ui import update_ui

    calls = []
    monkeypatch.setattr(updater, "check_async", lambda cb: calls.append(cb))
    c = update_ui.UpdateController(None, settings, lambda: None)
    try:
        c.check(manual=False)
        assert c._win is None  # the check at start-up shows nothing
        c.check(manual=True)
        c.check(manual=True)
        assert len(calls) == 1  # one check on its way
        c._on_checked(None, "")
        assert c._win.state == "newest" and __version__ in c._win.head.text()  # the manual click is honoured
        c.check(manual=True)
        assert len(calls) == 2  # a new click after the answer checks again
    finally:
        c._win.close()


def _release(version="99.0.0", kind="patch"):
    from mirrorvoice import updater

    return updater.Release(version=version, notes="- faster", page="https://example.com/r", asset_name="p.zip",
                           asset_url="https://example.com/p.zip", size=2 << 20, sha256="", kind=kind, full_size=230 << 20)


def test_the_update_window_opens_at_the_click_and_the_answer_arrives_in_it(app, settings, no_popups, monkeypatch):
    """Nothing showed for the seconds GitHub took: the click looked dead."""
    from mirrorvoice import updater
    from mirrorvoice.ui import main_window

    calls = []
    monkeypatch.setattr(updater, "check_async", lambda cb: calls.append(cb))
    monkeypatch.setattr(updater, "can_self_install", lambda: True)
    w = _window(main_window, settings, monkeypatch)
    try:
        u = w.updates
        w.nav["_check_updates"].click()
        win = u._win
        assert win.isVisible() and win.state == "checking" and len(calls) == 1  # before GitHub has answered
        assert win.spinner.isVisible() and "正在检查更新" in win.head.text()
        assert not win.now.isVisible() and not win.notes.isVisible() and win.later.text() == "取消"
        small = win.height()

        u._on_checked(None, "timed out")  # no network: said in the window, with a way to try again
        assert win.state == "error" and "timed out" in win.head.text() and not win.spinner.isVisible()
        win.again.click()
        assert win.state == "checking" and len(calls) == 2

        u._on_checked(_release(), "")  # the same window turns into the offer
        assert win.isVisible() and win.state == "release" and "99.0.0" in win.head.text()
        assert win.now.isVisible() and win.skip.isVisible() and win.notes.isVisible() and not win.spinner.isVisible()
        assert "补丁更新" in win.detail.text() and "faster" in win.notes.toPlainText() and win.height() > small
        started = []
        monkeypatch.setattr(u, "_start", started.append)
        win.now.click()
        assert not win.isVisible() and started[0].version == "99.0.0"
    finally:
        w.close()


def test_an_answer_nobody_waits_for_opens_nothing(app, settings, no_popups, monkeypatch):
    from mirrorvoice import updater
    from mirrorvoice.ui import update_ui

    monkeypatch.setattr(updater, "check_async", lambda cb: None)
    c = update_ui.UpdateController(None, settings, lambda: None)
    c.check(manual=True)
    c._win.later.click()  # 取消 while it is still asking
    c._on_checked(_release(), "")
    assert not c._win.isVisible() and c._win.state == "checking"

    c.check(manual=False)  # at start-up: a new version opens the window by itself …
    c._on_checked(_release(), "")
    assert c._win.isVisible() and c._win.state == "release"
    c._win.skip.click()  # … unless it was skipped
    assert not c._win.isVisible() and settings.skip_version == "99.0.0"
    c.check(manual=False)
    c._on_checked(_release(), "")
    assert not c._win.isVisible()
    c.check(manual=False)  # nothing new, or offline: silence
    c._on_checked(None, "offline")
    assert not c._win.isVisible()
    c.check(manual=True)  # a click still shows the skipped version
    c._on_checked(_release(), "")
    assert c._win.isVisible() and c._win.state == "release"
    c._win.close()


def test_update_routine_floats_up_curls_and_plugs_in_then_lands_when_the_update_is_in(app):
    from mirrorvoice.ui import mascot
    from mirrorvoice.ui.icons import assets_dir

    for n in mascot.UPDATE_POSES:
        assert (assets_dir() / "mascot" / "update" / f"{n}.png").exists()
    now = [100.0]
    m = mascot.UpdateMascot()
    assert all(not px.isNull() for px in m.frames.values())
    m.clock = lambda: now[0]
    m._t0 = 100.0
    intro = [0.0]
    for _, d in mascot.INTRO:
        intro.append(intro[-1] + d)
    seen = []
    for t in [0.0, intro[1] - 0.1, intro[1] + 0.1, intro[2] + 0.1, intro[3] + 0.1, intro[3] + 5, 60.0]:
        pose, _, _, hover = m.at(100.0 + t)
        seen.append(pose)
        if t == 0.0:
            assert hover == 0.0  # still on the ground
        if t >= intro[1]:
            assert hover == 1.0  # up in the air
    assert seen == ["lift", "lift", "cross", "curl", "link", "link", "link"]  # plugged in for as long as it takes
    now[0] = 101.0  # a quick download: she still curls up and plugs in first
    outro = sum(d for _, d in mascot.OUTRO)
    wait = m.finish()
    assert wait == pytest.approx(intro[3] + mascot.LINK_MIN_S + outro - 1.0)
    assert m.finish() == pytest.approx(wait)  # asking again does not move the landing
    end = m.finish_time()
    rise, land = (d for _, d in mascot.OUTRO)
    assert m.at(end - outro - 0.01)[0] == "link" and m.at(end - land - 0.1)[0] == "rise"
    assert m.at(end - 0.1)[0] == "land" and m.at(end - land / 2)[3] < 1.0
    assert m.at(end + 5)[:2] == ("land", "rise") and m.at(end + 5)[3] == 0.0  # back on the ground, eyes open
    assert not m.landed()
    now[0] = end
    assert m.landed()
    for t in (0.3, intro[1] + 0.2, intro[3] + 2.0, end - 100.0 - land + 0.2):  # lifting, blending, plugged in, landing
        now[0] = 100.0 + t
        img = m.grab().toImage()
        assert (img.width(), img.height()) == (132, 170)


def test_update_frames_have_their_faces_where_the_blending_expects_them():
    np = pytest.importorskip("numpy")
    image = pytest.importorskip("PIL.Image")
    from mirrorvoice.ui import mascot
    from mirrorvoice.ui.icons import assets_dir

    for n, (fx, fy) in mascot.FACES.items():
        a = np.asarray(image.open(assets_dir() / "mascot" / "update" / f"{n}.png").convert("RGBA")).astype(float)
        r, g, b, al = (a[..., i] for i in range(4))
        ys = np.mgrid[0:a.shape[0], 0:a.shape[1]][0]
        top, bottom = np.where(al > 20)[0][[0, -1]]
        skin = (al > 230) & (r > 215) & (g > 170) & (g < 225) & (b > 140) & (b < 205) & (r - b > 25) \
            & (ys < top + 0.6 * (bottom - top))
        y, x = np.where(skin)
        assert abs(x.mean() / a.shape[1] - fx) < 0.01 and abs(y.mean() / a.shape[0] - fy) < 0.01, n


def test_update_progress_installs_then_lets_her_land_before_quitting(app, settings, no_popups, monkeypatch, tmp_path):
    from mirrorvoice import updater
    from mirrorvoice.ui import update_ui

    installed, timers, before = [], [], []
    monkeypatch.setattr(updater, "install", lambda path, rel: installed.append((path, rel)))
    monkeypatch.setattr(update_ui.QTimer, "singleShot", lambda ms, fn: timers.append(ms))
    c = update_ui.UpdateController(None, settings, lambda: before.append(1))
    rel = updater.Release(version="9.9.9", notes="", page="", asset_name="MirrorVoice-9.9.9-patch.zip",
                          asset_url="", size=2 << 20, sha256="", kind="patch")
    c._dlg = update_ui.UpdateProgress(None, "正在下载…")
    c._rel, c._path = rel, tmp_path / "MirrorVoice-9.9.9-patch.zip"
    c._on_progress(1 << 20, 2 << 20)
    assert c._dlg.bar.value() == 500
    c._on_progress(-1, 0)
    assert before == [1] and installed == [(c._path, rel)]
    from mirrorvoice.ui import mascot

    least = sum(d for _, d in mascot.INTRO) + mascot.LINK_MIN_S + sum(d for _, d in mascot.OUTRO)
    assert len(timers) == 1 and timers[0] >= 1000 * least  # she curls up, plugs in, rises and lands
    c._dlg.reject()
    assert not c._dlg.canceled.is_set()  # installing: no cancelling now

    d = update_ui.UpdateProgress(None, "正在下载…")
    d.show()
    d.reject()  # 取消 while downloading stops the download
    assert d.canceled.is_set() and not d.isVisible()


_QUIT_CHECK = r"""
import sys
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from mirrorvoice.ui import update_ui

app = QApplication([])
dlg = update_ui.UpdateProgress(None, "更新好了，马上重新打开翻了么…")
dlg.show()
dlg.lock()
state = {}
QTimer.singleShot(100, QApplication.quit)  # what v0.8.12 did: the locked window refuses, Qt cancels the quit
QTimer.singleShot(600, lambda: (state.setdefault("alive", True), update_ui.UpdateController._restart(dlg)))
QTimer.singleShot(8000, lambda: QApplication.exit(3))  # stuck
code = app.exec()
print("alive-after-plain-quit" if state.get("alive") else "quit-at-once", code)
sys.exit(code)
"""


def test_restart_after_the_update_really_quits_although_the_progress_window_is_locked(tmp_path):
    import os
    import subprocess
    import sys

    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MIRRORVOICE_HOME=str(tmp_path))
    r = subprocess.run([sys.executable, "-c", _QUIT_CHECK], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr  # 3: still running 8 s later (the v0.8.12 hang)
    assert "alive-after-plain-quit" in r.stdout  # a plain quit() was held up by the locked window


def test_teams_one_click_asks_before_closing_teams_and_reopens_it(app, no_popups, monkeypatch):
    from mirrorvoice import teams_devices as td
    from mirrorvoice.ui import checkup

    log, running, answer = [], [True], [QtWidgets.QMessageBox.StandardButton.No]
    monkeypatch.setattr(td, "supported", lambda: True)
    monkeypatch.setattr(td, "state", lambda: "other")
    monkeypatch.setattr(td, "running", lambda: running[0])
    monkeypatch.setattr(td, "quit", lambda: log.append("quit") or running.__setitem__(0, False) or True)
    monkeypatch.setattr(td, "launch", lambda: log.append("launch"))
    monkeypatch.setattr(td, "prepare", lambda out: log.append("prepare") or "")
    monkeypatch.setattr(td, "apply", lambda: log.append("apply") or "已设置好")
    monkeypatch.setattr(td, "restore", lambda: log.append("restore") or "已还原")
    monkeypatch.setattr(checkup.QMessageBox, "question", lambda *a, **k: answer[0])
    said = []
    monkeypatch.setattr(checkup.QMessageBox, "information", lambda *a, **k: said.append(a[2]))
    monkeypatch.setattr(checkup.QMessageBox, "warning", lambda *a, **k: said.append("!" + a[2]))

    d = checkup.MeetingGuideDialog()
    w = d.findChild(checkup.TeamsOneClick)
    assert w is not None and "不是翻了么的线路" in w.status.text()
    w.set_btn.click()  # Teams is open and I say no: nothing is touched
    assert log == [] and said == []
    answer[0] = QtWidgets.QMessageBox.StandardButton.Yes
    w.set_btn.click()
    assert log == ["quit", "prepare", "apply", "launch"] and "重新打开" in said[-1]
    del log[:]
    w.back_btn.click()  # Teams is closed now: no question, no relaunch
    assert log == ["restore"] and said[-1] == "已还原"

    def broken():
        raise td.TeamsError("格式和预期不一样")
    running[0] = True
    monkeypatch.setattr(td, "apply", broken)
    del log[:]
    w.set_btn.click()  # a failure is said, and Teams comes back all the same
    assert log == ["quit", "prepare", "launch"] and said[-1].startswith("!") and "格式" in said[-1]
    d.close()


def test_the_switch_at_the_bottom_left_shows_and_hides_her_and_is_remembered(app, settings, no_popups, monkeypatch):
    from mirrorvoice.config import Settings
    from mirrorvoice.ui import main_window, update_ui

    assert settings.mascot is False  # off unless asked for
    w = _window(main_window, settings, monkeypatch)
    try:
        w.show()
        sw = w.mascot_switch
        assert not sw.isChecked() and w.mascot.isHidden() and sw.position() == 0.0
        assert sw.mapTo(w, sw.rect().center()).y() > w.nav["_check_updates"].mapTo(w, w.rect().topLeft()).y()
        assert sw.mapTo(w, sw.rect().center()).x() < 176  # in the sidebar, below the last entry
        sw.click()
        assert w.mascot.isVisible() and settings.mascot is True and Settings.load().mascot is True
        import time

        end = time.monotonic() + 10  # the knob glides over (waited for, however busy the machine)
        while sw.position() != 1.0 and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        assert sw.position() == 1.0
        sw.click()
        assert w.mascot.isHidden() and Settings.load().mascot is False
    finally:
        w.close()
    # her update routine follows the switch: without her nothing waits for the landing
    assert update_ui.UpdateProgress(None, "正在下载…", False).mascot.isHidden()
    assert not update_ui.UpdateProgress(None, "正在下载…").mascot.isHidden()
    again = _window(main_window, Settings.load(), monkeypatch)  # the next start finds it as it was left
    assert not again.mascot_switch.isChecked() and again.mascot.isHidden()
    again.close()



def test_three_looks_and_switching_between_them(app, settings, no_popups, monkeypatch):
    from mirrorvoice.config import Settings
    from mirrorvoice.ui import logview, main_window, theme

    assert settings.theme == "quiet" and set(theme.THEMES) == {"quiet", "studio", "chat"}
    for t in theme.THEMES.values():  # every token of the stylesheet has a value in every theme
        css = theme.qss(t)
        assert t.accent in css and t.bg in css and "{" not in css.replace("{ ", "").replace(" {", "").replace("{\n", "")
    w = _window(main_window, settings, monkeypatch)
    try:
        w.resize(1100, 700)
        w.show()
        w._on_event("me", dict(src="大家好。", tgt="Hello everyone.", latency=1.2, asr_s=0.1, queue_s=0.0,
                               translate_s=0.6, voice_s=0.5, passthrough=False))
        w._on_event("them", dict(src="Any blockers?", tgt="有什么阻碍吗？", latency=0.6, asr_s=0.1, queue_s=0.0,
                                 translate_s=0.5))
        w._on_event("notice", dict(text="有一段声音很轻，没有翻译。"))
        me, them, note = (w.log.item(w.log.count() - k) for k in (3, 2, 1))
        assert me.data(logview.ROLE) == logview.entry("me", "大家好。", "Hello everyone.", "1.2s")
        assert "翻译 0.6s" in me.text() and "翻译 0.6s" in me.toolTip()  # the full timing: text and tooltip
        assert them.data(logview.ROLE)["who"] == "them" and note.data(logview.ROLE)["who"] == "note"

        box, d, opt = w.theme_box, w.log.itemDelegate(), QtWidgets.QStyleOptionViewItem()
        heights = {}
        for key in ("studio", "chat", "quiet"):
            box.setCurrentIndex(box.findData(key))
            assert theme.current().key == key and Settings.load().theme == key  # at once, and remembered
            assert theme.current().accent in app.styleSheet()
            heights[key] = d.sizeHint(opt, w.log.indexFromItem(me)).height()
            w.grab()  # every way of drawing a sentence paints without error
        assert heights["chat"] > heights["quiet"]  # a bubble carries its time below it
        assert theme.current().convo == "rows" and theme.STUDIO.convo == "cards" and theme.CHAT.convo == "bubbles"
        assert theme.use(app, "no-such-look").key == "quiet"  # a setting from a newer version: the default
    finally:
        w.close()
        theme.use(app, "quiet")


def test_each_look_has_its_own_layout_made_of_the_same_controls(app, settings, no_popups, monkeypatch):
    """静场: conversation in the middle, settings at the right. 调音台: navigation across the top, the
    time of each stage, my side and theirs in two lists. 对话: languages at the top, start under the
    conversation. Switching moves the very same controls: nothing set or said is lost."""
    from mirrorvoice.ui import main_window, theme

    w = _window(main_window, settings, monkeypatch)
    try:
        w.resize(1280, 800)
        w.show()
        at = lambda x: x.mapTo(w, x.rect().center())  # noqa: E731
        w._on_event("me", dict(src="大家好。", tgt="Hello everyone.", latency=1.2, asr_s=0.1, queue_s=0.0,
                               translate_s=0.6, voice_s=0.5, passthrough=False))
        w._on_event("partial", dict(who="them", src="Any block", tgt="有什么 …", live=True))
        w._on_event("them", dict(src="Any blockers?", tgt="有什么阻碍吗？", latency=0.6, asr_s=0.1, queue_s=0.0,
                                 translate_s=0.5))
        assert (w.log.count(), w.log_me.count(), w.log_them.count()) == (2, 1, 1)  # the partial row became the sentence
        w.stream.setChecked(True)
        controls = (w.start_btn, w.mute_btn, w.my, w.their, w.mic, w.call, w.auto_upd, w.theme_box, w.mascot_switch)
        ids = [id(c) for c in controls]

        def use(key):
            w.theme_box.setCurrentIndex(w.theme_box.findData(key))
            app.processEvents()
            assert w._layout_key == key and [id(c) for c in controls] == ids
            assert all(c.isVisible() for c in controls), key  # every control found its place again
            assert w.stream.isChecked() and w.lat_label.isVisible() and not w.mascot.isVisible()
            w.grab()

        use("studio")
        assert at(w.nav["_glossary"]).y() < 60 and at(w.start_btn).y() < 60  # pages and start across the top
        assert w.chain.isVisible() and w._chain_vals["total"].text() == "1.2 s" and w._chain_vals["tr"].text() == "0.6 s"
        assert w.log_me.isVisible() and w.log_them.isVisible() and not w.log.isVisible()
        assert at(w.log_me).x() < at(w.log_them).x() < at(w.mic).x()  # my side, theirs, then the settings
        # the functions are chips here: short names, lit while on; a click is the setting
        assert w.stream.isVisible() and w.stream.text() == "边说边译" and w.stream.property("look") == "chip"
        assert "说到逗号就先翻译" in w.stream.toolTip() and w.nav["_glossary"].icon().isNull()
        w.stream.click()
        assert not w.stream.isChecked() and settings.stream is False
        w.stream.click()

        use("chat")
        assert at(w.my).y() < 70 and at(w.their).y() < 70  # the two languages head the window
        assert w.log.isVisible() and not w.log_me.isVisible() and not w.chain.isVisible()
        assert at(w.start_btn).y() > at(w.log).y() and at(w.start_btn).x() < at(w.mic).x()  # docked under the conversation
        assert at(w.nav["_glossary"]).x() < at(w.log).x() and not w.nav["_glossary"].icon().isNull()  # a rail of icons

        use("quiet")
        assert at(w.nav["_glossary"]).x() < 176 and at(w.log).x() < at(w.mic).x()  # conversation first, settings right
        assert w.log.isVisible() and not w.chain.isVisible() and at(w.start_btn).y() < 80
        # the functions are lines with sliding switches here; the check box behind each still holds the setting
        sw = w._fn_switches[w.stream]
        assert sw.isVisible() and sw.isChecked() and not w.stream.isVisible() and w.nav["_glossary"].icon().isNull()
        sw.click()
        assert not w.stream.isChecked() and settings.stream is False
        w.stream.setChecked(True)  # set from elsewhere (the tray, a hot key): the switch follows
        assert sw.isChecked()
        w.resize(820, 700)  # a narrow window: the settings go under the conversation
        app.processEvents()
        assert at(w.mic).y() > at(w.log).y()
    finally:
        w.close()
        theme.use(app, "quiet")
