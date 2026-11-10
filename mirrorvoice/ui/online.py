"""在线模型: translate with an online OpenAI-compatible API instead of (or besides) the local model.

Provider presets, the API key (kept in the OS keychain, never in settings.json), the model with a
"获取模型列表" button, a "测试" button that translates one sentence and shows how long it took, and
a one-time confirmation of what online translation sends out.
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QRadioButton, QTabWidget, QVBoxLayout,
                               QWidget)

from .. import keystore, models, providers
from ..config import IS_MAC, Settings
from ..online_llm import OnlineChat, TranslateError, list_models
from ..translate import Translator

TEST_SENTENCE = "今天的会议我们先讨论预算，然后再看上线时间。"


class _Relay(QObject):
    """Results from worker threads back to the dialog's thread."""
    done = Signal(str, object)  # (what, result or TranslateError)


def consent_text(provider_name: str, their: bool) -> str:
    items = ["你说的话（语音识别后的文字）", "会议背景，以及这句话用到的术语表词条"]
    if their:
        items.insert(1, "对方说的话（语音识别后的文字，用于翻译字幕）")
    return (f"开启在线翻译后，下面的内容会发送给「{provider_name}」：\n\n" + "\n".join("• " + i for i in items)
            + "\n\n声音本身不会上传，API key 只保存在这台电脑上。服务商如何保存这些内容，以它的隐私政策为准。")


def status_text(s: Settings) -> str:
    """'翻译：本地' / '翻译：在线 · DeepSeek · deepseek-chat' for the control panel."""
    if s.llm_mode != "online":
        return "翻译：本地模型"
    return "翻译：在线 · " + providers.label(s.llm_provider, s.llm_model or providers.get(s.llm_provider).default_model)


def asr_status_text(s: Settings) -> str:
    """' · 识别：在线' when online recognition is on, else ''."""
    if s.asr_mode != "online":
        return {"qwen3": " · 识别：Qwen3-ASR", "qwen3_them": " · 字幕识别：Qwen3-ASR"}.get(s.asr_local, "") if IS_MAC else ""
    sides = "、".join(w for w, on in (("我", s.asr_me), ("对方", s.asr_them)) if on)
    return f" · 识别：在线（{sides}）"


