"""Control panel: start/stop, languages, devices, features, live transcript."""
from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QVBoxLayout, QWidget)

from .. import __version__, models
from ..audio import list_devices
from ..config import IS_MAC, LOG_FILE, TRANSCRIPTS, Settings
from ..engine import Engine
from ..hotkeys import PTT_KEYS
from ..languages import LANGS
from .bridge import Bridge
from .convo import ConversationWindow
from .dialogs import GlossaryDialog, StyleDialog, VoiceDialog
from .icons import app_icon
from .overlay import Overlay
from .tray import Tray
from .wizard import Wizard


class MainWindow(QMainWindow):
    def __init__(self, s: Settings) -> None:
        super().__init__()
        self.s = s
        self.setWindowTitle(f"同传助手 {__version__}")
        self.resize(560, 820)
        self.bridge = Bridge()
        self.bridge.event.connect(self._on_event)
        self.engine = Engine(self.bridge.emit_event)
        self.setWindowIcon(app_icon())
        self.overlay = Overlay(s)
        self.overlay.on_hidden = self._overlay_hidden
        self.convo = ConversationWindow(s)
        self.convo.on_closed = self._convo_closed
        from .downloads import DownloadManager

        self.dm = DownloadManager.get()
        self._build()
        self.tray = Tray(self)
        if s.convo_open:
            self.convo.show()
        if s.overlay_pinned:
            self.overlay.show()
        self.dm.overall.connect(self._dl_overall)
        self.dm.finished.connect(self._dl_finished)
        self._refresh_banner()
        self._load_devices()
        from PySide6.QtCore import QTimer

        from .update_ui import UpdateController

        self.updates = UpdateController(self, s, self._before_update)
        if s.auto_check_update:
            QTimer.singleShot(3000, lambda: self.updates.check(manual=False))

    # ---------------------------------------------------------------- layout
    def _build(self) -> None:
        root = QWidget()
        lay = QVBoxLayout(root)

        top = QHBoxLayout()
        self.status = QLabel("● 已停止")
        self.status.setStyleSheet("font-size: 15px; color: gray;")
        self.start_btn = QPushButton("开始")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.start_btn.clicked.connect(self._toggle)
        self.mute_btn = QPushButton("静音")
        self.mute_btn.setCheckable(True)
        self.mute_btn.toggled.connect(self.engine.set_muted)
        top.addWidget(self.status, 1)
        top.addWidget(self.mute_btn)
        top.addWidget(self.start_btn)
        lay.addLayout(top)

        # model status banner: always tells whether models are ready / downloading / failed
        self.banner = QWidget()
        self.banner.setStyleSheet("background: #fff4e5; border-radius: 6px;")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(10, 6, 10, 6)
        self.banner_text = QLabel()
        self.banner_text.setStyleSheet("color: #8a5300;")
        self.banner_bar = QProgressBar(maximum=1000)
        self.banner_bar.setMaximumHeight(14)
        self.banner_btn = QPushButton("下载模型")
        self.banner_btn.clicked.connect(lambda: self.dm.start(self.s.quality))
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_bar, 1)
        bl.addWidget(self.banner_btn)
        lay.addWidget(self.banner)

        g = QGroupBox("语言")
        h = QHBoxLayout(g)
        self.my, self.their = QComboBox(), QComboBox()
        for c, lg in LANGS.items():
            self.my.addItem(lg.label, c)
            if lg.tts:
                self.their.addItem(lg.label, c)
        swap = QPushButton("⇄")
        swap.setFixedWidth(36)
        swap.clicked.connect(self._swap)
        h.addWidget(QLabel("我说"))
        h.addWidget(self.my, 1)
        h.addWidget(swap)
        h.addWidget(QLabel("对方说"))
        h.addWidget(self.their, 1)
        lay.addWidget(g)

        g = QGroupBox("声音设备")
        f = QFormLayout(g)
        self.mic, self.call, self.mon, self.src = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        f.addRow("我的麦克风", self.mic)
        f.addRow("送进会议（虚拟麦克风）", self.call)
        f.addRow("我自己监听（耳机）", self.mon)
        f.addRow("对方声音来源", self.src)
        rb = QPushButton("刷新设备")
        rb.clicked.connect(self._load_devices)
        f.addRow("", rb)
        lay.addWidget(g)

        g = QGroupBox("功能")
        v = QVBoxLayout(g)
        self.speak = QCheckBox("用我的声音说出翻译（送进会议）")
        self.subs = QCheckBox("对方说话显示翻译字幕")
        self.passthru = QCheckBox("我直接说对方的语言时，透传原声不翻译")
        for w in (self.speak, self.subs, self.passthru):
            v.addWidget(w)
        mh = QHBoxLayout()
        self.cont, self.ptt = QRadioButton("持续翻译"), QRadioButton("按住说话")
        bg = QButtonGroup(self)
        bg.addButton(self.cont)
        bg.addButton(self.ptt)
        self.ptt_key = QComboBox()
        for k, name in PTT_KEYS.items():
            self.ptt_key.addItem(name, k)
        mh.addWidget(self.cont)
        mh.addWidget(self.ptt)
        mh.addWidget(self.ptt_key)
        mh.addStretch()
        v.addLayout(mh)
        oh = QHBoxLayout()
        self.quality = QComboBox()
        self.quality.addItem(f"快速（{models.llm_name('fast')}）", "fast")
        self.quality.addItem(f"高质量（{models.llm_name('quality')}）", "quality")
        for i, q in enumerate(("fast", "quality")):
            self.quality.setItemData(i, models.summary(q), Qt.ItemDataRole.ToolTipRole)
        self.display = QComboBox()
        self.display.addItem("图形界面", "ui")
        self.display.addItem("命令行风格", "cli")
        oh.addWidget(QLabel("模型"))
        oh.addWidget(self.quality)
        oh.addWidget(QLabel("显示方式"))
        oh.addWidget(self.display)
        from ..config import cuda_voice_ok, has_nvidia_gpu

        self.gpu_voice = QComboBox()
        self.gpu_voice.addItem("自动（RTX 30 系及以上）", "auto")
        self.gpu_voice.addItem("强制使用（旧卡会很慢）", "cuda")
        self.gpu_voice.addItem("不使用", "cpu")
        self.gpu_voice.setCurrentIndex(max(0, self.gpu_voice.findData(self.s.gpu_voice)))
        self.gpu_voice.currentIndexChanged.connect(
            lambda: (setattr(self.s, "gpu_voice", self.gpu_voice.currentData()), self.s.save(), self._refresh_banner()))
        if not IS_MAC and has_nvidia_gpu():
            oh.addWidget(QLabel("GPU 音色"))
            oh.addWidget(self.gpu_voice)
        self.cpu_voice = QComboBox()
        self.cpu_voice.addItem("我的克隆音色（CPU，英文样本）", "clone")
        self.cpu_voice.addItem("标准音色", "standard")
        self.cpu_voice.setCurrentIndex(max(0, self.cpu_voice.findData(self.s.cpu_voice)))
        self.cpu_voice.currentIndexChanged.connect(
            lambda: (setattr(self.s, "cpu_voice", self.cpu_voice.currentData()), self.s.save(), self._refresh_banner()))
        if not IS_MAC and not cuda_voice_ok(self.s.gpu_voice):  # Windows without a usable CUDA voice
            oh.addWidget(QLabel("音色"))
            oh.addWidget(self.cpu_voice)
        oh.addStretch()
        v.addLayout(oh)
        hk = self.s.mute_hotkey.replace("<ctrl>", "⌃" if IS_MAC else "Ctrl+").replace("<alt>", "⌥" if IS_MAC else "Alt+").replace("+", "").upper() if IS_MAC else self.s.mute_hotkey.replace("<", "").replace(">", "").title()
        self.auto_upd = QCheckBox("启动时检查更新（离线时自动跳过）")
        self.auto_upd.setChecked(self.s.auto_check_update)
        self.auto_upd.toggled.connect(lambda on: (setattr(self.s, "auto_check_update", on), self.s.save()))
        v.addWidget(self.auto_upd)
        v.addWidget(QLabel(f"静音快捷键：{hk}　（全局快捷键需要系统“辅助功能/输入监控”权限）",
                           styleSheet="color: gray;"))
        lay.addWidget(g)

        mh = QHBoxLayout()
        self.lv_me, self.lv_them = QProgressBar(maximum=100, textVisible=False), QProgressBar(maximum=100, textVisible=False)
        for w in (self.lv_me, self.lv_them):
            w.setMaximumHeight(8)
        mh.addWidget(QLabel("我"))
        mh.addWidget(self.lv_me)
        mh.addWidget(QLabel("对方"))
        mh.addWidget(self.lv_them)
        lay.addLayout(mh)

        self.log = QListWidget()
        self.log.setWordWrap(True)
        lay.addWidget(self.log, 1)

        bh = QHBoxLayout()
        for text, fn in (("术语表", self._glossary), ("字幕样式", self._style), ("音色", self._voice),
                         ("会议记录", self._open_transcripts), ("设置向导", self._wizard),
                         ("检查更新", lambda: self.updates.check(manual=True))):
            b = QPushButton(text)
            b.clicked.connect(fn)
            bh.addWidget(b)
        lay.addLayout(bh)
        self.setCentralWidget(root)

        # load settings into widgets, save on any change
        s = self.s
        self.my.setCurrentIndex(self.my.findData(s.my_lang))
        self.their.setCurrentIndex(self.their.findData(s.their_lang))
        self.speak.setChecked(s.speak)
        self.subs.setChecked(s.subtitles)
        self.passthru.setChecked(s.passthrough)
        (self.ptt if s.mode == "ptt" else self.cont).setChecked(True)
        self.ptt_key.setCurrentIndex(max(0, self.ptt_key.findData(s.ptt_key)))
        self.quality.setCurrentIndex(self.quality.findData(s.quality))
        self.display.setCurrentIndex(self.display.findData(s.display))
        for w in (self.my, self.their, self.ptt_key, self.quality, self.display, self.mic, self.call, self.mon, self.src):
            w.currentIndexChanged.connect(self._save)
        for w in (self.speak, self.subs, self.passthru, self.ptt):
            w.toggled.connect(self._save)
        self.display.currentIndexChanged.connect(self._display_changed)

    def _load_devices(self) -> None:
        def fill(combo: QComboBox, items: list[tuple[str, str]], cur: str) -> None:
            combo.blockSignals(True)
            combo.clear()
            for text, data in items:
                combo.addItem(text, data)
            i = combo.findData(cur)
            if i < 0 and cur:  # same partial match as audio.find_device
                i = next((k for k in range(combo.count())
                          if cur.lower() in str(combo.itemData(k)).lower()), -1)
            if i < 0 and cur:
                combo.addItem(f"{cur}（未连接）", cur)
                i = combo.count() - 1
            combo.setCurrentIndex(max(0, i))
            combo.blockSignals(False)

        ins, outs = list_devices("input"), list_devices("output")
        fill(self.mic, [("系统默认", "")] + [(n, n) for n in ins if not n.startswith("loopback:")], self.s.mic)
        fill(self.call, [(n, n) for n in outs], self.s.call_out)
        fill(self.mon, [("不监听", "")] + [(n, n) for n in outs], self.s.monitor)
        src_items = [(("扬声器回采：" + n.split(':', 1)[1]) if n.startswith("loopback:") else n, n) for n in ins]
        fill(self.src, [("不显示字幕", "")] + src_items, self.s.their_source)

    # settings that only take effect when the engine (re)starts
    RESTART_KEYS = ("my_lang", "their_lang", "mic", "call_out", "their_source", "speak", "subtitles",
                    "passthrough", "mode", "ptt_key", "quality")

    def _save(self) -> None:
        s = self.s
        before = {k: getattr(s, k) for k in self.RESTART_KEYS + ("monitor",)}
        s.my_lang, s.their_lang = self.my.currentData(), self.their.currentData()
        s.mic, s.call_out, s.monitor, s.their_source = (self.mic.currentData(), self.call.currentData(),
                                                        self.mon.currentData(), self.src.currentData())
        s.speak, s.subtitles, s.passthrough = self.speak.isChecked(), self.subs.isChecked(), self.passthru.isChecked()
        s.mode = "ptt" if self.ptt.isChecked() else "continuous"
        s.ptt_key, s.quality, s.display = self.ptt_key.currentData(), self.quality.currentData(), self.display.currentData()
        s.save()
        if hasattr(self, "banner"):
            self._refresh_banner()
        if self.engine.running:
            if before["monitor"] != s.monitor:
                self.engine.set_monitor(s.monitor)  # the one device that can be swapped live
            if any(before[k] != getattr(s, k) for k in self.RESTART_KEYS):
                self.status.setText("● 运行中 · 设置已保存，点“停止”再“开始”后生效")
                self.status.setStyleSheet("font-size: 15px; color: #d08700;")

    def _swap(self) -> None:
        my, their = self.my.currentData(), self.their.currentData()
        if LANGS[my].tts:
            self.their.setCurrentIndex(self.their.findData(my))
            self.my.setCurrentIndex(self.my.findData(their))

    def _display_changed(self) -> None:
        if self.display.currentData() == "cli":
            r = QMessageBox.question(self, "切换到命令行风格", "下次点击应用图标会以命令行窗口启动。现在就切换吗？")
            if r == QMessageBox.StandardButton.Yes:
                from .launch import open_cli_terminal

                self.engine.stop()
                open_cli_terminal()
                self.close()

    # ---------------------------------------------------------------- actions
    def _toggle(self) -> None:
        if self.engine.running:
            self.engine.stop()
            return
        self._save()
        if self.dm.missing(self.s.quality):
            self.dm.start(self.s.quality)
            self._refresh_banner()
            QMessageBox.information(self, "模型下载中", "模型还没下载完整，已在后台继续下载，进度见窗口顶部。下载完成后再点“开始”。")
            return
        self.start_btn.setText("停止")
        self.status.setText("● 启动中…")
        self.status.setStyleSheet("font-size: 15px; color: #d08700;")
        self.engine.start(self.s)
        self.tray.refresh()

    # ---------------------------------------------------------------- tray / floating windows
    def toggle_mute(self) -> None:
        self.mute_btn.setChecked(not self.mute_btn.isChecked())

    def toggle_convo(self) -> None:
        if self.convo.isVisible():
            self.convo.hide()
        else:
            self.convo.show()
        self.s.convo_open = self.convo.isVisible()
        self.s.save()
        self.tray.refresh()

    def _convo_closed(self) -> None:
        if self.s.convo_open:
            self.s.convo_open = False
            self.s.save()
        if hasattr(self, "tray"):
            self.tray.refresh()

    def toggle_overlay_pin(self) -> None:
        """Tray / 字幕样式: show or hide the subtitle bar right now. Shown by hand = pinned (stays
        after stop); hidden by hand stays hidden until the next start or the next toggle."""
        if self.overlay.isVisible():
            self.overlay.hide()  # on_hidden clears the pin
        else:
            self.s.overlay_pinned = True
            self.s.save()
            self.overlay.show()
        self.tray.refresh()

    def _overlay_hidden(self) -> None:
        if self.s.overlay_pinned:
            self.s.overlay_pinned = False
            self.s.save()
        if hasattr(self, "tray"):
            self.tray.refresh()

    def show_main(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self.engine.stop()
        self.overlay.close()
        self.convo.close()
        QApplication.quit()

    def _on_event(self, kind: str, d: dict) -> None:
        if kind == "level":
            (self.lv_me if d["who"] == "me" else self.lv_them).setValue(min(100, int(d["rms"] * 600)))
        elif kind == "status":
            self.status.setText("● " + d["text"])
        elif kind == "ready":
            self.status.setText(f"● 运行中 · {d['voice']}")
            self.status.setStyleSheet("font-size: 15px; color: #1a9e4b;")
            if self.s.subtitles and d["them"]:
                self.overlay.show()
            self.tray.refresh()
        elif kind == "me":
            tag = "我·原声" if d["passthrough"] else "我"
            self._add(f"{tag}  {d['src']}\n→ {d['tgt']}   ({d['latency']:.1f}s)", "#1f6feb")
            self.convo.add("me", d["src"], d["tgt"], d["passthrough"])
        elif kind == "them":
            self._add(f"对方  {d['src']}\n→ {d['tgt']}   ({d['latency']:.1f}s)", "#8250df")
            self.overlay.show_text(d["tgt"], d["src"] if d["tgt"] != d["src"] else "")
            self.convo.add("them", d["src"], d["tgt"])
        elif kind == "mute":
            self.mute_btn.blockSignals(True)
            self.mute_btn.setChecked(d["muted"])
            self.mute_btn.blockSignals(False)
            self.mute_btn.setText("已静音" if d["muted"] else "静音")
            self.tray.refresh()
        elif kind == "ptt":
            self.status.setText("● 说话中…" if d["down"] else "● 运行中")
        elif kind == "error":
            QMessageBox.warning(self, "出错了", d["message"] + f"\n\n详细日志：{LOG_FILE}")
        elif kind == "stopped":
            self.start_btn.setText("开始")
            self.status.setText("● 已停止")
            self.status.setStyleSheet("font-size: 15px; color: gray;")
            if not self.s.overlay_pinned:
                self.overlay.hide()
            self.tray.refresh()

    def _add(self, text: str, color: str) -> None:
        it = QListWidgetItem(text)
        it.setForeground(QColor(color))
        self.log.addItem(it)
        if self.log.count() > 300:
            self.log.takeItem(0)
        self.log.scrollToBottom()

    def _glossary(self) -> None:
        if GlossaryDialog(self).exec():
            self.engine.reload_glossary()

    def _style(self) -> None:
        self.overlay.show()
        StyleDialog(self.s, lambda: (self.overlay.apply_style(), self.convo.apply_style()), self,
                    toggle_bar=self.toggle_overlay_pin, toggle_convo=self.toggle_convo,
                    bar_visible=self.overlay.isVisible).exec()
        if not self.engine.running and not self.s.overlay_pinned:
            self.overlay.hide()

    def _voice(self) -> None:
        VoiceDialog(self.s, self).exec()

    def _open_transcripts(self) -> None:
        if IS_MAC:
            subprocess.run(["open", str(TRANSCRIPTS)])
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(TRANSCRIPTS)))

    def _wizard(self) -> None:
        Wizard(self.s, self).exec()
        self._load_devices()
        self._refresh_banner()

    def _refresh_banner(self) -> None:
        missing = self.dm.missing(self.s.quality)
        if not missing:
            self.banner.hide()
            return
        self.banner.show()
        if self.dm.running:
            self.banner_btn.hide()
            self.banner_bar.show()
            if not self.banner_text.text().startswith("模型下载中"):
                self.banner_text.setText("模型下载中…")
        else:
            mb = sum(m.approx_mb for m in missing)
            err = f"（上次中断：{self.dm.error[:60]}）" if self.dm.error else ""
            self.banner_text.setText(f"⚠️ 还有 {len(missing)} 个模型未下载（约 {mb / 1024:.1f} GB）{err}")
            self.banner_bar.hide()
            self.banner_btn.setText("继续下载" if self.dm.error else "下载模型")
            self.banner_btn.show()

    def _dl_overall(self, done: int, total: int, speed: float) -> None:
        from .downloads import fmt_bytes, fmt_eta

        self.banner.show()
        self.banner_btn.hide()
        self.banner_bar.show()
        self.banner_bar.setValue(int(1000 * done / max(total, 1)))
        eta = fmt_eta((total - done) / speed) if speed > 0 else ""
        self.banner_text.setText(f"模型下载中 {100 * done // max(total, 1)}%  {fmt_bytes(done)} / {fmt_bytes(total)}"
                                 f"  {speed / (1 << 20):.1f} MB/s  {eta}")

    def _dl_finished(self, err: str) -> None:
        self._refresh_banner()
        if not err and not self.dm.missing(self.s.quality):
            self.status.setText("● 模型已就绪，可以开始")

    def _before_update(self) -> None:
        self.engine.stop()
        self.overlay.close()
        self.convo.close()

    def closeEvent(self, e) -> None:
        self.engine.stop()
        self.overlay.close()
        self.convo.close()
        super().closeEvent(e)
