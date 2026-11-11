"""Update prompts for the control panel: '发现新版本' dialog + download progress + hand-over."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QMessageBox, QProgressDialog, QPushButton,
                               QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__, updater
from ..config import Settings
from .bridge import Bridge


def mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n < 100 << 20 else f"{n >> 20} MB"


def size_line(rel: updater.Release) -> str:
    """What will be downloaded: a small code patch, or the whole installer."""
    if rel.kind == "patch":
        saved = f"，不用下载 {mb(rel.full_size)} 的完整安装包" if rel.full_size else ""
        return f"补丁更新：只需下载 {mb(rel.size)}{saved}，装好后自动重启。"
    return f"完整安装包：{mb(rel.size)}。"


class UpdateController:
    def __init__(self, parent: QWidget, s: Settings, before_install: Callable[[], None]) -> None:
        self.parent, self.s, self.before_install = parent, s, before_install
        self.bridge = Bridge()
        self.bridge.update.connect(self._on_checked)
        self.bridge.progress.connect(self._on_progress)
        self._manual = False
        self._checking = False  # a check is on its way: more clicks join it (one answer, one window)
        self._dlg: QProgressDialog | None = None

    def check(self, manual: bool) -> None:
        if self._dlg is not None and self._dlg.isVisible():
            self._dlg.raise_()  # already downloading the update
            return
        if self._checking:
            self._manual = self._manual or manual
            return
        self._checking, self._manual = True, manual
        updater.check_async(lambda rel, err: self.bridge.update.emit(rel, err))

    def _on_checked(self, rel, err: str) -> None:
        self._checking = False
        if rel is None:
            if self._manual:
                if err:
                    QMessageBox.information(self.parent, "检查更新", f"暂时无法连接 GitHub：{err[:120]}")
                else:
                    QMessageBox.information(self.parent, "检查更新", f"已是最新版本（{__version__}）。")
            return
        if not self._manual and rel.version == self.s.skip_version:
            return
        self._offer(rel)

    def _offer(self, rel: updater.Release) -> None:
        d = QDialog(self.parent)
        d.setWindowTitle("发现新版本")
        d.resize(520, 420)
        lay = QVBoxLayout(d)
        lay.addWidget(QLabel(f"<b>镜声 {rel.version}</b> 可以更新了（当前 {__version__}）。"
                             "模型、音色、会议记录和设置都会保留。"))
        lay.addWidget(QLabel(size_line(rel)))
        notes = QTextBrowser(openExternalLinks=True)
        notes.setMarkdown(rel.notes or "（无更新说明）")
        lay.addWidget(notes)
        bb = QDialogButtonBox()
        now = bb.addButton("立即更新", QDialogButtonBox.ButtonRole.AcceptRole)
        skip = QPushButton("跳过此版本")
        bb.addButton(skip, QDialogButtonBox.ButtonRole.DestructiveRole)
        bb.addButton("稍后", QDialogButtonBox.ButtonRole.RejectRole)
        lay.addWidget(bb)
        now.clicked.connect(lambda: (d.accept(), self._start(rel)))
        skip.clicked.connect(lambda: (setattr(self.s, "skip_version", rel.version), self.s.save(), d.reject()))
        bb.rejected.connect(d.reject)
        if not updater.can_self_install():
            now.setText("打开下载页")
            now.clicked.disconnect()
            now.clicked.connect(lambda: (d.accept(), __import__("webbrowser").open(rel.page)))
        d.exec()

    def _start(self, rel: updater.Release) -> None:
        self._dlg = QProgressDialog(f"正在下载 {rel.asset_name}（{mb(rel.size)}）…", "取消", 0, 1000, self.parent)
        self._dlg.setWindowTitle("更新镜声")
        self._dlg.setWindowModality(Qt.WindowModality.WindowModal)
        self._dlg.setMinimumDuration(0)
        cancel = threading.Event()
        self._dlg.canceled.connect(cancel.set)
        self._rel = rel

        def work():
            try:
                path = updater.download(rel, lambda d, t: self.bridge.progress.emit(d, t), cancel)
                self.bridge.progress.emit(-1, 0)
                self._path = path
            except updater.UpdateBusy as e:  # another window is downloading it: not a failure
                self._err, self._busy = str(e), True
                self.bridge.progress.emit(-2, 0)
            except Exception as e:  # noqa: BLE001
                self._err, self._busy = str(e), False
                self.bridge.progress.emit(-2, 0)
        threading.Thread(target=work, daemon=True, name="update-download").start()

    def _on_progress(self, done: int, total: int) -> None:
        if self._dlg is None:
            return
        if done >= 0:
            self._dlg.setValue(int(1000 * done / max(total, 1)))
            self._dlg.setLabelText(f"正在下载 {self._rel.asset_name}  {mb(done)} / {mb(total)}")
            return
        self._dlg.close()
        if done == -2:
            if getattr(self, "_busy", False):
                QMessageBox.information(self.parent, "正在更新", self._err)
            elif "取消" not in getattr(self, "_err", ""):
                QMessageBox.warning(self.parent, "更新失败", self._err)
            return
        try:
            self.before_install()
            updater.install(self._path, self._rel)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self.parent, "更新失败", str(e))
            return
        from PySide6.QtWidgets import QApplication

        QApplication.quit()  # the hand-over script replaces the app and relaunches it
