"""Small dialogs: glossary editor, subtitle style, voice sample recorder."""
from __future__ import annotations

import threading

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QProgressBar, QPushButton,
                               QSlider, QTableWidget, QTableWidgetItem, QVBoxLayout)

from ..config import Settings
from ..languages import label
from ..translate import load_glossary, save_glossary
from ..tts import REF_LANG, REF_TXT, REF_WAV


class GlossaryDialog(QDialog):
    """Meeting background + fixed translations (my language -> their language, used both ways) and
    the ways speech recognition tends to mishear each term (fixed before translating)."""

    def __init__(self, s: Settings, parent=None) -> None:
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("术语表")
        self.resize(720, 480)
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("会议背景（可选）：一句话说明会议主题，帮助翻译选对专业用词。"))
        self.meeting = QLineEdit(s.meeting_context)
        self.meeting.setPlaceholderText("例如：支付系统的技术评审，会提到对账、清结算、风控规则")
        self.meeting.setMaxLength(400)
        lay.addWidget(self.meeting)
        lay.addSpacing(6)
        lay.addWidget(QLabel("专有名词、产品名、人名的固定译法（两个方向都会用到）。"
                             "“识别常错成”填语音识别经常听错的写法，多个用 | 分隔，翻译前会自动纠正：", wordWrap=True))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["我的说法（如：中文）", "对方语言的译法", "识别常错成（可选）"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for row in load_glossary():
            self._add(*row)
        self._add("", "", "")
        lay.addWidget(self.table)
        row = QHBoxLayout()
        add, rm = QPushButton("添加一行"), QPushButton("删除选中")
        add.clicked.connect(lambda: self._add("", "", ""))
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

    def _add(self, a: str, b: str, c: str = "") -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)
        for col, text in enumerate((a, b, c)):
            self.table.setItem(r, col, QTableWidgetItem(text))

    def _save(self) -> None:
        rows = []
        for r in range(self.table.rowCount()):
            cells = [self.table.item(r, c) for c in range(3)]
            rows.append(tuple(it.text() if it else "" for it in cells))
        save_glossary(rows)
        self.s.meeting_context = self.meeting.text().strip()
        self.s.save()
        self.accept()


class StyleDialog(QDialog):
    """Subtitle bar + conversation window style, and the pop-out toggles for both."""

    def __init__(self, s: Settings, on_change, parent=None, toggle_bar=None, toggle_convo=None,
                 bar_visible=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("字幕样式")
        self.s, self.on_change = s, on_change
        self.toggle_bar, self.toggle_convo = toggle_bar, toggle_convo
        self.bar_visible = bar_visible or (lambda: s.overlay_pinned)
        form = QFormLayout(self)
        form.addRow(QLabel("<b>对方字幕条</b>（屏幕底部，手动打开后常驻）"))
        self.font = QSlider(Qt.Orientation.Horizontal, minimum=14, maximum=48, value=s.overlay_font)
        self.op = QSlider(Qt.Orientation.Horizontal, minimum=30, maximum=100, value=int(s.overlay_opacity * 100))
        self.orig = QCheckBox("显示原文", checked=s.overlay_show_original)
        form.addRow("字号", self.font)
        form.addRow("背景不透明度（两个窗口共用）", self.op)
        form.addRow("", self.orig)
        self.bar_btn = QPushButton()
        self.bar_btn.clicked.connect(self._bar)
        form.addRow("", self.bar_btn)
        form.addRow(QLabel("<b>对话悬浮窗</b>（我和对方的原文 + 译文，可拖动、可缩放）"))
        self.cfont = QSlider(Qt.Orientation.Horizontal, minimum=10, maximum=30, value=s.convo_font)
        form.addRow("字号", self.cfont)
        self.convo_btn = QPushButton()
        self.convo_btn.clicked.connect(self._convo)
        form.addRow("", self.convo_btn)
        for w in (self.font, self.op, self.cfont):
            w.valueChanged.connect(self._apply)
        self.orig.toggled.connect(self._apply)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.accept)
        form.addRow(bb)
        self._labels()

    def sync(self) -> None:
        """The bar or the conversation window was shown/hidden elsewhere (its own ✕, tray)."""
        self._labels()

    def _labels(self) -> None:
        self.bar_btn.setText("隐藏字幕条" if self.bar_visible() else "显示字幕条（常驻屏幕）")
        self.convo_btn.setText("收起对话悬浮窗" if self.s.convo_open else "弹出对话悬浮窗")
        self.bar_btn.setVisible(self.toggle_bar is not None)
        self.convo_btn.setVisible(self.toggle_convo is not None)

    def _bar(self) -> None:
        self.toggle_bar()
        self._labels()

    def _convo(self) -> None:
        self.toggle_convo()
        self._labels()

    def _apply(self) -> None:
        self.s.overlay_font = self.font.value()
        self.s.overlay_opacity = self.op.value() / 100
        self.s.overlay_show_original = self.orig.isChecked()
        self.s.convo_font = self.cfont.value()
        self.s.save()
        self.on_change()


