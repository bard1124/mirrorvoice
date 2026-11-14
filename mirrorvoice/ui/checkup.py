"""测试链路 (one-click audio routing check) and 会议软件设置 (per-app instructions)."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
                               QPushButton, QTabWidget, QTextBrowser, QVBoxLayout, QWidget)

from .. import meeting_apps, teams_devices
from ..config import Settings

ICON = {True: "✅", False: "❌", None: "ℹ️"}


class SelfTestDialog(QDialog):
    step = Signal(str)
    done = Signal(object)

    def __init__(self, s: Settings, parent=None) -> None:
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("测试链路")
        self.resize(560, 460)
        lay = QVBoxLayout(self)
        intro = QLabel("不用开会就能检查：对方能不能听到你的翻译、对方的字幕能不能出来、麦克风和监听是否正常。"
                       "\n\n⚠️ 测试会往“送进会议”线路播放一声提示音：如果正在开会，请先退出会议或在会议里静音。",
                       wordWrap=True)
        lay.addWidget(intro)
        self.now = QLabel("", wordWrap=True)
        self.now.setStyleSheet("font-size: 15px; font-weight: 600; padding: 6px 0;")
        lay.addWidget(self.now)
        self.out = QTextBrowser()
        self.out.setOpenExternalLinks(False)
        lay.addWidget(self.out, 1)
        self.btn = QPushButton("开始测试", objectName="primary")
        self.btn.clicked.connect(self._run)
        lay.addWidget(self.btn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.step.connect(self.now.setText)
        self.done.connect(self._show)

    def _run(self) -> None:
        self.btn.setEnabled(False)
        self.btn.setText("测试中…")
        self.out.clear()

        def work() -> None:
            from ..selftest import run_all

            try:
                self.done.emit(run_all(self.s, self.step.emit))
            except Exception as e:  # noqa: BLE001
                self.done.emit(e)
        threading.Thread(target=work, daemon=True, name="selftest").start()

    def _show(self, res) -> None:
        self.btn.setEnabled(True)
        self.btn.setText("再测一次")
        if isinstance(res, Exception):
            self.now.setText("测试出错")
            self.out.setPlainText(str(res))
            return
        bad = [c for c in res if c.ok is False]
        self.now.setText("❌ 有问题需要处理" if bad else "✅ 线路正常，可以开会了")
        html = []
        for c in res:
            delay = f"（线路延迟 {c.delay_s * 1000:.0f} ms）" if c.delay_s is not None else ""
            html.append(f"<p><b>{ICON[c.ok]} {c.title}</b>{delay}<br>{c.detail}</p>")
        html.append("<p style='color:#6b7280'>会议软件里的麦克风、扬声器还要选对，见“会议软件设置”。</p>")
        self.out.setHtml("".join(html))


class TeamsOneClick(QWidget):
    """一键设置 / 一键还原: Teams' microphone and speaker without opening its settings."""

    STATE = {"set": "✅ Teams 现在用的是翻了么的线路（麦克风 BlackHole 2ch，扬声器“Teams 扬声器”）。",
             "other": "ℹ️ Teams 现在用的不是翻了么的线路。",
             "unknown": "ℹ️ 还读不到 Teams 的设置（没装新版 Teams，或还没打开过）。"}

    def __init__(self, system_audio: bool = False) -> None:
        super().__init__()
        self.system_audio = system_audio  # the other side is heard from what the Mac plays: no meeting-speaker device
        self.status = QLabel(wordWrap=True)
        self.set_btn = QPushButton("一键设置 Teams", objectName="primary")
        self.back_btn = QPushButton("一键还原")
        self.set_btn.setToolTip("把 Teams 的麦克风、扬声器改成上面第 2、3 步的样子；原来的选择会记下来。")
        self.back_btn.setToolTip("改回一键设置之前的麦克风和扬声器（没有记录时改回 Teams 默认：跟随系统）。")
        self.set_btn.clicked.connect(lambda: self._change(self._apply))
        self.back_btn.clicked.connect(lambda: self._change(teams_devices.restore))
        if system_audio:
            self.STATE = dict(self.STATE, set="✅ Teams 现在用的是翻了么的线路（麦克风 BlackHole 2ch，扬声器跟随系统）。")
        row = QHBoxLayout()
        row.addWidget(self.set_btn)
        row.addWidget(self.back_btn)
        row.addStretch(1)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 12, 0, 0)
        v.addWidget(self.status)
        v.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.status.setText(self.STATE[teams_devices.state(self.system_audio)])

    def _apply(self) -> str:
        from ..audio import default_output_name

        made = teams_devices.prepare(default_output_name(), self.system_audio)
        return (made + "\n\n" if made else "") + teams_devices.apply(self.system_audio)

    def _change(self, change) -> None:
        reopen = False
        if teams_devices.running():
            ok = QMessageBox.question(
                self, "需要重新打开 Teams",
                "Teams 只在启动时读取这项设置，所以要先退出再改。\n\n"
                "现在退出 Teams、改好后重新打开吗？正在进行的会议会断开。")
            if ok != QMessageBox.StandardButton.Yes:
                return
            reopen = True
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            if reopen and not teams_devices.quit():
                raise teams_devices.TeamsError("Teams 没有退出（可能有窗口在等你确认）。请手动退出 Teams 后再点一次。")
            text = change()
        except teams_devices.TeamsError as e:
            QApplication.restoreOverrideCursor()
            if reopen and not teams_devices.running():
                teams_devices.launch()
            self.refresh()
            QMessageBox.warning(self, "Teams 设置", str(e))
            return
        QApplication.restoreOverrideCursor()
        if reopen:
            teams_devices.launch()
            text += "\n\nTeams 正在重新打开。"
        self.refresh()
        QMessageBox.information(self, "Teams 设置", text)


class MeetingGuideDialog(QDialog):
    def __init__(self, parent=None, system_audio: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("会议软件设置")
        self.resize(600, 500)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("在会议软件里把麦克风、扬声器选成下面这样（菜单名称随版本略有不同）："))
        tabs = QTabWidget()
        for app in meeting_apps.APPS:
            page = QWidget()
            v = QVBoxLayout(page)
            text = QLabel("<br><br>".join(f"{i}. {t}" for i, t in enumerate(
                              meeting_apps.steps(app, system_audio=system_audio), 1)),
                          wordWrap=True, textFormat=Qt.TextFormat.RichText)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setStyleSheet("font-size: 14px;")
            v.addWidget(text)
            if app == "Teams" and teams_devices.supported():
                v.addWidget(TeamsOneClick(system_audio))
            v.addStretch()
            tabs.addTab(page, app)
        lay.addWidget(tabs, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
