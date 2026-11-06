"""First-run wizard: languages -> models -> virtual audio -> voice sample -> meeting app setup."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit,
                               QProgressBar, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import models, virtual_audio
from ..config import IS_MAC, Settings
from ..languages import LANGS
from ..tts import has_voice_sample
from .dialogs import VoiceDialog
from .downloads import DownloadManager, fmt_bytes, fmt_eta


def _title(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet("font-size: 20px; font-weight: 600; margin-bottom: 6px;")
    return lb


class Wizard(QDialog):
    logline = Signal(str)

    def __init__(self, s: Settings, parent=None) -> None:
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("同传助手 · 设置向导")
        self.resize(640, 520)
        self.pages = QStackedWidget()
        for build in (self._p_lang, self._p_models, self._p_audio, self._p_voice, self._p_meeting):
            self.pages.addWidget(build())
        nav = QHBoxLayout()
        self.back, self.next = QPushButton("上一步"), QPushButton("下一步")
        self.back.clicked.connect(lambda: self._go(-1))
        self.next.clicked.connect(lambda: self._go(1))
        nav.addStretch()
        nav.addWidget(self.back)
        nav.addWidget(self.next)
        lay = QVBoxLayout(self)
        lay.addWidget(self.pages, 1)
        lay.addLayout(nav)
        self.logline.connect(lambda t: self.audio_log.appendPlainText(t))
        self._go(0)

    def _go(self, step: int) -> None:
        i = max(0, min(self.pages.count() - 1, self.pages.currentIndex() + step))
        if step > 0 and self.pages.currentIndex() == self.pages.count() - 1:
            self.s.setup_done = True
            self.s.save()
            self.accept()
            return
        self.pages.setCurrentIndex(i)
        self.back.setEnabled(i > 0)
        self.next.setText("完成" if i == self.pages.count() - 1 else "下一步")
        if i == 1:
            self._refresh_models()
        if i == 2:
            self._refresh_audio()
        if i == 3:
            self._refresh_voice()

    # ---------------------------------------------------------------- 1 languages
    def _p_lang(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        f.addRow(_title("欢迎使用同传助手"))
        f.addRow(QLabel("你说话 → 对方听到翻译后的语音（你的克隆音色）；对方说话 → 你看到翻译字幕。全部在本机运行。"))
        self.my = QComboBox()
        self.their = QComboBox()
        for c, lg in LANGS.items():
            self.my.addItem(lg.label, c)
            if lg.tts:
                self.their.addItem(lg.label, c)
        self.my.setCurrentIndex(self.my.findData(self.s.my_lang))
        self.their.setCurrentIndex(self.their.findData(self.s.their_lang))
        self.q = QComboBox()
        self.q.addItem("快速（推荐，约 4.5 GB 模型）", "fast")
        self.q.addItem("高质量（更准更像，约 8 GB，更慢）", "quality")
        self.q.setCurrentIndex(self.q.findData(self.s.quality))
        self.disp = QComboBox()
        self.disp.addItem("图形界面", "ui")
        self.disp.addItem("命令行风格", "cli")
        self.disp.setCurrentIndex(self.disp.findData(self.s.display))
        f.addRow("我说", self.my)
        f.addRow("对方说", self.their)
        f.addRow("模型", self.q)
        f.addRow("显示方式", self.disp)

        def save():
            self.s.my_lang, self.s.their_lang = self.my.currentData(), self.their.currentData()
            self.s.quality, self.s.display = self.q.currentData(), self.disp.currentData()
            self.s.save()
        for c in (self.my, self.their, self.q, self.disp):
            c.currentIndexChanged.connect(save)
        return w

    # ---------------------------------------------------------------- 2 models
    def _p_models(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_title("下载模型"))
        backend = models.voice_backend(self.s)
        note = {"mlx": "Apple Silicon：翻译与克隆音色用 MLX 加速。",
                "cuda": "检测到 NVIDIA 显卡：将用你的克隆音色（需在下一步安装 GPU 音色包）。",
                "pocket": "未检测到 NVIDIA 显卡：用 CPU 版克隆音色 Pocket TTS（只能说英文，录音色时需用英文朗读；"
                          "该模型仅限非商业用途）。",
                "kokoro": "未检测到 NVIDIA 显卡：使用标准音色（不是你的声音）。"}[backend]
        lay.addWidget(QLabel(note + "\n进入本页会自动开始下载（来源 ModelScope / GitHub，支持断点续传）。"
                             "可以先去做下一步，关掉向导也会在后台继续下载。", wordWrap=True))
        self.dl_total = QProgressBar(maximum=1000)
        self.dl_total.setMinimumHeight(26)
        self.dl_status = QLabel("准备下载…")
        self.dl_status.setStyleSheet("font-size: 14px;")
        lay.addWidget(self.dl_total)
        lay.addWidget(self.dl_status)
        self.m_box = QVBoxLayout()
        lay.addLayout(self.m_box)
        row = QHBoxLayout()
        self.dl_btn = QPushButton("重试")
        self.dl_btn.clicked.connect(lambda: self.dm.start(self.s.quality))
        self.dl_btn.hide()
        row.addWidget(self.dl_btn)
        row.addStretch()
        lay.addLayout(row)
        lay.addStretch()
        self.m_rows: dict[str, QProgressBar] = {}
        self.dm = DownloadManager.get()
        self.dm.model_progress.connect(self._on_model_progress)
        self.dm.overall.connect(self._on_overall)
        self.dm.finished.connect(self._on_dl_done)
        return w

    def _refresh_models(self) -> None:
        while self.m_box.count():
            it = self.m_box.takeAt(0)
            it.widget() and it.widget().deleteLater()
        self.m_rows.clear()
        for m in models.required(self.s.quality):
            lb = QLabel(f"{m.title}（约 {m.approx_mb} MB）")
            lb.setMinimumWidth(260)
            pb = QProgressBar(maximum=1000)
            pb.setValue(1000 if m.ready() else 0)
            pb.setFormat("✅ 已就绪" if m.ready() else "等待中")
            box = QWidget()
            hl = QHBoxLayout(box)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(lb)
            hl.addWidget(pb, 1)
            self.m_box.addWidget(box)
            self.m_rows[m.title] = pb
        if DownloadManager.missing(self.s.quality):
            self.dl_btn.hide()
            self.dm.start(self.s.quality)  # auto-start; no-op if already running
            self.dl_status.setText("下载中…")
        else:
            self._on_dl_done("")

    def _on_model_progress(self, title: str, done: int, total: int) -> None:
        for key, pb in self.m_rows.items():
            if title.startswith(key):
                pb.setValue(int(1000 * done / max(total, 1)))
                pb.setFormat("✅ 已就绪" if done >= total else
                             ("解压中…" if "解压" in title else f"%p%  {fmt_bytes(done)} / {fmt_bytes(total)}"))

    def _on_overall(self, done: int, total: int, speed: float) -> None:
        self.dl_total.setValue(int(1000 * done / max(total, 1)))
        self.dl_total.setFormat(f"总进度 %p%  {fmt_bytes(done)} / {fmt_bytes(total)}")
        eta = fmt_eta((total - done) / speed) if speed > 0 else ""
        self.dl_status.setText(f"下载中  {speed / (1 << 20):.1f} MB/s  {('剩余 ' + eta) if eta else ''}")

    def _on_dl_done(self, err: str) -> None:
        if err:
            self.dl_status.setText(f"❌ 下载中断：{err[:160]}\n已下载的部分会保留，检查网络后点“重试”继续。")
            self.dl_status.setStyleSheet("font-size: 14px; color: #c62828;")
            self.dl_btn.show()
            return
        if not DownloadManager.missing(self.s.quality):
            self.dl_total.setValue(1000)
            self.dl_total.setFormat("全部就绪")
            self.dl_status.setText("✅ 模型全部下载完成")
            self.dl_status.setStyleSheet("font-size: 14px; color: #1a9e4b;")
            self.dl_btn.hide()
            for pb in self.m_rows.values():
                pb.setValue(1000)
                pb.setFormat("✅ 已就绪")

    # ---------------------------------------------------------------- 3 virtual audio
    def _p_audio(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_title("虚拟声卡"))
        if IS_MAC:
            lay.addWidget(QLabel("BlackHole 2ch：把你的翻译语音送进会议软件（当麦克风）。\n"
                                 "BlackHole 16ch + 多输出设备：把会议里对方的声音同时送给耳机和字幕。"))
        else:
            lay.addWidget(QLabel("VB-CABLE：把你的翻译语音送进会议软件（当麦克风）。对方声音直接从扬声器回采，无需额外设置。"))
        self.audio_status = QLabel()
        lay.addWidget(self.audio_status)
        row = QHBoxLayout()
        if IS_MAC:
            for text, fn in (("安装 BlackHole 2ch", lambda: virtual_audio.mac_install_blackhole(2)),
                             ("安装 BlackHole 16ch", lambda: virtual_audio.mac_install_blackhole(16)),
                             ("重启音频服务（装完后点）", virtual_audio.mac_restart_coreaudio),
                             ("创建多输出设备", self._make_multi)):
                b = QPushButton(text)
                b.clicked.connect(lambda _=False, fn=fn: (fn(), self._refresh_audio()))
                row.addWidget(b)
        else:
            b = QPushButton("下载并安装 VB-CABLE")
            b.clicked.connect(lambda: threading.Thread(
                target=virtual_audio.win_install_vbcable, args=(self.logline.emit,), daemon=True).start())
            row.addWidget(b)
            if models.voice_backend() == "cuda":
                g = QPushButton("安装 GPU 音色包（约 3 GB）")
                g.clicked.connect(lambda: threading.Thread(
                    target=virtual_audio.win_install_gpu_pack, args=(self.logline.emit,), daemon=True).start())
                row.addWidget(g)
        r = QPushButton("刷新")
        r.clicked.connect(self._refresh_audio)
        row.addWidget(r)
        lay.addLayout(row)
        self.audio_log = QPlainTextEdit(readOnly=True)
        self.audio_log.setMaximumHeight(160)
        lay.addWidget(self.audio_log)
        lay.addStretch()
        return w

    def _make_multi(self) -> None:
        self.audio_log.appendPlainText(virtual_audio.mac_create_multi_output(self.s.monitor or "耳机"))

    def _refresh_audio(self) -> None:
        st = virtual_audio.status()
        if not IS_MAC and models.voice_backend() == "cuda":
            st["GPU 音色包"] = virtual_audio.gpu_pack_installed()
        self.audio_status.setText("\n".join(f"{'✅' if ok else '❌'}  {k}" for k, ok in st.items()))

    # ---------------------------------------------------------------- 4 voice
    def _p_voice(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_title("录你的音色样本"))
        self.voice_status = QLabel()
        lay.addWidget(self.voice_status)
        b = QPushButton("录音 / 重录…")
        b.clicked.connect(lambda: (VoiceDialog(self.s, self).exec(), self._refresh_voice()))
        lay.addWidget(b, alignment=Qt.AlignmentFlag.AlignLeft)
        lay.addStretch()
        return w

    def _refresh_voice(self) -> None:
        from ..tts import sample_language

        backend = models.voice_backend(self.s)
        if backend == "kokoro":
            self.voice_status.setText("当前设置使用标准音色，不需要录音（可跳过）。")
        elif not has_voice_sample():
            self.voice_status.setText("❌ 还没有音色样本，点下面按钮录 12 秒" +
                                      ("（请用英文朗读）" if backend == "pocket" else ""))
        elif backend == "pocket" and sample_language() != "en":
            self.voice_status.setText("⚠️ 现有样本不是用英文朗读的。没有 NVIDIA 显卡时克隆音色需要英文朗读的样本，请重录。")
        else:
            self.voice_status.setText("✅ 已有音色样本")

    # ---------------------------------------------------------------- 5 meeting app
    def _p_meeting(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.addWidget(_title("设置会议软件"))
        mic = "BlackHole 2ch" if IS_MAC else "CABLE Output (VB-Audio Virtual Cable)"
        spk = virtual_audio.MULTI_OUTPUT_NAME if IS_MAC else "你的耳机（保持默认即可）"
        lay.addWidget(QLabel(
            f"在 Teams / Zoom / Meet / 腾讯会议 的音频设置里：\n\n"
            f"  • 麦克风：{mic}\n  • 扬声器：{spk}\n\n"
            "提示：用耳机，不要用外放，避免翻译语音被麦克风再次收进去。\n"
            "全局快捷键（按住说话 / 静音）需要在系统设置里给本应用“辅助功能 / 输入监控”权限。", wordWrap=True))
        lay.addStretch()
        return w