def transcribe(audio_24k: np.ndarray, lang: str) -> tuple[str, str]:
    """Transcript in the chosen language + the language SenseVoice actually heard.

    With a fixed language SenseVoice just echoes it back, so detection needs a second pass in
    "auto" mode (cheap: the int8 model loads in well under a second)."""
    from ..asr import Recognizer
    from ..audio import resample

    a16 = resample(audio_24k, 24000, 16000)
    text, _ = Recognizer(lang)(a16)
    _, spoken = Recognizer("auto")(a16)
    return text, spoken


MAX_RECORD_S, MIN_RECORD_S = 25, 5  # the old fixed 12 s made people rush, and the clone inherited that pace

CPU_CLONE_HINT = ("CPU 版克隆音色（Pocket TTS）只能输出英文，而且用非英文样本时经常合成失控："
                  "句尾会多出杂音、每句要多等 1～3 秒。没有 RTX 30 系以上显卡或 Mac 时，请用英文朗读。")

SAMPLE = {
    "zh": "大家好，今天我想和大家分享一下我们项目的最新进展，包括已经完成的部分、遇到的问题，以及下一步的计划。",
    "en": "Hi everyone, today I'd like to share the latest progress on our project, including what we've "
          "finished, the problems we ran into, and our next steps.",
}


