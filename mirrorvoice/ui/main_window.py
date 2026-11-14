"""Control panel: start/stop, languages, devices, features, live transcript."""
from __future__ import annotations

import subprocess
import threading
import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QComboBox, QFormLayout, QFrame,
                               QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
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
from .icons import app_icon
from . import theme
from .logview import ROLE, ConvoDelegate, entry
from .mascot import Mascot
from .switch import Switch
from .theme import card, repolish
from .overlay import Overlay
from .tray import Tray
from .wizard import Wizard


class MainWindow(QMainWindow):
    def __init__(self, s: Settings) -> None:
        super().__init__()
        self.s = s
        self.setWindowTitle(f"翻了么 {__version__}")
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
        self._make_widgets()
        self.resize(1320, 860)
        self._arrange()
        self._center_on_screen()

        # load settings into widgets, save on any change
        s = self.s
        self.my.setCurrentIndex(self.my.findData(s.my_lang))
        self.their.setCurrentIndex(self.their.findData(s.their_lang))
        self.speak.setChecked(s.speak)
        self.subs.setChecked(s.subtitles)
        self.passthru.setChecked(s.passthrough)
        self.quiet.setChecked(s.quiet_gate)
        self.stream.setChecked(s.stream)
        (self.ptt if s.mode == "ptt" else self.cont).setChecked(True)
        self.ptt_key.setCurrentIndex(max(0, self.ptt_key.findData(s.ptt_key)))
        self.quality.setCurrentIndex(self.quality.findData(s.quality))
        self.display.setCurrentIndex(self.display.findData(s.display))
        for w in (self.my, self.their, self.ptt_key, self.quality, self.display, self.mic, self.call, self.mon, self.src):
            w.currentIndexChanged.connect(self._save)
        for w in (self.speak, self.subs, self.passthru, self.quiet, self.stream, self.cont, self.ptt):
            w.toggled.connect(self._save)
        self.display.currentIndexChanged.connect(self._display_changed)
        for role, combo, _ in self._device_combos():
            combo.currentIndexChanged.connect(lambda _i, role=role: self._device_picked(role))
        for combo in (self.mic, self.call):
            combo.currentIndexChanged.connect(self._refresh_chain)

    # ---- the pieces, made once
    def _make_widgets(self) -> None:
        """Everything that holds state or is talked to. _arrange() places these in the theme's
        layout, and again when the theme changes: only frames, rows and plain labels are made anew."""
        s = self.s
        self._attic = QWidget(self)  # where the pieces wait while a layout is rebuilt (or does not use them)
        self._attic.hide()
        self._atoms: list[QWidget] = []

        def keep(*ws):
            self._atoms.extend(ws)
            return ws[0] if len(ws) == 1 else ws

        # brand and navigation. The owl girl dozes while stopped, cheers on start, types / waves while people talk
        self.mascot = keep(Mascot(100, 120))
        self.mascot.setVisible(s.mascot)
        self.brand = keep(QLabel("翻了么", objectName="brand"))
        self.brand_sub = keep(QLabel(f"MirrorVoice · v{__version__}", objectName="brandSub"))
        self.nav_main = None
        self.nav = {}
        for text, fn in self.NAV:
            b = self.nav[fn] = keep(QPushButton(text, objectName="nav"))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if fn is None:
                b.setCheckable(True)
                b.setChecked(True)
                self.nav_main = b
            else:
                b.clicked.connect(getattr(self, fn))
        self.theme_box = keep(QComboBox())
        self.theme_box.setAccessibleName("界面风格")
        for t in theme.THEMES.values():
            self.theme_box.addItem(f"风格 · {t.name}", t.key)
        self.theme_box.setCurrentIndex(max(0, self.theme_box.findData(theme.current().key)))
        self.theme_box.setToolTip("静场：极简浅色 · 调音台：深色专业 · 对话：亲和圆润（气泡）")
        self.theme_box.currentIndexChanged.connect(self._use_theme)
        self.mascot_label = keep(QLabel("虚拟形象", objectName="sideOption"))
        self.mascot_switch = keep(Switch(s.mascot))
        self.mascot_switch.setToolTip("显示或隐藏虚拟形象（更新时的动画也跟着开关）")
        self.mascot_switch.toggled.connect(self._show_mascot)

        # the page's head
        self.h1 = keep(QLabel("同传", objectName="h1"))
        self.status = keep(QLabel("● 已停止", objectName="status"))
        self.mute_btn = keep(QPushButton("静音"))
        self.mute_btn.setCheckable(True)
        self.mute_btn.setMinimumHeight(40)
        self.mute_btn.toggled.connect(self.engine.set_muted)
        self.start_btn = keep(QPushButton("开始", objectName="primary"))
        self.start_btn.clicked.connect(self._toggle)

        # model status banner: always tells whether models are ready / downloading / failed
        self.banner = keep(QFrame(objectName="banner"))
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

        # languages
        self.my, self.their = keep(QComboBox(), QComboBox())
        for c, lg in LANGS.items():
            self.my.addItem(lg.label, c)
            if lg.tts:
                self.their.addItem(lg.label, c)
        self.swap_btn = keep(QPushButton("⇄"))
        self.swap_btn.setFixedWidth(40)
        self.swap_btn.setToolTip("交换两种语言")
        self.swap_btn.clicked.connect(self._swap)

        # sound devices
        self.test_btn = keep(QPushButton("测试链路"))
        self.test_btn.setToolTip("不用开会，检查对方能否听到翻译、字幕能否出来（翻译运行时不可用）")
        self.test_btn.clicked.connect(self._selftest)
        self.guide_btn = keep(QPushButton("会议软件设置"))
        self.guide_btn.clicked.connect(lambda: MeetingGuideDialog(self).exec())
        self.refresh_btn = keep(QPushButton("刷新设备"))
        self.refresh_btn.clicked.connect(self._load_devices)
        self.mic, self.call, self.mon, self.src = keep(QComboBox(), QComboBox(), QComboBox(), QComboBox())
        self.dev_warn = keep(QLabel(objectName="devWarn", wordWrap=True))  # problems with the current choice
        self.dev_warn.hide()
        # macOS: the meeting-speaker multi-output still points at the old headphones
        self.multi_fix = keep(QFrame(objectName="banner"))
        mh_ = QHBoxLayout(self.multi_fix)
        mh_.setContentsMargins(12, 8, 8, 8)
        self.multi_fix_text = QLabel(wordWrap=True)
        self.multi_fix_btn = QPushButton()
        self.multi_fix_btn.clicked.connect(self._rebuild_multi_output)
        mh_.addWidget(self.multi_fix_text, 1)
        mh_.addWidget(self.multi_fix_btn)
        self.multi_fix.hide()
        self._multi_target = ""

        # what it does
        self.speak = keep(QCheckBox("用我的声音说出翻译（送进会议）"))
        self.subs = keep(QCheckBox("对方说话显示翻译字幕"))
        self.passthru = keep(QCheckBox("我直接说对方的语言时，透传原声不翻译"))
        self.stream = keep(QCheckBox("边说边译（实验）：一句话说到逗号就先翻译，长句更快开口"))
        self.speak.setToolTip("翻译好的话用你的声音合成，送进会议软件的麦克风。")
        self.subs.setToolTip("听对方说话，把翻译显示成字幕。")
        self.passthru.setToolTip("你直接说对方的语言时，把你的原声送进会议，不再翻译一遍。")
        self.stream.setToolTip("说话途中每 0.4 秒识别一次，稳定的分句先送去翻译。长句子明显更快，"
                               "但译文会碎一些，已经念出的部分不能改。全部在本机完成。")
        self.quiet = keep(QCheckBox("比我平时说话轻很多的声音不翻译（旁人说话、杂音不会被念进会议）"))
        self.quiet.setToolTip("翻了么会记住你平时说话的音量；明显更轻的声音（约低 14 分贝以上）不翻译、不出声。"
                              "连续几段都很轻时，会认为是你离麦克风远了，自动按新的音量重新学习。")
        # (row wording, chip wording) — set by the layout in use; the tooltip always tells the whole of it
        self._fn_names = {self.speak: (self.speak.text(), "用我的声音出声"), self.subs: (self.subs.text(), "对方字幕"),
                          self.passthru: (self.passthru.text(), "透传原声"),
                          self.quiet: ("比我平时说话轻很多的声音不翻译", "轻声不翻译"),
                          self.stream: ("边说边译（实验）：说到逗号就先翻译", "边说边译")}
        for cb, (row, _chip) in self._fn_names.items():
            cb.setToolTip(f"{cb.text()}\n{cb.toolTip()}" if cb.toolTip() and cb.text() != row else cb.toolTip() or cb.text())
        self.cont, self.ptt = keep(QRadioButton("持续翻译"), QRadioButton("按住说话"))
        bg = QButtonGroup(self)
        bg.addButton(self.cont)
        bg.addButton(self.ptt)
        self.ptt_key = keep(QComboBox())
        for k, name in PTT_KEYS.items():
            self.ptt_key.addItem(name, k)
        self.quality = keep(QComboBox())
        for i, q in enumerate(models.QUALITIES):
            self.quality.addItem(f"{models.QUALITY_LABEL[q]}（{models.llm_name(q)}）", q)
            tip = models.summary(q) + ("\n速度约快一倍，但翻译质量明显较低，适合 8 GB 内存或老电脑" if q == "turbo" else "")
            self.quality.setItemData(i, tip, Qt.ItemDataRole.ToolTipRole)
        self.display = keep(QComboBox())
        self.display.addItem("图形界面", "ui")
        self.display.addItem("命令行风格", "cli")
        self.llm_btn = keep(QPushButton(objectName="link"))  # 翻译：本地模型 / 翻译：在线 · DeepSeek
        self.llm_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.llm_btn.setToolTip("用本地模型，或填 API key 改用在线模型翻译")
        self.llm_btn.clicked.connect(self._online)
        self._refresh_llm_label()
        self.gpu_voice = keep(QComboBox())
        self.gpu_voice.addItem("自动（RTX 30 系及以上）", "auto")
        self.gpu_voice.addItem("强制使用（旧卡会很慢）", "cuda")
        self.gpu_voice.addItem("不使用", "cpu")
        self.gpu_voice.setCurrentIndex(max(0, self.gpu_voice.findData(self.s.gpu_voice)))
        self.gpu_voice.currentIndexChanged.connect(
            lambda: (setattr(self.s, "gpu_voice", self.gpu_voice.currentData()), self.s.save(), self._refresh_banner(),
                     self.engine.reload_voice()))
        self.cpu_voice = keep(QComboBox())
        self.cpu_voice.addItem("我的克隆音色（CPU，英文样本）", "clone")
        self.cpu_voice.addItem("标准音色", "standard")
        self.cpu_voice.setCurrentIndex(max(0, self.cpu_voice.findData(self.s.cpu_voice)))
        self.cpu_voice.currentIndexChanged.connect(
            lambda: (setattr(self.s, "cpu_voice", self.cpu_voice.currentData()), self.s.save(), self._refresh_banner(),
                     self.engine.reload_voice()))
        self.auto_upd = keep(QCheckBox("启动时检查更新（离线时自动跳过）"))
        self.auto_upd.setChecked(self.s.auto_check_update)
        self.auto_upd.toggled.connect(lambda on: (setattr(self.s, "auto_check_update", on), self.s.save()))
        hk = self.s.mute_hotkey.replace("<ctrl>", "⌃" if IS_MAC else "Ctrl+").replace("<alt>", "⌥" if IS_MAC else "Alt+").replace("+", "").upper() if IS_MAC else self.s.mute_hotkey.replace("<", "").replace(">", "").title()
        self._hotkey_text = f"静音快捷键：{hk}　（全局快捷键需要系统“辅助功能/输入监控”权限）"

        # the conversation: one list of everything, and (调音台) one per side
        self.lv_me, self.lv_them = keep(QProgressBar(maximum=100, textVisible=False),
                                        QProgressBar(maximum=100, textVisible=False))
        for w in (self.lv_me, self.lv_them):
            w.setFixedHeight(6)
            w.setMinimumWidth(80)
        self.lat_label = keep(QLabel(objectName="muted", wordWrap=True))  # rolling latency summary
        self.lat_label.hide()
        self.log, self.log_me, self.log_them = keep(*(self._convo_list() for _ in range(3)))
        # 调音台: where the time of a sentence goes, stage by stage
        self.chain = keep(QFrame(objectName="chain"))
        ch = QHBoxLayout(self.chain)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.setSpacing(8)
        self._chain_vals: dict[str, QLabel] = {}
        for key, caption, stretch in (("mic", "麦克风", 3), ("asr", "识别", 2), ("tr", "翻译", 2), ("voice", "发声", 2),
                                      ("out", "送进会议", 3), ("total", "说完到出声", 3)):
            cell = QFrame(objectName="cellOn" if key == "total" else "cell")
            cv = QVBoxLayout(cell)
            cv.setContentsMargins(12, 8, 12, 8)
            cv.setSpacing(2)
            cv.addWidget(QLabel(caption, objectName="cellCaption"))
            val = self._chain_vals[key] = QLabel("—", objectName="cellValue")
            cv.addWidget(val)
            ch.addWidget(cell, stretch)

    def _convo_list(self) -> QListWidget:
        lst = QListWidget(objectName="log")
        lst.setWordWrap(True)
        lst.setResizeMode(QListWidget.ResizeMode.Adjust)  # rewrap rows when the column changes width
        lst.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        lst.setItemDelegate(ConvoDelegate(lst))  # rows, cards or bubbles: the theme's choice
        lst.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        lst.setMinimumHeight(200)
        return lst

    def _refresh_chain(self, *_) -> None:
        """调音台's strip: the devices at both ends and the average time of each stage between."""
        if not hasattr(self, "_chain_vals"):
            return
        avg = self.lat_stats.averages()
        plain = lambda combo: combo.currentText().split("（")[0] or "系统默认"  # noqa: E731
        show = {"mic": plain(self.mic), "out": plain(self.call)}
        for key, stage in (("asr", "asr_s"), ("tr", "translate_s"), ("voice", "voice_s"), ("total", "latency")):
            show[key] = f"{avg[stage]:.1f} s" if stage in avg else "—"
        for key, text in show.items():
            self._chain_vals[key].setText(text)

    # ---- the layouts: the same pieces, placed the theme's way
    LAYOUTS = {"quiet": "_lay_quiet", "studio": "_lay_studio", "chat": "_lay_chat"}
    MIN_SIZE = {"quiet": (760, 560), "studio": (980, 600), "chat": (900, 600)}

    def _arrange(self) -> None:
        key = theme.current().key if theme.current().key in self.LAYOUTS else theme.DEFAULT
        self._fn_switches: dict[QCheckBox, Switch] = {}
        for w in self._atoms:  # out of the old frames before those are thrown away
            w.setParent(self._attic)
        root = getattr(self, self.LAYOUTS[key])()
        old = self.takeCentralWidget()
        self.setCentralWidget(root)
        if old is not None:
            old.deleteLater()
        self._layout_key = key
        if key != "chat":
            self._nav_icons(False)
        self.setMinimumSize(*self.MIN_SIZE[key])
        self._refresh_chain()
        self._fit_layout()

    def _nav_icons(self, on: bool) -> None:
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QIcon

        from .icons import nav_icon

        t = theme.current()
        for fn, b in self.nav.items():
            b.setIcon(nav_icon(fn, t.nav_text, t.nav_on_fg) if on else QIcon())
            b.setIconSize(QSize(20, 20))

    def _lang_row(self, labels: bool = True, title: bool = False) -> QHBoxLayout:
        """title: the two languages large, as the page's heading (静场)."""
        h = QHBoxLayout()
        for combo in (self.my, self.their):
            combo.setProperty("look", "title" if title else "")
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents if title
                                      else QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
            repolish(combo)
        if title:
            h.setSpacing(6)
            h.addWidget(self.my)
            h.addWidget(self.swap_btn)
            h.addWidget(self.their)
            return h
        if labels:
            h.addWidget(QLabel("我说", objectName="muted"))
        h.addWidget(self.my, 1)
        h.addWidget(self.swap_btn)
        if labels:
            h.addWidget(QLabel("对方说", objectName="muted"))
        h.addWidget(self.their, 1)
        return h

    def _card_lang(self) -> QFrame:
        g, body = card("语言")
        body.addLayout(self._lang_row())
        return g

    def _card_devices(self, title: str = "声音设备", plain: bool = False) -> QFrame:
        """plain: the wording of 对话 — what each line is for, not what it is called."""
        g, body = card()
        tr_ = QHBoxLayout()
        tr_.addWidget(QLabel(title, objectName="cardTitle"))
        tr_.addStretch()
        tr_.addWidget(self.test_btn)
        tr_.addWidget(self.guide_btn)
        tr_.addWidget(self.refresh_btn)
        body.addLayout(tr_)
        f = QFormLayout()
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(6)
        names = ("我对着说", "翻译送到会议", "我自己也听", "从这里听对方") if plain else \
            ("我的麦克风", "送进会议（虚拟麦克风）", "我自己监听（耳机）", "对方声音来源")
        for name, combo in zip(names, (self.mic, self.call, self.mon, self.src)):
            f.addRow(QLabel(name, objectName="muted"), combo)
        body.addLayout(f)
        body.addWidget(self.dev_warn)
        body.addWidget(self.multi_fix)
        return g

    def _card_functions(self, style: str = "switches") -> QFrame:
        """style: 'switches' — a line of words and a sliding switch each (静场); 'chips' — short names
        to click, lit while on (调音台, 对话). The check boxes themselves hold the state either way."""
        from ..config import cuda_voice_ok, has_nvidia_gpu

        g, v = card("功能")
        boxes = (self.speak, self.subs, self.passthru, self.quiet, self.stream)
        if style == "chips":
            grid = QGridLayout()
            grid.setSpacing(8)
            for i, cb in enumerate(boxes):
                cb.setText(self._fn_names[cb][1])
                cb.setProperty("look", "chip")
                cb.setCursor(Qt.CursorShape.PointingHandCursor)
                repolish(cb)
                grid.addWidget(cb, i // 3, i % 3)
            v.addLayout(grid)
        else:
            for cb in boxes:
                cb.setProperty("look", "")
                cb.setText(self._fn_names[cb][0])
                row = QHBoxLayout()
                row.setSpacing(12)
                name = QLabel(self._fn_names[cb][0], objectName="optionText", wordWrap=True)
                name.setToolTip(cb.toolTip())
                sw = Switch(cb.isChecked())
                sw.setAccessibleName(self._fn_names[cb][0])
                sw.setToolTip(cb.toolTip())
                sw.toggled.connect(cb.setChecked)   # the box is the state (saved, read by the engine);
                cb.toggled.connect(sw.setChecked)   # the switch is how it is shown here
                row.addWidget(name, 1)
                row.addWidget(sw)
                v.addLayout(row)
                self._fn_switches[cb] = sw
        mh = QHBoxLayout()
        mh.setSpacing(10)
        seg = QFrame(objectName="seg")  # one control with two halves, the chosen one raised
        sh = QHBoxLayout(seg)
        sh.setContentsMargins(3, 3, 3, 3)
        sh.setSpacing(2)
        for rb in (self.cont, self.ptt):
            rb.setProperty("look", "seg")
            rb.setCursor(Qt.CursorShape.PointingHandCursor)
            sh.addWidget(rb)
        mh.addWidget(seg)
        mh.addWidget(self.ptt_key)
        mh.addStretch()
        v.addLayout(mh)
        oh = QHBoxLayout()
        oh.setSpacing(8)
        oh.addWidget(QLabel("模型", objectName="muted"))
        oh.addWidget(self.quality, 1)
        oh.addWidget(QLabel("显示方式", objectName="muted"))
        oh.addWidget(self.display, 1)
        v.addLayout(oh)
        oh = QHBoxLayout()
        oh.addWidget(self.llm_btn)
        oh.addStretch()
        v.addLayout(oh)
        oh = QHBoxLayout()  # voice options on their own row so the card never forces the window wider
        oh.setSpacing(8)
        if not IS_MAC and has_nvidia_gpu():
            oh.addWidget(QLabel("GPU 音色", objectName="muted"))
            oh.addWidget(self.gpu_voice, 1)
        if not IS_MAC and not cuda_voice_ok(self.s.gpu_voice):  # Windows without a usable CUDA voice
            oh.addWidget(QLabel("音色", objectName="muted"))
            oh.addWidget(self.cpu_voice, 1)
        if oh.count():
            v.addLayout(oh)
        v.addWidget(self.auto_upd)
        v.addWidget(QLabel(self._hotkey_text, objectName="muted"))
        return g

    def _settings_column(self, *cards: QWidget) -> QScrollArea:
        """The settings cards, scrolling on their own when the window is short."""
        settings = QWidget(objectName="content")
        cl = QVBoxLayout(settings)
        cl.setContentsMargins(0, 0, 4, 0)
        cl.setSpacing(12)
        for c in cards:
            if c is self.mascot:
                cl.addWidget(c, 0, Qt.AlignmentFlag.AlignHCenter)
            else:
                cl.addWidget(c)
        cl.addStretch()
        scroll = QScrollArea(objectName="page")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(settings)
        self._settings_scroll = scroll
        return scroll

    def _meter(self, name: str, bar: QProgressBar) -> QHBoxLayout:
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(QLabel(name, objectName="muted"))
        h.addWidget(bar, 1)
        return h

    def _convo(self, meters: bool = True) -> QFrame:
        """The conversation card: (title, both levels, the latency line) and the list of everything."""
        g, tv = card()
        if meters:
            th = QHBoxLayout()
            th.addWidget(QLabel("对话", objectName="cardTitle"))
            th.addSpacing(16)
            th.addLayout(self._meter("我", self.lv_me), 1)
            th.addSpacing(8)
            th.addLayout(self._meter("对方", self.lv_them), 1)
            tv.addLayout(th)
            tv.addWidget(self.lat_label)
        tv.addWidget(self.log, 1)
        return g

    def _two_columns(self, into: QVBoxLayout | QHBoxLayout, convo: QWidget, settings: QScrollArea, chrome: int) -> None:
        """The conversation with the settings beside it — or below it in a narrow window
        (_fit_layout). chrome: the width the layout spends on everything else."""
        self._convo_card, self._chrome_w = convo, chrome
        self._body = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._body.setSpacing(16)
        self._body.addWidget(convo, 6)
        self._body.addWidget(settings, 5)
        into.addLayout(self._body, 1)

    def _side_row(self, *ws: QWidget, margins=(14, 0, 4, 0)) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(*margins)
        for w in ws:
            if w is None:
                row.addStretch(1)
            else:
                row.addWidget(w, 1 if isinstance(w, QComboBox) else 0)
        return row

    def _lay_quiet(self) -> QWidget:
        """静场: the navigation at the left, the conversation in the middle, the settings at the right."""
        root = QWidget()
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = QFrame(objectName="sidebar")
        side.setFixedWidth(176)
        v = QVBoxLayout(side)
        v.setContentsMargins(14, 18, 14, 14)
        v.setSpacing(4)
        v.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignHCenter)
        for lbl in (self.brand, self.brand_sub):
            lbl.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            v.addWidget(lbl)
        v.addSpacing(14)
        for b in self.nav.values():
            v.addWidget(b)
        v.addStretch()
        v.addLayout(self._side_row(self.theme_box, margins=(4, 0, 4, 6)))
        v.addLayout(self._side_row(self.mascot_label, None, self.mascot_switch))
        outer.addWidget(side)

        content = QWidget(objectName="content")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(28, 20, 28, 20)
        lay.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(12)
        top.addLayout(self._lang_row(title=True))  # what is being interpreted is the page's title
        top.addStretch()
        top.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.mute_btn)
        top.addWidget(self.start_btn)
        lay.addLayout(top)
        lay.addWidget(self.banner)
        self._two_columns(lay, self._convo(),
                          self._settings_column(self._card_devices(), self._card_functions()), chrome=176 + 56)
        outer.addWidget(content, 1)
        return root

    def _lay_studio(self) -> QWidget:
        """调音台: everything across the top, the time of each stage under it, then my side and
        theirs next to each other."""
        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        bar = QFrame(objectName="topbar")
        h = QHBoxLayout(bar)
        h.setContentsMargins(20, 8, 20, 8)
        h.setSpacing(4)
        self.brand.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        h.addWidget(self.brand)
        h.addSpacing(14)
        tail = ("_wizard", "_check_updates")  # (these two go to the foot: the bar is for the pages)
        for fn, b in self.nav.items():
            if fn not in tail:
                h.addWidget(b)
        h.addStretch()
        h.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        h.addSpacing(8)
        h.addWidget(self.mute_btn)
        h.addWidget(self.start_btn)
        v.addWidget(bar)

        content = QWidget(objectName="content")
        lay = QVBoxLayout(content)
        lay.setContentsMargins(20, 14, 20, 10)
        lay.setSpacing(12)
        lay.addWidget(self.chain)
        lay.addWidget(self.banner)
        both = QWidget(objectName="content")
        bh = QHBoxLayout(both)
        bh.setContentsMargins(0, 0, 0, 0)
        bh.setSpacing(12)
        for title, bar_, lst in (("我 → 会议", self.lv_me, self.log_me), ("对方 → 字幕", self.lv_them, self.log_them)):
            g, tv = card()
            th = QHBoxLayout()
            th.addWidget(QLabel(title, objectName="cardTitle"))
            th.addSpacing(12)
            th.addWidget(bar_, 1)
            tv.addLayout(th)
            tv.addWidget(lst, 1)
            bh.addWidget(g, 1)
        self._two_columns(lay, both, self._settings_column(self._card_lang(), self._card_devices(), self._card_functions("chips"),
                                                           self.mascot), chrome=40)  # (she sits under the settings: above, she pushed them down)
        foot = QHBoxLayout()
        foot.setSpacing(8)
        foot.addWidget(self.lat_label, 1)
        foot.addStretch()
        self.brand_sub.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        foot.addWidget(self.brand_sub)
        for fn in tail:
            foot.addWidget(self.nav[fn])
        foot.addWidget(self.theme_box)
        foot.addWidget(self.mascot_label)
        foot.addWidget(self.mascot_switch)
        lay.addLayout(foot)
        v.addWidget(content, 1)
        return root

    def _lay_chat(self) -> QWidget:
        """对话: the two languages at the top, the conversation in the middle with the controls
        docked under it, the navigation as a rail at the left."""
        root = QWidget(objectName="content")
        v = QVBoxLayout(root)
        v.setContentsMargins(20, 14, 20, 16)
        v.setSpacing(12)
        top = QHBoxLayout()
        top.setSpacing(10)
        self.brand.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.brand_sub.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.brand)
        top.addWidget(self.brand_sub)
        top.addStretch(1)
        pill = QFrame(objectName="card")
        ph = self._lang_row(labels=False)
        ph.setContentsMargins(8, 6, 8, 6)
        pill.setLayout(ph)
        pill.setMinimumWidth(380)
        top.addWidget(pill)
        top.addStretch(1)
        top.addWidget(self.status, 0, Qt.AlignmentFlag.AlignVCenter)
        v.addLayout(top)
        v.addWidget(self.banner)

        row = QHBoxLayout()
        row.setSpacing(16)
        rail = QFrame(objectName="rail")
        rail.setFixedWidth(128)
        rv = QVBoxLayout(rail)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        rv.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignHCenter)
        self._nav_icons(True)
        for b in self.nav.values():
            rv.addWidget(b)
        rv.addStretch()
        rv.addWidget(self.theme_box)
        rv.addLayout(self._side_row(self.mascot_label, None, self.mascot_switch, margins=(8, 0, 4, 0)))
        row.addWidget(rail)

        mid = QWidget(objectName="content")
        mv = QVBoxLayout(mid)
        mv.setContentsMargins(0, 0, 0, 0)
        mv.setSpacing(12)
        mv.addWidget(self._convo(meters=False), 1)
        dock, dv = card()
        dh = QHBoxLayout()
        dh.setSpacing(14)
        dh.addLayout(self._meter("我", self.lv_me), 1)
        dh.addWidget(self.mute_btn)
        dh.addWidget(self.start_btn)
        dh.addLayout(self._meter("对方", self.lv_them), 1)
        dv.addLayout(dh)
        dv.addWidget(self.lat_label)
        mv.addWidget(dock)
        self._two_columns(row, mid, self._settings_column(self._card_devices("声音怎么走", plain=True),
                                                          self._card_functions("chips")), chrome=40 + 128 + 16)
        v.addLayout(row, 1)
        return root

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
        # (the window's width: a layout just put in place has not been given its size yet)
        room = self.width() - self._chrome_w - self._body.spacing()  # navigation, page margins
        wide = room - need >= self.CONVO_MIN_W
        want = QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
        if self._body.direction() != want:
            self._body.setDirection(want)
        self._body.setStretch(0, 1 if wide else 2)  # the conversation, first; then the settings
        self._body.setStretch(1, 0 if wide else 3)
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

    def _use_theme(self, *_) -> None:
        key = self.theme_box.currentData()
        theme.use(QApplication.instance(), key)
        self.s.theme = key
        self.s.save()
        if getattr(self, "_layout_key", key) != key:
            self._arrange()
        for lst in (self.log, self.log_me, self.log_them):  # laid out again: a bubble is not as tall as a row
            lst.doItemsLayout()
            lst.scrollToBottom()

    def _show_mascot(self, on: bool) -> None:
        self.mascot.setVisible(on)
        self.s.mascot = on
        self.s.save()

    def _check_updates(self) -> None:
        self.updates.check(manual=True)

    def _status_state(self, state: str) -> None:
        """'stopped' | 'busy' | 'running' — colours the status pill via the stylesheet."""
        self.status.setProperty("state", state)
        repolish(self.status)
        if hasattr(self, "mascot"):
            if state == "busy" and self.engine.running and self.mascot.state == "running":
                state = "running"  # a notice while translating: the headphones stay on
            self.mascot.set_state(state)

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
        from ..virtual_audio import mac_multi_output_state

        state, members = mac_multi_output_state()
        self._multi_missing = state == "missing"
        target, why = dh.multi_output_fix(members, self.mon.currentData() or "",
                                          getattr(self, "_default_out", ""), missing=self._multi_missing)
        self._multi_target = target
        if target:
            self.multi_fix_text.setText("🎧 " + why)
            self.multi_fix_btn.setText(f"创建（用“{target}”听对方）" if self._multi_missing else f"改成“{target}”")
        self.multi_fix.setVisible(bool(target))

    def _rebuild_multi_output(self) -> None:
        from ..virtual_audio import MULTI_OUTPUT_NAME, mac_rebuild_multi_output

        target = self._multi_target
        if not target:
            return
        created = getattr(self, "_multi_missing", False)
        ok, msg = mac_rebuild_multi_output(target)
        if ok and created:
            QMessageBox.information(self, "已创建会议扬声器",
                                    f"“{MULTI_OUTPUT_NAME}”会把对方的声音同时送到“{target}”和字幕。\n\n"
                                    "下一步：在会议软件的扬声器里选它（Teams 可以在“会议软件设置”里一键设置）。")
        elif ok:
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
    LIVE_KEYS = ("my_lang", "their_lang", "speak", "subtitles", "passthrough", "stream", "mode", "ptt_key",
                 "quality")
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
        s.stream, s.quiet_gate = self.stream.isChecked(), self.quiet.isChecked()
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
            self.mascot.set_level(d["rms"], d["who"])
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
            took = f"{d['latency']:.1f}s"
            self._add(f"{tag}  {d['src']}\n→ {d['tgt']}   ({timing})", "me",
                      entry("me_raw", "原声透传", d["src"], took) if d["passthrough"]
                      else entry("me", d["src"], d["tgt"], took), tip=timing)
            if not d.get("failed"):
                self.lat_stats.add(d)
            self.lat_label.setText(self.lat_stats.summary("online" if self.s.llm_mode == "online" else self.s.quality))
            self.lat_label.setVisible(bool(self.lat_label.text()))
            self._refresh_chain()
            self.convo.add("me", d["src"], d["tgt"], d["passthrough"])
        elif kind == "them":
            same = d["tgt"] == d["src"]  # they spoke my language: nothing to translate
            took = f"{d['latency']:.1f}s" if d.get("latency") else ""
            self._add(f"对方  {d['src']}\n→ {d['tgt']}   ({breakdown(d)})", "them",
                      entry("them", "" if same else d["src"], d["tgt"], took), replace_live=True, tip=breakdown(d))
            self.overlay.show_text(d["tgt"], d["src"] if d["tgt"] != d["src"] else "")
            self.convo.add("them", d["src"], d["tgt"])
        elif kind == "partial":
            if d["who"] == "them":  # online recognition: the sentence as it is being spoken
                self.overlay.show_partial(d["src"], d.get("tgt", ""))
                if d.get("tgt"):  # its translation so far: a provisional row the final one replaces
                    self._add(f"对方  {d['src']}\n→ {d['tgt']}", "live", entry("them", d["src"], d["tgt"], live=True),
                              live=True)
        elif kind == "notice":
            self._add(f"提示  {d['text']}", "note", entry("note", f"提示  {d['text']}"))
        elif kind == "mute":
            self.mascot.set_muted(d["muted"])
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

    def _add(self, text: str, kind: str, data: dict | None = None, live: bool = False,
             replace_live: bool = False, tip: str = "") -> None:
        """kind: me | them | live | note. data: what the row is made of, for the theme's way of
        drawing it (logview.entry). live: the other side's sentence still being translated — kept
        in one row that later updates and finally the finished translation (replace_live) overwrite.
        Every sentence goes into the list of everything and into its side's own list (调音台)."""
        t = theme.current()
        color = {"me": t.me, "them": t.them, "live": t.live}.get(kind, t.text_2)
        if not hasattr(self, "_live_items"):
            self._live_items: dict[int, QListWidgetItem | None] = {}
        for lst in (self.log, self.log_me if kind in ("me", "note") else self.log_them):
            it = self._live_items.get(id(lst))
            if it is not None and (live or replace_live) and lst.row(it) >= 0:
                it.setText(text)
                it.setData(ROLE, data)
                it.setToolTip(tip)
                it.setForeground(QColor(color))
                self._live_items[id(lst)] = None if replace_live else it
                lst.scrollToBottom()
                continue
            it = QListWidgetItem(text)
            it.setData(ROLE, data)
            it.setToolTip(tip)
            it.setForeground(QColor(color))
            if live or replace_live:  # my own rows in between leave the provisional one where it is
                self._live_items[id(lst)] = it if live else None
            lst.addItem(it)
            if lst.count() > 300:
                lst.takeItem(0)
            lst.scrollToBottom()

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
        d = StyleDialog(self.s, lambda: (self.overlay.apply_style(), self.convo.apply_style(),
                                         self.overlay.apply_sharing(), self.convo.apply_sharing()), self,
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
