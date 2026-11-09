"""测试链路 (one-click audio routing check) and 会议软件设置 (per-app instructions)."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QPushButton, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from .. import meeting_apps
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


class MeetingGuideDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("会议软件设置")
        self.resize(600, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("在会议软件里把麦克风、扬声器选成下面这样（菜单名称随版本略有不同）："))
        tabs = QTabWidget()
        for app in meeting_apps.APPS:
            page = QWidget()
            v = QVBoxLayout(page)
            text = QLabel("<br><br>".join(f"{i}. {t}" for i, t in enumerate(meeting_apps.steps(app), 1)),
                          wordWrap=True, textFormat=Qt.TextFormat.RichText)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text.setStyleSheet("font-size: 14px;")
            v.addWidget(text)
            v.addStretch()
            tabs.addTab(page, app)
        lay.addWidget(tabs, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