class VoiceDialog(QDialog):
    """Record ~12 s of your voice; the transcript is auto-filled and editable."""

    level = Signal(float)
    done = Signal(object)
    transcribed = Signal(object)  # (text, detected language code)

    def __init__(self, s: Settings, parent=None, on_speed=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("录音色样本")
        self.resize(560, 460)
        self.s = s
        self.on_speed = on_speed  # live speaking-rate update while the engine runs
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("安静环境，嘴离麦克风一拳，用<b>平时说话的语速</b>朗读下面这段，读完点“停止录音”"
                             f"（最长 {MAX_RECORD_S} 秒，至少 {MIN_RECORD_S} 秒）。克隆出来的声音会跟着样本的语速走，不用赶。",
                             wordWrap=True))
        row = QHBoxLayout()
        self.lang = QComboBox()
        self.lang.addItem("用中文朗读", "zh")
        self.lang.addItem("用英文朗读（推荐）", "en")
        from ..models import voice_backend

        self.cpu_clone = voice_backend(s) == "pocket"  # Pocket TTS wants an English reading
        if self.cpu_clone:
            self.lang.setCurrentIndex(self.lang.findData("en"))
            self.lang.setToolTip(CPU_CLONE_HINT)
        row.addWidget(self.lang)
        row.addStretch()
        lay.addLayout(row)
        self.lang_hint = QLabel(wordWrap=True)
        self.lang_hint.setStyleSheet("color: #9a6700;")
        lay.addWidget(self.lang_hint)
        self.prompt = QLabel(SAMPLE[self.lang.currentData()], wordWrap=True)
        self.lang.currentIndexChanged.connect(self._lang_changed)
        self._lang_changed()
        self.prompt.setStyleSheet("font-size: 16px; padding: 12px; background: #F7F8FA; border: 1px solid #E8EAEE;"
                                  " border-radius: 10px;")
        lay.addWidget(self.prompt)
        self.meter = QProgressBar(maximum=100, textVisible=False)
        lay.addWidget(self.meter)
        self.btn = QPushButton("开始录音")
        self.btn.clicked.connect(self._record)
        lay.addWidget(self.btn)
        self._rec_stop: threading.Event | None = None
        srow = QHBoxLayout()
        srow.addWidget(QLabel("合成语速"))
        self.speed = QSlider(Qt.Orientation.Horizontal, minimum=70, maximum=130, value=int(round(s.voice_speed * 100)))
        self.speed.setToolTip("对所有音色生效：标准音色由模型原生变速，克隆音色用变速不变调处理。运行中拖动立即生效")
        self.speed_lbl = QLabel()
        self.speed.valueChanged.connect(self._speed_changed)
        srow.addWidget(self.speed, 1)
        srow.addWidget(self.speed_lbl)
        lay.addLayout(srow)
        self._speed_changed()
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
        self._needs_asr = False  # recorded while the recognition model was still downloading
        self._spoken = ""        # language SenseVoice heard in the recording ("" = unknown)
        self.level.connect(lambda v: self.meter.setValue(min(100, int(v * 400))))
        self.done.connect(self._recorded)
        self.transcribed.connect(self._on_transcribed)
        from .downloads import DownloadManager

        self.dm = DownloadManager.get()
        self.dm.finished.connect(self._on_models_done)

    def _speed_changed(self) -> None:
        self.s.voice_speed = self.speed.value() / 100
        self.speed_lbl.setText(f"{self.s.voice_speed:.2f}×")
        self.s.save()
        if self.on_speed:
            self.on_speed(self.s.voice_speed)

    def _record(self) -> None:
        if self._rec_stop is not None:  # second click = stop early
            self._rec_stop.set()
            self.btn.setEnabled(False)
            return
        self._rec_stop = threading.Event()
        secs = [MAX_RECORD_S]
        self.btn.setText(f"停止录音（还可录 {secs[0]} 秒）")
        t = QTimer(self, interval=1000)
        t.timeout.connect(lambda: (secs.__setitem__(0, secs[0] - 1), self.btn.setText(f"停止录音（还可录 {secs[0]} 秒）")))
        t.start()
        self._timer = t

        lang = self.lang.currentData()

        def work():
            from ..audio import record_seconds
            from ..models import ASR

            try:
                a = record_seconds(self.s.mic, MAX_RECORD_S, 24000, self.level.emit, stop=self._rec_stop)
                peak = float(np.abs(a).max()) if len(a) else 0.0
                clipped = float((np.abs(a) > 0.99).mean()) if len(a) else 0.0
                a = a / peak * 0.9 if peak > 0.02 else a
                if ASR.ready():
                    text, spoken = transcribe(a, lang)
                    auto = True
                else:  # recognition model still downloading: use the sample text that was read
                    text, spoken, auto = SAMPLE[lang], "", False
                self.done.emit((a, text, peak, clipped, auto, spoken))
            except Exception as e:  # noqa: BLE001
                self.done.emit(e)

        threading.Thread(target=work, daemon=True).start()

    def _recorded(self, res) -> None:
        self._timer.stop()
        self._rec_stop = None
        self.btn.setEnabled(True)
        self.btn.setText("重新录音")
        if isinstance(res, Exception):
            QMessageBox.warning(self, "录音失败", str(res))
            return
        a, text, peak, clipped, auto, spoken = res
        if peak < 0.02:
            QMessageBox.warning(self, "没录到声音", "几乎没录到声音，请检查麦克风权限和输入设备。")
            return
        if len(a) < MIN_RECORD_S * 24000:
            QMessageBox.warning(self, "录音太短", f"只录到 {len(a) / 24000:.1f} 秒，至少需要 {MIN_RECORD_S} 秒，请重录。")
            return
        if clipped > 0.001:
            QMessageBox.information(self, "声音偏大", "有削波（声音太大或离麦太近），建议调低输入音量后重录。")
        self.audio = a
        self.text.setPlainText(text)
        self._needs_asr = not auto
        self._lang = self.lang.currentData()
        self._spoken = spoken
        self._check_spoken()
        if not auto:
            QMessageBox.information(self, "已录好", "录音已保留在本窗口。模型还在下载，下载完成后会自动识别你读的文字，"
                                    "请等下载完成、核对文字后再点“保存”。")

    def _on_models_done(self, err: str) -> None:
        """Models finished downloading while this dialog is open: transcribe the pending recording."""
        from ..models import ASR

        if err or not self._needs_asr or self.audio is None or not ASR.ready():
            return
        audio, lang = self.audio, self._lang

        def work():
            try:
                text, spoken = transcribe(audio, lang)
            except Exception:  # noqa: BLE001 — keep the sample text the user can edit
                text, spoken = "", ""
            self.transcribed.emit((text, spoken))
        threading.Thread(target=work, daemon=True).start()

    def _on_transcribed(self, res) -> None:
        text, self._spoken = res
        self._needs_asr = False
        if text:
            self.text.setPlainText(text)
        QMessageBox.information(self, "模型已就绪", "模型下载完成，已自动识别你读的文字。核对无误后点“保存”。")
        self._check_spoken()

    def _lang_changed(self) -> None:
        lang = self.lang.currentData()
        self.prompt.setText(SAMPLE[lang])
        self.lang_hint.setText("⚠️ " + CPU_CLONE_HINT if self.cpu_clone and lang != "en" else "")
        self.lang_hint.setVisible(bool(self.lang_hint.text()))

    def _mismatch(self) -> bool:
        return bool(self._spoken) and self._spoken != self._lang

    def _non_english_for_cpu(self) -> bool:
        """CPU clone voice about to get a sample that is not English (by choice or by mistake)."""
        return self.cpu_clone and (self._spoken or self._lang) != "en"

    def _check_spoken(self) -> None:
        if not self._mismatch():
            return
        heard, want = label(self._spoken), label(self._lang)
        if self._non_english_for_cpu():
            QMessageBox.warning(self, "朗读语言不一致", f"听起来你读的是{heard}，而选择的是{want}。\n" + CPU_CLONE_HINT
                                + "\n建议点“重新录音”，朗读上面的英文段落。")
        else:
            QMessageBox.information(self, "朗读语言不一致", f"听起来你读的是{heard}，而选择的是{want}。"
                                    "可以保存，但合成的口音可能不自然；建议按所选语言重录。")

    def _save(self) -> None:
        if self.audio is None and not REF_WAV.exists():
            QMessageBox.warning(self, "还没录音", "请先录一段。")
            return
        missing = self.dm.missing(self.s.quality)
        if missing or self._needs_asr:
            if not self.dm.running:
                self.dm.start(self.s.quality)
            names = "、".join(m.title for m in missing) or "语音识别"
            QMessageBox.information(
                self, "请等模型下载完成",
                f"模型还没下载完成（{names}），请等下载完成后再保存录音。\n\n"
                "录音已保留在本窗口，不用重录；下载进度见主窗口顶部或设置向导的“下载模型”页。"
                "下载完成后会自动识别文字，再点“保存”即可。")
            return
        if self.audio is not None and self._non_english_for_cpu():
            heard = label(self._spoken or self._lang)
            if QMessageBox.question(
                    self, "确定保存非英文样本？",
                    f"这段录音是{heard}。" + CPU_CLONE_HINT + "\n\n仍要保存吗？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                return
        if self.audio is not None:
            for p in (REF_WAV, REF_TXT):  # keep one previous sample
                if p.exists():
                    p.replace(p.with_name(p.name.replace("ref.", "ref.prev.")))
            sf.write(REF_WAV, self.audio, 24000)
        REF_TXT.write_text(self.text.toPlainText().strip() + "\n", encoding="utf-8")
        if self.audio is not None:
            REF_LANG.write_text(self._lang, encoding="utf-8")
        self.accept()
