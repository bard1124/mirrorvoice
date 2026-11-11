"""Control panel: start/stop, languages, devices, features, live transcript."""
from __future__ import annotations

import subprocess
import threading
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
                               QProgressBar, QPushButton, QRadioButton, QScrollArea, QVBoxLayout, QWidget, QBoxLayout)

from .. import __version__, models
from .. import device_hints as dh
from ..audio import default_output_name, list_devices
from ..config import IS_MAC, LOG_FILE, TRANSCRIPTS, Settings
from ..engine import Engine
from ..timing import LatencyStats, breakdown
from ..hotkeys import PTT_KEYS
from ..languages import LANGS
from .bridge import Bridge
from .checkup import MeetingGuideDialog, SelfTestDialog
from .convo import ConversationWindow
from .dialogs import GlossaryDialog, StyleDialog, VoiceDialog
from .icons import app_icon, brand_icon
from .theme import card, repolish
from .overlay import Overlay
from .tray import Tray
from .wizard import Wizard


class MainWindow(QMainWindow):
    def __init__(self, s: Settings) -> None:
        super().__init__()
        self.s = s
        self.setWindowTitle(f"镜声 {__version__}")
        self.resize(560, 820)
        self.bridge = Bridge()
        self.lat_stats = LatencyStats()
        self.bridge.event.connect(self._on_event)
        self.engine = Engine(self.bridge.emit_event)
        self.setWindowIcon(app_icon())
        self.overlay = Overlay(s)
        self.overlay.on_hidden = self._overlay_hidden
        self.convo = ConversationWindow(s)
        self.convo.on_closed = self._convo_closed
        self._style_dlg = None  # the open 字幕样式 window (non-modal)
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
    NAV = (("同传", None), ("术语表", "_glossary"), ("字幕样式", "_style"), ("音色", "_voice"), ("在线模型", "_online"),
           ("会议记录", "_open_transcripts"), ("设置向导", "_wizard"), ("检查更新", "_check_updates"))

    def _build(self) -> None:
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._sidebar())

        content = QWidget(objectName="content")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(12)

        top = QHBoxLayout()
        top.setSpacing(12)
        top.addWidget(QLabel("同传", objectName="h1"))
        self.status = QLabel("● 已停止", objectName="status")
        top.addWidget(self.status)
        top.addStretch()
        self.mute_btn = QPushButton("静音")
        self.mute_btn.setCheckable(True)
        self.mute_btn.setMinimumHeight(40)
        self.mute_btn.toggled.connect(self.engine.set_muted)
        self.start_btn = QPushButton("开始", objectName="primary")
        self.start_btn.clicked.connect(self._toggle)
        top.addWidget(self.mute_btn)
        top.addWidget(self.start_btn)
        lay.addLayout(top)

        # model status banner: always tells whether models are ready / downloading / failed
        self.banner = QFrame(objectName="banner")
        bl = QHBoxLayout(self.banner)
        bl.setContentsMargins(14, 8, 14, 8)
        self.banner_text = QLabel()
        self.banner_bar = QProgressBar(maximum=1000)
        self.banner_bar.setMaximumHeight(8)
        self.banner_bar.setTextVisible(False)
        self.banner_btn = QPushButton("下载模型")
        self.banner_btn.clicked.connect(lambda: self.dm.start(self.s.quality))
        bl.addWidget(self.banner_text, 1)
        bl.addWidget(self.banner_bar, 1)
        bl.addWidget(self.banner_btn)
        lay.addWidget(self.banner)

        # settings cards on the left (scrolling on their own), the conversation on the right at full
        # height; a narrow window stacks them (_fit_layout)
        settings = QWidget(objectName="content")
        cl = QVBoxLayout(settings)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(12)

        g, h0 = card("语言")
        h = QHBoxLayout()
        self.my, self.their = QComboBox(), QComboBox()
        for c, lg in LANGS.items():
            self.my.addItem(lg.label, c)
            if lg.tts:
                self.their.addItem(lg.label, c)
        swap = QPushButton("⇄")
        swap.setFixedWidth(40)
        swap.setToolTip("交换两种语言")
        swap.clicked.connect(self._swap)
        h.addWidget(QLabel("我说", objectName="muted"))
        h.addWidget(self.my, 1)
        h.addWidget(swap)
        h.addWidget(QLabel("对方说", objectName="muted"))
        h.addWidget(self.their, 1)
        h0.addLayout(h)
        cl.addWidget(g)

        g, body = card()
        tr_ = QHBoxLayout()
        tr_.addWidget(QLabel("声音设备", objectName="cardTitle"))
        tr_.addStretch()
        self.test_btn = QPushButton("测试链路")
        self.test_btn.setToolTip("不用开会，检查对方能否听到翻译、字幕能否出来（翻译运行时不可用）")
        self.test_btn.clicked.connect(self._selftest)
        guide = QPushButton("会议软件设置")
        guide.clicked.connect(lambda: MeetingGuideDialog(self).exec())
        rb = QPushButton("刷新设备")
        rb.clicked.connect(self._load_devices)
        tr_.addWidget(self.test_btn)
        tr_.addWidget(guide)
        tr_.addWidget(rb)
        body.addLayout(tr_)
        f = QFormLayout()
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(6)
        self.mic, self.call, self.mon, self.src = QComboBox(), QComboBox(), QComboBox(), QComboBox()
        f.addRow(QLabel("我的麦克风", objectName="muted"), self.mic)
        f.addRow(QLabel("送进会议（虚拟麦克风）", objectName="muted"), self.call)
        f.addRow(QLabel("我自己监听（耳机）", objectName="muted"), self.mon)
        f.addRow(QLabel("对方声音来源", objectName="muted"), self.src)
        body.addLayout(f)
        self.dev_warn = QLabel(objectName="devWarn", wordWrap=True)  # problems with the current choice
        self.dev_warn.hide()
        body.addWidget(self.dev_warn)
        # macOS: the meeting-speaker multi-output still points at the old headphones
        self.multi_fix = QFrame(objectName="banner")
        mh_ = QHBoxLayout(self.multi_fix)
        mh_.setContentsMargins(12, 8, 8, 8)
        self.multi_fix_text = QLabel(wordWrap=True)
        self.multi_fix_btn = QPushButton()
        self.multi_fix_btn.clicked.connect(self._rebuild_multi_output)
        mh_.addWidget(self.multi_fix_text, 1)
        mh_.addWidget(self.multi_fix_btn)
        self.multi_fix.hide()
        body.addWidget(self.multi_fix)
        self._multi_target = ""
        cl.addWidget(g)

        g, v = card("功能")
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
        oh.setSpacing(8)
        self.quality = QComboBox()
        for i, q in enumerate(models.QUALITIES):
            self.quality.addItem(f"{models.QUALITY_LABEL[q]}（{models.llm_name(q)}）", q)
            tip = models.summary(q) + ("\n速度约快一倍，但翻译质量明显较低，适合 8 GB 内存或老电脑" if q == "turbo" else "")
            self.quality.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        self.display = QComboBox()
        self.display.addItem("图形界面", "ui")
        self.display.addItem("命令行风格", "cli")
        oh.addWidget(QLabel("模型", objectName="muted"))
        oh.addWidget(self.quality, 1)
        oh.addWidget(QLabel("显示方式", objectName="muted"))
        oh.addWidget(self.display, 1)
        v.addLayout(oh)
        oh = QHBoxLayout()
        self.llm_btn = QPushButton(objectName="link")  # 翻译：本地模型 / 翻译：在线 · DeepSeek
        self.llm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.llm_btn.setToolTip("用本地模型，或填 API key 改用在线模型翻译")
        self.llm_btn.clicked.connect(self._online)
        oh.addWidget(self.llm_btn)
        oh.addStretch()
        v.addLayout(oh)
        self._refresh_llm_label()
        oh = QHBoxLayout()  # voice options on their own row so the card never forces the window wider
        oh.setSpacing(8)
        from ..config import cuda_voice_ok, has_nvidia_gpu

        self.gpu_voice = QComboBox()
        self.gpu_voice.addItem("自动（RTX 30 系及以上）", "auto")
        self.gpu_voice.addItem("强制使用（旧卡会很慢）", "cuda")
        self.gpu_voice.addItem("不使用", "cpu")
        self.gpu_voice.setCurrentIndex(max(0, self.gpu_voice.findData(self.s.gpu_voice)))
        self.gpu_voice.currentIndexChanged.connect(
            lambda: (setattr(self.s, "gpu_voice", self.gpu_voice.currentData()), self.s.save(), self._refresh_banner(),
                     self.engine.reload_voice()))
        if not IS_MAC and has_nvidia_gpu():
            oh.addWidget(QLabel("GPU 音色", objectName="muted"))
            oh.addWidget(self.gpu_voice, 1)
        self.cpu_voice = QComboBox()
        self.cpu_voice.addItem("我的克隆音色（CPU，英文样本）", "clone")
        self.cpu_voice.addItem("标准音色", "standard")
        self.cpu_voice.setCurrentIndex(max(0, self.cpu_voice.findData(self.s.cpu_voice)))
        self.cpu_voice.currentIndexChanged.connect(
            lambda: (setattr(self.s, "cpu_voice", self.cpu_voice.currentData()), self.s.save(), self._refresh_banner(),
                     self.engine.reload_voice()))
        if not IS_MAC and not cuda_voice_ok(self.s.gpu_voice):  # Windows without a usable CUDA voice
            oh.addWidget(QLabel("音色", objectName="muted"))
            oh.addWidget(self.cpu_voice, 1)
        if oh.count():
            v.addLayout(oh)
        self.auto_upd = QCheckBox("启动时检查更新（离线时自动跳过）")
        self.auto_upd.setChecked(self.s.auto_check_update)
        self.auto_upd.toggled.connect(lambda on: (setattr(self.s, "auto_check_update", on), self.s.save()))
        v.addWidget(self.auto_upd)
        hk = self.s.mute_hotkey.replace("<ctrl>", "⌃" if IS_MAC else "Ctrl+").replace("<alt>", "⌥" if IS_MAC else "Alt+").replace("+", "").upper() if IS_MAC else self.s.mute_hotkey.replace("<", "").replace(">", "").title()
        v.addWidget(QLabel(f"静音快捷键：{hk}　（全局快捷键需要系统“辅助功能/输入监控”权限）", objectName="muted"))
        cl.addWidget(g)

        g, tv = card()
        th = QHBoxLayout()
        th.addWidget(QLabel("对话", objectName="cardTitle"))
        th.addSpacing(16)
        self.lv_me, self.lv_them = QProgressBar(maximum=100, textVisible=False), QProgressBar(maximum=100, textVisible=False)
        for w in (self.lv_me, self.lv_them):
            w.setFixedHeight(6)
            w.setMinimumWidth(80)
        th.addWidget(QLabel("我", objectName="muted"))
        th.addWidget(self.lv_me, 1)
        th.addSpacing(8)
        th.addWidget(QLabel("对方", objectName="muted"))
        th.addWidget(self.lv_them, 1)
        tv.addLayout(th)
        self.lat_label = QLabel(objectName="muted", wordWrap=True)  # rolling latency summary
        self.lat_label.hide()
        tv.addWidget(self.lat_label)
        self.log = QListWidget(objectName="log")
        self.log.setWordWrap(True)
        self.log.setResizeMode(QListWidget.ResizeMode.Adjust)  # rewrap rows when the column changes width
        self.log.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.log.setMinimumHeight(200)
        tv.addWidget(self.log, 1)
        cl.addStretch()

        scroll = QScrollArea(objectName="page")  # the settings scroll when the window is short
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(settings)
        self._settings_scroll, self._convo_card = scroll, g
        self._body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._body.setSpacing(16)
        self._body.addWidget(scroll, 5)
        self._body.addWidget(g, 6)
        lay.addLayout(self._body, 1)
        outer.addWidget(content, 1)
        self.setCentralWidget(root)
        self.resize(1320, 860)
        self.setMinimumSize(760, 560)
        self._fit_layout()
        self._center_on_screen()

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
        for w in (self.speak, self.subs, self.passthru, self.cont, self.ptt):
            w.toggled.connect(self._save)
        self.display.currentIndexChanged.connect(self._display_changed)
        for role, combo, _ in self._device_combos():
            combo.currentIndexChanged.connect(lambda _i, role=role: self._device_picked(role))

    CONVO_MIN_W = 420  # the conversation column needs at least this; narrower windows stack the two

    def resizeEvent(self, e) -> None:  # noqa: N802 — Qt override
        super().resizeEvent(e)
        if hasattr(self, "_body"):
            self._fit_layout()

    def showEvent(self, e) -> None:  # noqa: N802 — Qt override
        super().showEvent(e)
        # the settings' real width is known only once the style sheet has polished them
        QTimer.singleShot(0, self._fit_layout)

    def _fit_layout(self) -> None:
        """Settings keep the width their controls need (never clipped); the conversation takes the
        rest beside them, or goes below when that would leave it narrower than CONVO_MIN_W."""
        settings = self._settings_scroll.widget()
        need = settings.minimumSizeHint().width() + self._settings_scroll.verticalScrollBar().sizeHint().width() + 8
        room = self.centralWidget().width() - 176 - 56 - self._body.spacing()  # sidebar, page margins
        wide = room - need >= self.CONVO_MIN_W
        want = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self._body.direction() != want:
            self._body.setDirection(want)
        self._body.setStretch(0, 0 if wide else 3)
        self._body.setStretch(1, 1 if wide else 2)
        if wide:
            self._settings_scroll.setFixedWidth(need)
        else:
            self._settings_scroll.setMinimumWidth(0)
            self._settings_scroll.setMaximumWidth(16777215)
        self._convo_card.setMinimumHeight(0 if wide else 260)

    def _center_on_screen(self) -> None:
        """Open centred on the screen under the cursor (fit the window to it first)."""
        from PySide6.QtGui import QCursor

        scr = (QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()).availableGeometry()
        self.resize(min(self.width(), scr.width() - 40), min(self.height(), scr.height() - 40))
        geo = self.frameGeometry()
        geo.moveCenter(scr.center())
        self.move(max(scr.left(), geo.left()), max(scr.top(), geo.top()))

    def _sidebar(self) -> QFrame:
        side = QFrame(objectName="sidebar")
        side.setFixedWidth(176)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(4)
        brand = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(brand_icon().pixmap(36, 36))
        brand.addWidget(logo)
        bt = QVBoxLayout()
        bt.setSpacing(0)
        bt.addWidget(QLabel("镜声", objectName="brand"))
        bt.addWidget(QLabel(f"MirrorVoice · v{__version__}", objectName="brandSub"))
        brand.addLayout(bt)
        brand.addStretch()
        v.addLayout(brand)
        v.addSpacing(18)
        self.nav_main = None
        for text, fn in self.NAV:
            b = QPushButton(text, objectName="nav")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if fn is None:
                b.setCheckable(True)
                b.setChecked(True)
                self.nav_main = b
            else:
                b.clicked.connect(getattr(self, fn))
            v.addWidget(b)
        v.addStretch()
        return side

    def _check_updates(self) -> None:
        self.updates.check(manual=True)

    def _status_state(self, state: str) -> None:
        """'stopped' | 'busy' | 'running' — colours the status pill via the stylesheet."""
        self.status.setProperty("state", state)
        repolish(self.status)

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
        self._default_out = default_output_name()
        ctx = self._device_ctx(self.s.call_out, self.s.their_source)
        mics = [n for n in ins if not n.startswith("loopback:")]
        fill(self.mic, [("系统默认", "")] + [(n, n) for n in dh.ordered("mic", mics, ctx)], self.s.mic)
        fill(self.call, [(n, n) for n in dh.ordered("call", outs, ctx)], self.s.call_out)
        fill(self.mon, [("不监听", "")] + [(n, n) for n in dh.ordered("monitor", outs, ctx)], self.s.monitor)
        fill(self.src, [("不显示字幕", "")] + [(n, n) for n in dh.ordered("source", ins, ctx)], self.s.their_source)
        self._annotate_devices()
        self._check_multi_output()

    def _check_multi_output(self) -> None:
        """macOS: offer to point the meeting-speaker device at the headphones in use now."""
        if not IS_MAC or dh.kind(self.src.currentData() or "", "input") != "bh16":
            self.multi_fix.hide()
            return
        from ..virtual_audio import mac_multi_output_members

        target, why = dh.multi_output_fix(mac_multi_output_members(), self.mon.currentData() or "",
                                          getattr(self, "_default_out", ""))
        self._multi_target = target
        if target:
            self.multi_fix_text.setText("🎧 " + why)
            self.multi_fix_btn.setText(f"改成“{target}”")
        self.multi_fix.setVisible(bool(target))

    def _rebuild_multi_output(self) -> None:
        from ..virtual_audio import MULTI_OUTPUT_NAME, mac_rebuild_multi_output

        target = self._multi_target
        if not target:
            return
        ok, msg = mac_rebuild_multi_output(target)
        if ok:
            QMessageBox.information(self, "已更新会议扬声器",
                                    f"“{MULTI_OUTPUT_NAME}”现在会把对方的声音送到“{target}”和字幕。\n\n"
                                    "如果会议软件正在运行，请在它的设置里重新选一次扬声器。")
        else:
            QMessageBox.warning(self, "更新失败", msg)
        self._load_devices()

    def _device_ctx(self, call_out: str, their_source: str) -> "dh.Context":
        return dh.Context(call_out or "", their_source or "", getattr(self, "_default_out", ""))

    def _device_combos(self) -> list[tuple[str, QComboBox, str]]:
        return [("mic", self.mic, "我的麦克风"), ("call", self.call, "送进会议"),
                ("monitor", self.mon, "我自己监听"), ("source", self.src, "对方声音来源")]

    def _annotate_devices(self) -> None:
        """Tag every entry for the current combination and list the problems of the current choice."""
        ctx = self._device_ctx(self.call.currentData(), self.src.currentData())
        problems = []
        for role, combo, title in self._device_combos():
            for i in range(combo.count()):
                name = combo.itemData(i) or ""
                if not name:
                    continue
                h = dh.hint(role, name, ctx)
                text = dh.label(name, h)
                if combo.itemText(i).endswith("（未连接）"):
                    text += "（未连接）"
                combo.setItemText(i, text)
                combo.setItemData(i, h.warning or None, Qt.ItemDataRole.ToolTipRole)
            h = dh.hint(role, combo.currentData() or "", ctx)
            if h.rank >= dh.UNSUITED and h.warning:
                text = h.warning.replace("\n\n", " ")
                problems.append((h.rank, f"{'⚠️' if h.rank == dh.CONFLICT else 'ℹ️'} {title}：{text}"))
        problems.sort(key=lambda p: -p[0])
        self.dev_warn.setText("\n".join(p[1] for p in problems))
        self.dev_warn.setProperty("level", "conflict" if problems and problems[0][0] == dh.CONFLICT else "info")
        repolish(self.dev_warn)
        self.dev_warn.setVisible(bool(problems))

    def _device_picked(self, role: str) -> None:
        """The user picked a device: explain right away if it breaks the setup."""
        self._annotate_devices()
        if role in ("monitor", "source"):
            self._check_multi_output()
        combo, title = next((c, t) for r, c, t in self._device_combos() if r == role)
        ctx = self._device_ctx(self.call.currentData(), self.src.currentData())
        h = dh.hint(role, combo.currentData() or "", ctx)
        if h.rank >= dh.UNSUITED and h.warning:
            QMessageBox.warning(self, f"{title}：{dh.label(combo.currentData(), dh.Hint(h.rank))}", h.warning)

    # settings that only take effect when the engine (re)starts
    # settings the running engine picks up through engine.apply() (nothing needs stop / start any more)
    LIVE_KEYS = ("my_lang", "their_lang", "speak", "subtitles", "passthrough", "mode", "ptt_key", "quality")
    # devices switched while running (engine.set_*); the monitor was the only one before
    LIVE_DEVICES = (("mic", "set_mic", "我的麦克风"), ("call_out", "set_call_out", "送进会议"),
                    ("their_source", "set_their_source", "对方声音来源"))

    def _save(self) -> None:
        s = self.s
        before = {k: getattr(s, k) for k in self.LIVE_KEYS + ("monitor", "mic", "call_out", "their_source")}
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
                self.engine.set_monitor(s.monitor)
            for key, fn, title in self.LIVE_DEVICES:
                if before[key] != getattr(s, key):
                    self._switch_device(fn, title, getattr(s, key))
            changed = {k for k in self.LIVE_KEYS if before[k] != getattr(s, k)}
            if changed:
                self.engine.apply(changed)

    def _switch_device(self, fn: str, title: str, value: str) -> None:
        """Hot-switch one device on a worker thread (opening a device or a recognizer can take a
        second); the outcome shows in the conversation list like other notices."""
        engine = self.engine

        def work():
            try:
                name = getattr(engine, fn)(value)
                engine.emit("notice", text=f"{title}已切换为：{name or '关闭'}")
            except Exception as e:  # noqa: BLE001 — the old device keeps running
                engine.emit("notice", text=f"{title}切换失败，仍用原来的设备：{e}")
        threading.Thread(target=work, daemon=True, name="switch-device").start()

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
        self.start_btn.setProperty("running", True)
        repolish(self.start_btn)
        self.status.setText("● 启动中…")
        self._status_state("busy")
        self._check_multi_output()  # headphones may have changed since the panel opened
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
        self._floating_changed()

    def _convo_closed(self) -> None:
        if self.s.convo_open:
            self.s.convo_open = False
            self.s.save()
        self._floating_changed()

    def toggle_overlay_pin(self) -> None:
        """Tray / 字幕样式: show or hide the subtitle bar right now. Shown by hand = pinned (stays
        after stop); hidden by hand stays hidden until the next start or the next toggle."""
        if self.overlay.isVisible():
            self.overlay.hide()  # on_hidden clears the pin
        else:
            self.s.overlay_pinned = True
            self.s.save()
            self.overlay.show()
        self._floating_changed()

    def _overlay_hidden(self) -> None:
        if self.s.overlay_pinned:
            self.s.overlay_pinned = False
            self.s.save()
        self._floating_changed()

    def _floating_changed(self) -> None:
        """A floating window was shown or hidden (tray, its own ✕, 字幕样式): sync the toggles."""
        if hasattr(self, "tray"):
            self.tray.refresh()
        if self._style_dlg is not None:
            self._style_dlg.sync()

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
            self.status.setText(f"● 运行中 · {d['voice']}" + (f" · 在线翻译 {d['llm']}" if d.get("llm") else ""))
            self._status_state("running")
            # the subtitle bar is opened by hand (托盘 / 字幕样式) and remembered; never popped up on 开始
            self.tray.refresh()
        elif kind == "me":
            tag = "我·原声" if d["passthrough"] else "我"
            timing = f"{d['latency']:.1f}s" if d["passthrough"] else breakdown(d)
            self._add(f"{tag}  {d['src']}\n→ {d['tgt']}   ({timing})", "#1f6feb")
            if not d.get("failed"):
                self.lat_stats.add(d)
            self.lat_label.setText(self.lat_stats.summary("online" if self.s.llm_mode == "online" else self.s.quality))
            self.lat_label.setVisible(bool(self.lat_label.text()))
            self.convo.add("me", d["src"], d["tgt"], d["passthrough"])
        elif kind == "them":
            self._add(f"对方  {d['src']}\n→ {d['tgt']}   ({breakdown(d)})", "#8250df", replace_live=True)
            self.overlay.show_text(d["tgt"], d["src"] if d["tgt"] != d["src"] else "")
            self.convo.add("them", d["src"], d["tgt"])
        elif kind == "partial":
            if d["who"] == "them":  # online recognition: the sentence as it is being spoken
                self.overlay.show_partial(d["src"], d.get("tgt", ""))
                if d.get("tgt"):  # its translation so far: a provisional row the final one replaces
                    self._add(f"对方  {d['src']}\n→ {d['tgt']}", "#a98bd9", live=True)
        elif kind == "notice":
            self._add(f"提示  {d['text']}", "#8a8a8a")
        elif kind == "mute":
            self.mute_btn.blockSignals(True)
            self.mute_btn.setChecked(d["muted"])
            self.mute_btn.blockSignals(False)
            self.mute_btn.setText("已静音" if d["muted"] else "静音")
            self.tray.refresh()
        elif kind == "voice":
            self.status.setText(f"● 运行中 · {d['voice']}")
            self._status_state("running")
        elif kind == "ptt":
            self.status.setText("● 说话中…" if d["down"] else "● 运行中")
        elif kind == "error":
            self._refresh_banner()  # a damaged model was removed: offer to download it again
            QMessageBox.warning(self, "出错了", d["message"] + f"\n\n详细日志：{LOG_FILE}")
        elif kind == "stopped":
            self.start_btn.setText("开始")
            self.start_btn.setProperty("running", False)
            repolish(self.start_btn)
            self.status.setText("● 已停止")
            self._status_state("stopped")
            if not self.s.overlay_pinned:
                self.overlay.hide()
            self.tray.refresh()

    def _add(self, text: str, color: str, live: bool = False, replace_live: bool = False) -> None:
        """live: the other side's sentence still being translated — kept in one row that later
        updates and finally the finished translation (replace_live) overwrite."""
        it = getattr(self, "_live_item", None)
        if it is not None and (live or replace_live) and self.log.row(it) >= 0:
            it.setText(text)
            it.setForeground(QColor(color))
            self._live_item = None if replace_live else it
            self.log.scrollToBottom()
            return
        it = QListWidgetItem(text)
        it.setForeground(QColor(color))
        if live or replace_live:  # my own rows in between leave the provisional one where it is
            self._live_item = it if live else None
        self.log.addItem(it)
        if self.log.count() > 300:
            self.log.takeItem(0)
        self.log.scrollToBottom()

    def _selftest(self) -> None:
        if self.engine.running:
            QMessageBox.information(self, "请先停止", "测试链路需要用到同样的声音设备，请先点“停止”再测试。")
            return
        SelfTestDialog(self.s, self).exec()

    def _refresh_llm_label(self) -> None:
        from .online import asr_status_text, status_text

        self.llm_btn.setText(status_text(self.s) + asr_status_text(self.s) + "  ›")
        for i, q in enumerate(models.QUALITIES):  # sizes change: online translation downloads no LLM
            tip = models.summary(q) + ("\n速度约快一倍，但翻译质量明显较低，适合 8 GB 内存或老电脑" if q == "turbo" else "")
            self.quality.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)

    def _online(self) -> None:
        from .online import OnlineModelDialog

        self._save()
        before = (self.s.llm_mode, self.s.llm_provider, self.s.llm_model, self.s.llm_base_url, self.s.online_their,
                  self.s.asr_mode, self.s.asr_me, self.s.asr_them, self.s.asr_local)
        if not OnlineModelDialog(self.s, self).exec():
            return
        self._refresh_llm_label()
        if hasattr(self, "banner"):
            self._refresh_banner()
        after = (self.s.llm_mode, self.s.llm_provider, self.s.llm_model, self.s.llm_base_url, self.s.online_their,
                 self.s.asr_mode, self.s.asr_me, self.s.asr_them, self.s.asr_local)
        if before[:5] != after[:5] and self.engine.running:
            self.engine.apply({"llm"})  # translation switches live
        changed = before[5:] != after[5:]  # speech recognition: on the next start
        if changed and self.engine.running:
            self.status.setText("● 运行中 · 识别设置已保存，点“停止”再“开始”后生效")
            self._status_state("busy")

    def _glossary(self) -> None:
        if GlossaryDialog(self.s, self).exec():
            self.engine.reload_glossary()

    def _style(self) -> None:
        """Non-modal: a modal dialog blocked dragging and closing the bar / conversation window
        while it was open."""
        if self._style_dlg is not None:
            self._style_dlg.raise_()
            self._style_dlg.activateWindow()
            return
        self.overlay.show()  # preview
        d = StyleDialog(self.s, lambda: (self.overlay.apply_style(), self.convo.apply_style()), self,
                        toggle_bar=self.toggle_overlay_pin, toggle_convo=self.toggle_convo,
                        bar_visible=self.overlay.isVisible)
        d.setModal(False)
        d.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        d.finished.connect(self._style_closed)
        self._style_dlg = d
        d.show()

    def _style_closed(self, _result: int = 0) -> None:
        self._style_dlg = None
        if not self.engine.running and not self.s.overlay_pinned:
            self.overlay.hide()  # end of the preview

    def _voice(self) -> None:
        if VoiceDialog(self.s, self, on_speed=self.engine.set_voice_speed).exec():
            self.engine.reload_voice()  # new sample takes effect without 停止/开始

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