class OnlineModelDialog(QDialog):
    def __init__(self, s: Settings, parent=None) -> None:
        super().__init__(parent)
        self.s = s
        self.setWindowTitle("在线模型")
        self.resize(620, 0)
        self._typed: dict[str, str] = {}  # keys typed in this dialog, per provider (saved on 保存)
        self._busy = False
        self.relay = _Relay()
        self.relay.done.connect(self._done)

        outer = QVBoxLayout(self)
        self.tabs = QTabWidget()
        outer.addWidget(self.tabs)
        page = QWidget()
        lay = QVBoxLayout(page)
        self.tabs.addTab(page, "翻译")
        lay.addWidget(QLabel("<b>翻译用哪个模型</b>"))
        self.rb_local = QRadioButton(f"本地模型（离线运行，需要下载 {models.llm_name(s.quality)}）")
        self.rb_online = QRadioButton("在线模型（填服务商的 API key，不用下载翻译模型，翻译更准）")
        grp = QButtonGroup(self)
        grp.addButton(self.rb_local)
        grp.addButton(self.rb_online)
        (self.rb_online if s.llm_mode == "online" else self.rb_local).setChecked(True)
        lay.addWidget(self.rb_local)
        lay.addWidget(self.rb_online)

        self.box = QWidget()
        f = QFormLayout(self.box)
        f.setContentsMargins(22, 6, 0, 0)
        self.provider = QComboBox()
        for p in providers.PRESETS.values():
            self.provider.addItem(p.name, p.id)
        self.provider.setCurrentIndex(max(0, self.provider.findData(s.llm_provider)))
        self.key_link = QPushButton("获取 API key", objectName="link")
        self.key_link.setCursor(Qt.CursorShape.PointingHandCursor)
        self.key_link.clicked.connect(self._open_key_page)
        row = QHBoxLayout()
        row.addWidget(self.provider, 1)
        row.addWidget(self.key_link)
        f.addRow("服务商", row)

        self.url = QLineEdit(s.llm_base_url)
        self.url.setPlaceholderText("例如 http://localhost:11434/v1（Ollama）或 https://api.example.com/v1")
        self.url_label = QLabel("服务地址")
        f.addRow(self.url_label, self.url)
        self.url_hint = QLabel(objectName="muted")
        f.addRow("", self.url_hint)

        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.textEdited.connect(lambda t: self._typed.__setitem__(self._pid(), t.strip()))
        show = QPushButton("显示")
        show.setCheckable(True)
        show.toggled.connect(lambda on: (self.key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password),
                                         show.setText("隐藏" if on else "显示")))
        row = QHBoxLayout()
        row.addWidget(self.key, 1)
        row.addWidget(show)
        f.addRow("API key", row)
        self.key_hint = QLabel(objectName="muted")
        f.addRow("", self.key_hint)

        self.model = QComboBox(editable=True)
        self.model.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.model.currentTextChanged.connect(self._model_changed)
        self.fetch = QPushButton("获取模型列表")
        self.fetch.clicked.connect(self._fetch_models)
        row = QHBoxLayout()
        row.addWidget(self.model, 1)
        row.addWidget(self.fetch)
        f.addRow("模型", row)
        self.model_hint = QLabel(objectName="muted", wordWrap=True)
        f.addRow("", self.model_hint)

        self.their = QCheckBox("对方说的话也用在线翻译（会把对方的发言文字发给服务商）", checked=s.online_their)
        self.their.toggled.connect(self._refresh_hints)
        f.addRow("", self.their)
        self.their_hint = QLabel(objectName="muted", wordWrap=True)
        f.addRow("", self.their_hint)

        self.test_btn = QPushButton("测试")
        self.test_btn.clicked.connect(self._test)
        self.test_out = QLabel(wordWrap=True)
        self.test_out.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row = QHBoxLayout()
        row.addWidget(self.test_btn)
        row.addWidget(self.test_out, 1)
        f.addRow("", row)
        lay.addWidget(self.box)
        lay.addStretch()
        self.tabs.addTab(self._asr_page(), "语音识别")
        self.tabs.currentChanged.connect(self._fit_height)

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

        self.provider.currentIndexChanged.connect(self._provider_changed)
        self.rb_online.toggled.connect(self._mode_changed)
        self.url.textChanged.connect(self._refresh_hints)
        self._provider_changed(initial=True)
        self._mode_changed()

    def _asr_page(self) -> QWidget:
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.addWidget(QLabel("<b>语音识别用哪个</b>"))
        self.asr_local = QRadioButton("本机识别（SenseVoice，离线运行）")
        self.asr_online = QRadioButton("在线流式识别（阿里云百炼 Fun-ASR：字幕边说边出，长句按句号自动分段）")
        grp = QButtonGroup(self)
        grp.addButton(self.asr_local)
        grp.addButton(self.asr_online)
        (self.asr_online if self.s.asr_mode == "online" else self.asr_local).setChecked(True)
        lay.addWidget(self.asr_local)
        self.local_model = QComboBox()
        self.local_model.addItem("SenseVoice（快，占内存少）", "sensevoice")
        self.local_model.addItem("Qwen3-ASR 1.7B：只用于对方字幕（推荐，你的发声不变慢）", "qwen3_them")
        self.local_model.addItem("Qwen3-ASR 1.7B：双方都用（最准，你的发声每句慢 1.5–3 秒）", "qwen3")
        self.local_model.setToolTip("Qwen3-ASR 对英文和中英夹杂明显更准；需下载 2.4 GB，运行时多占约 2.4 GB 内存")
        self.local_model.setCurrentIndex(max(0, self.local_model.findData(self.s.asr_local)))
        self.local_row = QWidget()
        lr = QHBoxLayout(self.local_row)
        lr.setContentsMargins(22, 0, 0, 0)
        lr.addWidget(QLabel("本机识别模型"))
        lr.addWidget(self.local_model, 1)
        self.local_row.setVisible(IS_MAC)  # Qwen3-ASR runs on MLX (Apple Silicon) only
        lay.addWidget(self.local_row)
        lay.addWidget(self.asr_online)
        self.asr_box = QWidget()
        f = QFormLayout(self.asr_box)
        f.setContentsMargins(22, 6, 0, 0)
        self.asr_key = QLineEdit()
        self.asr_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.asr_key.textEdited.connect(lambda t: (self._typed.__setitem__("dashscope", t.strip()),
                                                   self._refresh_asr()))
        link = QPushButton("获取 API key", objectName="link")
        link.setCursor(Qt.CursorShape.PointingHandCursor)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(providers.get("dashscope").key_url)))
        row = QHBoxLayout()
        row.addWidget(self.asr_key, 1)
        row.addWidget(link)
        f.addRow("API key", row)
        self.asr_key_hint = QLabel(objectName="muted")
        f.addRow("", self.asr_key_hint)
        self.asr_me = QCheckBox("我说的话", checked=self.s.asr_me)
        self.asr_them = QCheckBox("对方说的话（字幕边说边出）", checked=self.s.asr_them)
        row = QHBoxLayout()
        row.addWidget(self.asr_me)
        row.addWidget(self.asr_them)
        row.addStretch()
        f.addRow("用在", row)
        f.addRow("", QLabel("只在有人说话时上传声音，按识别的音频时长计费。\n"
                            "对方说话时字幕先显示识别出的原文，整句说完再换成译文；\n"
                            "用在线翻译时，长句说到一半会先给出临时译文。", objectName="muted"))
        self.asr_test = QPushButton("测试连接")
        self.asr_test.clicked.connect(self._test_asr)
        self.asr_out = QLabel(wordWrap=True)
        row = QHBoxLayout()
        row.addWidget(self.asr_test)
        row.addWidget(self.asr_out, 1)
        f.addRow("", row)
        lay.addWidget(self.asr_box)
        lay.addStretch()
        self.asr_online.toggled.connect(self._refresh_asr)
        self.asr_online.toggled.connect(self._fit_height)
        self._refresh_asr()
        return page

    def _fit_height(self, *_a) -> None:
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _asr_key_value(self) -> str:
        return self._typed.get("dashscope") or keystore.get("dashscope")

    def _refresh_asr(self, _on: bool = False) -> None:
        self.asr_box.setVisible(self.asr_online.isChecked())
        saved = keystore.get("dashscope")
        same = "与“通义千问（阿里云百炼）”翻译共用同一个 key\n"
        self.asr_key_hint.setText(f"{same}已保存在{keystore.where()}（{keystore.mask(saved)}），留空表示不修改"
                                  if saved else f"{same}保存在{keystore.where()}，不会写进设置文件")
        if self._pid() == "dashscope" and self.key.text() != self._typed.get("dashscope", ""):
            self.key.setText(self._typed.get("dashscope", ""))  # the translation tab shows the same key

    def _test_asr(self) -> None:
        key = self._asr_key_value()
        if not key:
            self.asr_out.setText("✗ 先填 API key")
            return
        self.asr_out.setText("连接中…")
        self.asr_test.setEnabled(False)

        def go():
            from ..online_asr import DashScopeStream, StreamError

            t0 = time.monotonic()
            st = DashScopeStream(key, self.s.my_lang, lambda *a: None, lambda e: None)
            try:
                st.start_task()
                st.finish_task()
                res = ("ok", time.monotonic() - t0)
            except StreamError as e:
                res = ("err", str(e))
            finally:
                st.close()
            self.relay.done.emit("asr", res)
        threading.Thread(target=go, daemon=True, name="asr-test").start()

    # ---------------------------------------------------------------- state
    def _pid(self) -> str:
        return self.provider.currentData()

    def _base_url(self) -> str:
        return providers.base_url(self._pid(), self.url.text())

    def _current_key(self) -> str:
        return self._typed.get(self._pid()) or keystore.get(self._pid())

    def _provider_changed(self, _i: int = 0, initial: bool = False) -> None:
        p = providers.get(self._pid())
        custom = p.id == "custom"
        self.url.setVisible(custom)
        self.url_label.setVisible(custom)
        self.url_hint.setText("" if custom else p.base_url)
        self.url_hint.setVisible(not custom)
        self.key_link.setVisible(bool(p.key_url))
        self.key.setText(self._typed.get(p.id, ""))
        current = self.s.llm_model if (initial and self.s.llm_provider == p.id) else ""
        self.model.blockSignals(True)
        self.model.clear()
        for m in p.models:
            self.model.addItem(m)
        self.model.setEditText(current or p.default_model)
        self.model.blockSignals(False)
        self.test_out.clear()
        self._refresh_hints()

    def _mode_changed(self, _on: bool = False) -> None:
        """本地模型: the online settings are hidden and the window shrinks to fit."""
        self.box.setVisible(self.rb_online.isChecked())
        self._refresh_hints()
        self.layout().activate()
        self.resize(self.width(), self.sizeHint().height())

    def _model_changed(self, _t: str = "") -> None:
        self._refresh_hints()

    def _refresh_hints(self) -> None:
        online = self.rb_online.isChecked()
        pid = self._pid()
        saved = keystore.get(pid)
        if not providers.needs_key(pid, self.url.text()):
            self.key_hint.setText("本机或局域网地址，不需要 API key")
        elif saved:
            self.key_hint.setText(f"已保存在{keystore.where()}（{keystore.mask(saved)}）；留空表示不修改")
        else:
            self.key_hint.setText(f"保存在{keystore.where()}，不会写进设置文件")
        model = self.model.currentText().strip()
        if providers.is_reasoning_model(model):
            self.model_hint.setText("这是推理型模型，每句要先“思考”几秒，不适合同传，建议换一个")
        elif pid == "doubao" and model.startswith("ep-"):
            self.model_hint.setText("用的是接入点 ID：如果它对应会“深度思考”的模型，每句会慢几秒，"
                                    "建议直接填 Model ID（如 doubao-seed-2-0-mini-260428），会自动关闭思考")
        else:
            self.model_hint.setText("")
        self.model_hint.setVisible(bool(self.model_hint.text()))
        if self.their.isChecked() or not (self.s.subtitles and self.s.their_source):
            self.their_hint.setText("")
        elif models.LLM[self.s.quality].ready():
            self.their_hint.setText("对方字幕仍用本地模型翻译")
        else:
            self.their_hint.setText(f"对方字幕仍用本地模型翻译，需要下载 {models.llm_name(self.s.quality)}"
                                    f"（约 {models.LLM[self.s.quality].approx_mb / 1024:.1f} GB）")
        self.their_hint.setVisible(bool(self.their_hint.text()))

    def _open_key_page(self) -> None:
        url = providers.get(self._pid()).key_url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    # ---------------------------------------------------------------- background calls
    def _run(self, what: str, fn) -> None:
        if self._busy:
            return
        self._busy = True
        self.test_btn.setEnabled(False)
        self.fetch.setEnabled(False)

        def work():
            try:
                res = fn()
            except TranslateError as e:
                res = e
            except Exception as e:  # noqa: BLE001 — shown, never crashes the dialog
                res = TranslateError("response", str(e))
            self.relay.done.emit(what, res)
        threading.Thread(target=work, daemon=True, name=f"online-{what}").start()

    def _test(self) -> None:
        pid, model, url, key = self._pid(), self.model.currentText().strip(), self._base_url(), self._current_key()
        self.test_out.setText("测试中…")

        def go():
            t0 = time.monotonic()
            out = Translator(OnlineChat(pid, model, key, url), self.s.meeting_context).translate(TEST_SENTENCE, "zh", "en")
            return time.monotonic() - t0, out
        self._run("test", go)

    def _fetch_models(self) -> None:
        url, key = self._base_url(), self._current_key()
        if not url:
            self.test_out.setText("✗ 先填服务地址")
            return
        self.test_out.setText("获取模型列表…")
        self._run("models", lambda: list_models(url, key))

    def _done(self, what: str, res) -> None:
        if what == "asr":
            self.asr_test.setEnabled(True)
            kind, val = res
            self.asr_out.setText(f"✓ 连接成功，{val:.2f} 秒" if kind == "ok" else f"✗ {val}")
            return
        self._busy = False
        self.test_btn.setEnabled(True)
        self.fetch.setEnabled(True)
        if isinstance(res, TranslateError):
            if what == "models" and res.kind in ("model", "response"):
                self.test_out.setText("这个服务商不提供模型列表，请直接填模型名（在服务商控制台的模型列表里查）")
            else:
                self.test_out.setText(f"✗ {res}")
            return
        if what == "test":
            secs, out = res
            self.test_out.setText(f"✓ {secs:.2f} 秒：{out}" if out else f"✗ {secs:.2f} 秒，但没有得到译文")
        else:
            current = self.model.currentText()
            self.model.blockSignals(True)
            self.model.clear()
            for m in res:
                self.model.addItem(m)
            self.model.setEditText(current)
            self.model.blockSignals(False)
            self.test_out.setText(f"✓ 找到 {len(res)} 个模型" if res else "没拿到模型列表，可以直接填模型名")

    # ---------------------------------------------------------------- save
    def _save(self) -> None:
        online = self.rb_online.isChecked()
        pid, model = self._pid(), self.model.currentText().strip()
        if online:
            if pid == "custom" and not self._base_url():
                QMessageBox.warning(self, "在线模型", "请填写服务地址。")
                return
            if not model:
                QMessageBox.warning(self, "在线模型", "请选择或填写模型名。")
                return
            if providers.needs_key(pid, self.url.text()) and not self._current_key():
                QMessageBox.warning(self, "在线模型", f"请填写 {providers.get(pid).name} 的 API key。")
                return
            if not self.s.online_consent or (self.their.isChecked() and not self.s.online_their):
                r = QMessageBox.question(self, "开启在线翻译", consent_text(providers.get(pid).name, self.their.isChecked()),
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                                         QMessageBox.StandardButton.Yes)
                if r != QMessageBox.StandardButton.Yes:
                    return
                self.s.online_consent = True
        asr_online = self.asr_online.isChecked()
        if asr_online:
            if not (self.asr_me.isChecked() or self.asr_them.isChecked()):
                QMessageBox.warning(self, "在线语音识别", "请至少选一方（我说的话 / 对方说的话）。")
                return
            if not self._asr_key_value():
                QMessageBox.warning(self, "在线语音识别", "请填写阿里云百炼的 API key。")
                return
            newly = (not self.s.asr_consent or (self.asr_me.isChecked() and not self.s.asr_me)
                     or (self.asr_them.isChecked() and not self.s.asr_them) or self.s.asr_mode != "online")
            if newly:
                who = "、".join(w for w, on in (("你的声音", self.asr_me.isChecked()),
                                                ("对方的声音", self.asr_them.isChecked())) if on)
                r = QMessageBox.question(
                    self, "开启在线语音识别",
                    f"开启后，{who}会在有人说话时实时上传到「阿里云百炼」识别成文字（只上传说话的片段）。\n\n"
                    "上传的是声音本身；服务商如何保存这些内容，以它的隐私政策为准。",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel, QMessageBox.StandardButton.Yes)
                if r != QMessageBox.StandardButton.Yes:
                    return
                self.s.asr_consent = True
        for p, k in self._typed.items():
            if k:
                keystore.save(p, k)
        self.s.asr_mode = "online" if asr_online else "local"
        self.s.asr_local = self.local_model.currentData() if IS_MAC else "sensevoice"
        self.s.asr_me, self.s.asr_them = self.asr_me.isChecked(), self.asr_them.isChecked()
        self.s.llm_mode = "online" if online else "local"
        self.s.llm_provider, self.s.llm_model = pid, model
        self.s.llm_base_url = self.url.text().strip() if pid == "custom" else self.s.llm_base_url
        self.s.online_their = self.their.isChecked()
        self.s.save()
        self.accept()
        from .theme import toast

        toast(self.parent(), "✓ 已保存 · " + status_text(self.s) + asr_status_text(self.s))
