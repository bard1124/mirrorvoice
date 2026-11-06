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


def _title(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setStyleSheet("font-size: 20px; font-weight: 600; margin-bottom: 6px;")
    return lb


class Wizard(QDialog):
    progress = Signal(str, int, int)
    logline = Signal(str)
    finished_dl = Signal(str)

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
        self.progress.connect(self._on_progress)
        self.logline.connect(lambda t: self.audio_log.appendPlainText(t))
        self.finished_dl.connect(self._on_dl_done)
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
        self.m_lay = QVBoxLayout(w)
        self.m_lay.addWidget(_title("下载模型"))
        backend = models.voice_backend()
        note = {"mlx": "Apple Silicon：翻译与克隆音色用 MLX 加速。",
                "cuda": "检测到 NVIDIA 显卡：将用你的克隆音色（需在下一步安装 GPU 音色包）。",
                "kokoro": "未检测到 NVIDIA 显卡：使用标准英文音色（不是你的声音）。"}[backend]
        self.m_lay.addWidget(QLabel(note + " 来源：ModelScope / GitHub，可断点续传。"))
        self.m_rows: dict[str, tuple[QLabel, QProgressBar]] = {}
        self.m_box = QVBoxLayout()
        self.m_lay.addLayout(self.m_box)
        self.dl_btn = QPushButton("开始下载")
        self.dl_btn.clicked.connect(self._download)
        self.m_lay.addWidget(self.dl_btn)
        self.m_lay.addStretch()
        return w

    def _refresh_models(self) -> None:
        while self.m_box.count():
            it = self.m_box.takeAt(0)
            it.widget() and it.widget().deleteLater()
        self.m_rows.clear()
        self.need = models.required(self.s.quality)
        for m in self.need:
            lb = QLabel(f"{m.title}（约 {m.approx_mb} MB）")
            pb = QProgressBar(maximum=1000)
            pb.setValue(1000 if m.ready() else 0)
            pb.setFormat("已就绪" if m.ready() else "%p%")
            self.m_box.addWidget(lb)
            self.m_box.addWidget(pb)
            self.m_rows[m.title] = (lb, pb)
        done = all(m.ready() for m in self.need)
        self.dl_btn.setEnabled(not done)
        self.dl_btn.setText("全部就绪" if done else "开始下载")

    def _download(self) -> None:
        self.dl_btn.setEnabled(False)
        self.dl_btn.setText("下载中…（可以先去做下一步）")

        def work():
            try:
                for m in self.need:
                    models.download(m, lambda t, d, tot, m=m: self.progress.emit(m.title, d, tot))
                self.finished_dl.emit("")
            except Exception as e:  # noqa: BLE001
                self.finished_dl.emit(str(e))
        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, title: str, done: int, total: int) -> None:
        for key, (_lb, pb) in self.m_rows.items():
            if title.startswith(key):
                pb.setValue(int(1000 * done / max(total, 1)))
                pb.setFormat(f"%p%  {done >> 20}/{total >> 20} MB" + ("  解压中" if "解压" in title else ""))

    def _on_dl_done(self, err: str) -> None:
        self.dl_btn.setText(f"下载出错：{err[:60]}，点此重试" if err else "全部就绪")
        self.dl_btn.setEnabled(bool(err))
        if not err:
            self._refresh_models()

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
        if models.voice_backend() == "kokoro":
            self.voice_status.setText("当前电脑用标准音色，不需要录音（可跳过）。")
        else:
            self.voice_status.setText("✅ 已有音色样本" if has_voice_sample() else "❌ 还没有音色样本，点下面按钮录 12 秒")

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
