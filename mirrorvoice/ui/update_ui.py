"""Update prompts for the control panel: the 检查更新 window (there at the click: checking…, then
the answer) + download progress (with the owl girl's update routine) + hand-over."""
from __future__ import annotations

import threading
import time
from typing import Callable

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__, updater
from ..config import Settings
from .bridge import Bridge
from .mascot import UpdateMascot
from .theme import ACCENT


def mb(n: int) -> str:
    return f"{n / 1048576:.1f} MB" if n < 100 << 20 else f"{n >> 20} MB"


def size_line(rel: updater.Release) -> str:
    """What will be downloaded: a small code patch, or the whole installer."""
    if rel.kind == "patch":
        saved = f"，不用下载 {mb(rel.full_size)} 的完整安装包" if rel.full_size else ""
        return f"补丁更新：只需下载 {mb(rel.size)}{saved}，装好后自动重启。"
    return f"完整安装包：{mb(rel.size)}。"


class Spinner(QWidget):
    """A turning arc: the answer is on its way."""

    TURN_S = 1.1  # one turn

    def __init__(self, size: int = 26) -> None:
        super().__init__()
        self.setFixedSize(size, size)
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self.update)

    def showEvent(self, e) -> None:  # noqa: N802
        self._timer.start()
        super().showEvent(e)

    def hideEvent(self, e) -> None:  # noqa: N802
        self._timer.stop()
        super().hideEvent(e)

    def angle(self) -> float:
        return 360.0 * (time.monotonic() % self.TURN_S) / self.TURN_S

    def paintEvent(self, e) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        box = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p.setPen(QPen(QColor("#EEF0F3"), 3))
        p.drawEllipse(box)
        pen = QPen(QColor(ACCENT), 3)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        p.drawArc(box, int(-16 * self.angle()) + 16 * 90, -16 * 100)  # clockwise, from the top
        p.end()


class UpdateWindow(QDialog):
    """The 检查更新 window. It opens at the click, turning while GitHub is asked, and the answer
    arrives in it: the new version, "already the newest", or why the check failed."""

    def __init__(self, parent: QWidget | None, on_update: Callable[[updater.Release], None],
                 on_skip: Callable[[updater.Release], None], on_retry: Callable[[], None]) -> None:
        super().__init__(parent)
        self.setWindowTitle("检查更新")
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.state = ""  # checking | newest | error | release
        self.rel: updater.Release | None = None
        self.spinner = Spinner()
        self.head = QLabel()
        self.head.setWordWrap(True)
        self.detail = QLabel()
        self.detail.setWordWrap(True)
        self.notes = QTextBrowser(openExternalLinks=True)
        bb = QDialogButtonBox()
        self.now = bb.addButton("立即更新", QDialogButtonBox.ButtonRole.AcceptRole)
        self.skip = QPushButton("跳过此版本")
        bb.addButton(self.skip, QDialogButtonBox.ButtonRole.DestructiveRole)
        self.again = bb.addButton("重试", QDialogButtonBox.ButtonRole.ActionRole)
        self.later = bb.addButton("稍后", QDialogButtonBox.ButtonRole.RejectRole)
        self.now.clicked.connect(lambda: (self.accept(), on_update(self.rel)))
        self.skip.clicked.connect(lambda: (on_skip(self.rel), self.reject()))
        self.again.clicked.connect(on_retry)
        bb.rejected.connect(self.reject)
        top = QHBoxLayout()
        top.addWidget(self.spinner, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.head, 1)
        top.setSpacing(12)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 20, 22, 16)
        lay.setSpacing(10)
        lay.addLayout(top)
        lay.addWidget(self.detail)
        lay.addWidget(self.notes, 1)
        lay.addWidget(bb)

    def _show(self, state: str, head: str, later: str, detail: str = "", notes: str | None = None) -> None:
        self.state = state
        self.head.setText(head)
        self.spinner.setVisible(state == "checking")
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        self.notes.setVisible(notes is not None)
        if notes is not None:
            self.notes.setMarkdown(notes or "（无更新说明）")
        self.now.setVisible(state == "release")
        self.skip.setVisible(state == "release")
        self.again.setVisible(state == "error")
        self.later.setText(later)
        main = self.now if state == "release" else self.later
        main.setDefault(True)
        main.setFocus()  # not the notes: the focus ring around them looked like an error
        self._fit(580, 440) if state == "release" else self._fit(440, 0)
        self.show()
        self.raise_()
        self.activateWindow()

    def _fit(self, w: int, h: int) -> None:
        """Size for what is shown now, staying centred where the window was."""
        self.layout().activate()
        centre = self.geometry().center() if self.isVisible() else None
        self.resize(w, h or max(self.layout().totalHeightForWidth(w), 132))
        if centre is not None:
            box = self.geometry()
            box.moveCenter(centre)
            self.move(box.topLeft())

    def checking(self) -> None:
        self._show("checking", "正在检查更新…", "取消")

    def newest(self) -> None:
        self._show("newest", f"已是最新版本（{__version__}）。", "好的")

    def failed(self, err: str) -> None:
        self._show("error", f"暂时无法连接 GitHub：{err[:120]}", "关闭")

    def release(self, rel: updater.Release) -> None:
        self.rel = rel
        self.now.setText("立即更新" if updater.can_self_install() else "打开下载页")
        self._show("release", f"<b>翻了么 {rel.version}</b> 可以更新了（当前 {__version__}）。"
                   "模型、音色、会议记录和设置都会保留。", "稍后", size_line(rel), rel.notes or "")


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
    def __init__(self, parent: QWidget, s: Settings, before_install: Callable[[], None]) -> None:
        self.parent, self.s, self.before_install = parent, s, before_install
        self.bridge = Bridge()
        self.bridge.update.connect(self._on_checked)
        self.bridge.progress.connect(self._on_progress)
        self._manual = False
        self._checking = False  # a check is on its way: more clicks join it (one answer, one window)
        self._win: UpdateWindow | None = None
        self._dlg: UpdateProgress | None = None

    def _window(self) -> UpdateWindow:
        if self._win is None:
            self._win = UpdateWindow(self.parent, self._update, self._skip, lambda: self.check(manual=True))
        return self._win

    def check(self, manual: bool) -> None:
        if self._dlg is not None and self._dlg.isVisible():
            self._dlg.raise_()  # already downloading the update
            return
        if manual:
            self._window().checking()  # there at the click, however long GitHub takes
        if self._checking:
            self._manual = self._manual or manual
            return
        self._checking, self._manual = True, manual
        updater.check_async(lambda rel, err: self.bridge.update.emit(rel, err))

    def _on_checked(self, rel, err: str) -> None:
        self._checking = False
        win = self._win
        if self._manual:
            if win is None or not win.isVisible() or win.state != "checking":
                return  # the window was closed before the answer came: it is not wanted any more
            if rel is None:
                win.failed(err) if err else win.newest()
                return
        elif rel is None or rel.version == self.s.skip_version:
            return  # the check at start-up says nothing unless there is something new
        self._window().release(rel)

    def _skip(self, rel: updater.Release) -> None:
        self.s.skip_version = rel.version
        self.s.save()

    def _update(self, rel: updater.Release) -> None:
        if updater.can_self_install():
            self._start(rel)
        else:
            __import__("webbrowser").open(rel.page)

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
