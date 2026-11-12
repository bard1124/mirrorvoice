"""Update prompts for the control panel: '发现新版本' dialog + download progress (with the owl girl's
update routine) + hand-over."""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__, updater
from ..config import Settings
from .bridge import Bridge
from .mascot import UpdateMascot


def mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n < 100 << 20 else f"{n >> 20} MB"


def size_line(rel: updater.Release) -> str:
    """What will be downloaded: a small code patch, or the whole installer."""
    if rel.kind == "patch":
        saved = f"，不用下载 {mb(rel.full_size)} 的完整安装包" if rel.full_size else ""
        return f"补丁更新：只需下载 {mb(rel.size)}{saved}，装好后自动重启。"
    return f"完整安装包：{mb(rel.size)}。"


class UpdateProgress(QDialog):
    """Download progress, with her floating, curling up and plugging in beside it."""

    def __init__(self, parent: QWidget | None, text: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("更新翻了么")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.canceled = threading.Event()
        self._locked = False
        self.mascot = UpdateMascot()
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.cancel = QPushButton("取消")
        self.cancel.clicked.connect(self.reject)
        right = QVBoxLayout()
        right.addStretch(1)
        right.addWidget(self.label)
        right.addWidget(self.bar)
        right.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel)
        right.addLayout(buttons)
        lay = QHBoxLayout(self)
        lay.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(right, 1)
        self.setMinimumWidth(520)

    def reject(self) -> None:  # 取消, Esc or the close box
        if self._locked:
            return  # installing: too late to stop
        self.canceled.set()
        super().reject()

    def lock(self) -> None:
        self._locked = True
        self.cancel.setEnabled(False)

    def dismiss(self) -> None:
        self._locked = False
        self.done(0)


class UpdateController:
    def __init__(self, parent: QWidget, s: Settings, before_install: Callable[[], None],
                 on_checking: Callable[[bool], None] | None = None) -> None:
        self.parent, self.s, self.before_install = parent, s, before_install
        # on_checking(True) the moment a check I asked for starts, (False) when its answer is in:
        # the click shows at once, however long GitHub takes
        self.on_checking = on_checking or (lambda busy: None)
        self.bridge = Bridge()
        self.bridge.update.connect(self._on_checked)
        self.bridge.progress.connect(self._on_progress)
        self._manual = False
        self._checking = False  # a check is on its way: more clicks join it (one answer, one window)
        self._dlg: UpdateProgress | None = None

    def check(self, manual: bool) -> None:
        if self._dlg is not None and self._dlg.isVisible():
            self._dlg.raise_()  # already downloading the update
            return
        if manual:
            self.on_checking(True)
        if self._checking:
            self._manual = self._manual or manual
            return
        self._checking, self._manual = True, manual
        updater.check_async(lambda rel, err: self.bridge.update.emit(rel, err))

    def _on_checked(self, rel, err: str) -> None:
        self._checking = False
        self.on_checking(False)
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
        lay.addWidget(QLabel(f"<b>翻了么 {rel.version}</b> 可以更新了（当前 {__version__}）。"
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
        self._dlg = UpdateProgress(self.parent, f"正在下载 {rel.asset_name}（{mb(rel.size)}）…")
        self._dlg.show()
        cancel = self._dlg.canceled
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
        dlg = self._dlg
        if dlg is None:
            return
        if done >= 0:
            dlg.bar.setValue(int(1000 * done / max(total, 1)))
            dlg.label.setText(f"正在下载 {self._rel.asset_name}  {mb(done)} / {mb(total)}")
            return
        if done == -2:
            dlg.dismiss()
            if getattr(self, "_busy", False):
                QMessageBox.information(self.parent, "正在更新", self._err)
            elif "取消" not in getattr(self, "_err", ""):
                QMessageBox.warning(self.parent, "更新失败", self._err)
            return
        dlg.lock()
        dlg.bar.setValue(1000)
        dlg.label.setText("下载好了，正在安装…")
        QApplication.processEvents()
        try:
            self.before_install()
            updater.install(self._path, self._rel)
        except Exception as e:  # noqa: BLE001
            dlg.dismiss()
            QMessageBox.warning(self.parent, "更新失败", str(e))
            return
        dlg.label.setText("更新好了，马上重新打开翻了么…")
        # let her rise and land first: the hand-over waits for this process to exit, then switches
        # to the patch (or replaces the app) and relaunches it. Not the Windows installer — it
        # starts at once and closes the app itself.
        landing = self._path.suffix.lower() == ".zip" or not updater.IS_WIN
        QTimer.singleShot(int(1000 * dlg.mascot.finish()) + 300 if landing else 0, lambda: self._restart(dlg))

    @staticmethod
    def _restart(dlg: UpdateProgress) -> None:
        # Qt 6's quit() first asks every window to close and gives up if one refuses — and the
        # locked progress window refuses (it stayed on "更新好了…" for good, the hand-over script
        # waiting for an exit that never came). Close it first; if another window still holds
        # the quit up, leave anyway.
        dlg.dismiss()
        QApplication.quit()
        QTimer.singleShot(3000, lambda: QApplication.exit(0))
