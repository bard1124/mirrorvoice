"""Small dialogs: glossary editor, subtitle style, voice sample recorder."""
from __future__ import annotations

import threading

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..config import Settings
from ..translate import load_glossary, save_glossary
from ..tts import REF_TXT, REF_WAV


class GlossaryDialog(QDialog):
    """Fixed translations, written as  my language -> their language  (used both ways)."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("术语表")
        self.resize(520, 420)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("专有名词、产品名、人名的固定译法（两个方向都会用到）："))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["我的说法（如：中文）", "对方语言的译法"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for a, b in load_glossary():
            self._add(a, b)
        self._add("", "")
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add, rm = QPushButton("添加一行"), QPushButton("删除选中")
        add.clicked.connect(lambda: self._add("", ""))
        rm.clicked.connect(lambda: [self.table.removeRow(r.row()) for r in
                                    sorted(self.table.selectionModel().selectedRows(), key=lambda i: -i.row())])
        row.addWidget(add)
        row.addWidget(rm)
        row.addStretch()
        lay.addLayout(row)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    def _add(self, a: str, b: str) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(a))
        self.table.setItem(r, 1, QTableWidgetItem(b))

    def _save(self) -> None:
        rows = []
        for r in range(self.table.rowCount()):
            a, b = self.table.item(r, 0), self.table.item(r, 1)
            rows.append(((a.text() if a else ""), (b.text() if b else "")))
        save_glossary(rows)
        self.accept()


class StyleDialog(QDialog):
    def __init__(self, s: Settings, on_change, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("字幕样式")
        self.s, self.on_change = s, on_change
        form = QFormLayout(self)
        self.font = QSlider(Qt.Orientation.Horizontal, minimum=14, maximum=48, value=s.overlay_font)
        self.op = QSlider(Qt.Orientation.Horizontal, minimum=30, maximum=100, value=int(s.overlay_opacity * 100))
        self.orig = QCheckBox("显示原文", checked=s.overlay_show_original)
        form.addRow("字号", self.font)
        form.addRow("背景不透明度", self.op)
        form.addRow("", self.orig)
        for w in (self.font, self.op):
            w.valueChanged.connect(self._apply)
        self.orig.toggled.connect(self._apply)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.accept)
        form.addRow(bb)

    def _apply(self) -> None:
        self.s.overlay_font = self.font.value()
        self.s.overlay_opacity = self.op.value() / 100
        self.s.overlay_show_original = self.orig.isChecked()
        self.s.save()
        self.on_change()


SAMPLE = {
    "zh": "大家好，今天我想和大家分享一下我们项目的最新进展，包括已经完成的部分、遇到的问题，以及下一步的计划。",
    "en": "Hi everyone, today I'd like to share the latest progress on our project, including what we've "
          "finished, the problems we ran into, and our next steps.",
}


class VoiceDialog(QDialog):
    """Record ~12 s of your voice; the transcript is auto-filled and editable."""

    level = Signal(float)
    done = Signal(object)

    def __init__(self, s: Settings, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("录音色样本")
        self.resize(560, 420)
        self.s = s
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("安静环境，嘴离麦克风一拳，用平时说话的语气朗读下面这段（约 12 秒）。"
                             "用英文朗读，合成的英文口音通常更自然。"))
        row = QHBoxLayout()
        self.lang = QComboBox()
        self.lang.addItem("用中文朗读", "zh")
        self.lang.addItem("用英文朗读（推荐）", "en")
        self.lang.currentIndexChanged.connect(lambda: self.prompt.setText(SAMPLE[self.lang.currentData()]))
        row.addWidget(self.lang)
        row.addStretch()
        lay.addLayout(row)
        self.prompt = QLabel(SAMPLE["zh"], wordWrap=True)
        self.prompt.setStyleSheet("font-size: 16px; padding: 8px; background: palette(base); border-radius: 6px;")
        lay.addWidget(self.prompt)
        self.meter = QProgressBar(maximum=100, textVisible=False)
        lay.addWidget(self.meter)
        self.btn = QPushButton("开始录音（12 秒）")
        self.btn.clicked.connect(self._record)
        lay.addWidget(self.btn)
        lay.addWidget(QLabel("识别出的文字（必须和你读的一字不差，有错请改）："))
        self.text = QPlainTextEdit()
        if REF_TXT.exists():
            self.text.setPlainText(REF_TXT.read_text(encoding="utf-8").strip())
        lay.addWidget(self.text)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.audio: np.ndarray | None = None
        self.level.connect(lambda v: self.meter.setValue(min(100, int(v * 400))))
        self.done.connect(self._recorded)

    def _record(self) -> None:
        self.btn.setEnabled(False)
        self.btn.setText("录音中…")
        secs = [12]
        t = QTimer(self, interval=1000)
        t.timeout.connect(lambda: (secs.__setitem__(0, secs[0] - 1), self.btn.setText(f"录音中… {secs[0]}")))
        t.start()
        self._timer = t

        def work():
            from ..asr import Recognizer
            from ..audio import record_seconds, resample

            try:
                a = record_seconds(self.s.mic, 12, 24000, self.level.emit)
                peak = float(np.abs(a).max()) if len(a) else 0.0
                clipped = float((np.abs(a) > 0.99).mean()) if len(a) else 0.0
                a = a / peak * 0.9 if peak > 0.02 else a
                text, _ = Recognizer(self.lang.currentData())(resample(a, 24000, 16000))
                self.done.emit((a, text, peak, clipped))
            except Exception as e:  # noqa: BLE001
                self.done.emit(e)

        threading.Thread(target=work, daemon=True).start()

    def _recorded(self, res) -> None:
        self._timer.stop()
        self.btn.setEnabled(True)
        self.btn.setText("重新录音")
        if isinstance(res, Exception):
            QMessageBox.warning(self, "录音失败", str(res))
            return
        a, text, peak, clipped = res
        if peak < 0.02:
            QMessageBox.warning(self, "没录到声音", "几乎没录到声音，请检查麦克风权限和输入设备。")
            return
        if clipped > 0.001:
            QMessageBox.information(self, "声音偏大", "有削波（声音太大或离麦太近），建议调低输入音量后重录。")
        self.audio = a
        self.text.setPlainText(text)

    def _save(self) -> None:
        if self.audio is None and not REF_WAV.exists():
            QMessageBox.warning(self, "还没录音", "请先录一段。")
            return
        if self.audio is not None:
            for p in (REF_WAV, REF_TXT):  # keep one previous sample
                if p.exists():
                    p.replace(p.with_name(p.name.replace("ref.", "ref.prev.")))
            sf.write(REF_WAV, self.audio, 24000)
        REF_TXT.write_text(self.text.toPlainText().strip() + "\n", encoding="utf-8")
        self.accept()
