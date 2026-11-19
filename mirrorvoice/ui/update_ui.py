"""Update prompts for the control panel: the 检查更新 window (there at the click: checking…, then
the answer) + download progress (with the owl girl's update routine) + hand-over."""
from __future__ import annotations

import threading
import time
from typing import Callable

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QTextBrowser, QVBoxLayout, QWidget)

from .. import __version__, updater
from ..config import Settings
from .bridge import Bridge
from .mascot import UpdateMascot, UpdateRobot
from . import theme


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
        p.setPen(QPen(QColor(theme.current().pressed), 3))
        p.drawEllipse(box)
        pen = QPen(QColor(theme.current().accent), 3)
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


ROBOT_BLUE = "#56BEF2"  # the light in the robot's cables (packaging/assets/mascot/robot): the bar is the same

# What the window says, in the voice of a machine being plugged in and rewritten (not 正在下载 /
# 正在安装): head while it downloads, while it installs, when it is in; the line under the bar.
SAY_LINK = "接入神经链路…"
SAY_WRITE = "改写核心协议…"
SAY_DONE = "v{v} 已上线"
SAY_FLOW = "下行 {done} / {total} // v{old}→v{new}"  # (one line: no wrap under the bar)
SAY_HOLD = "链路占用中，请勿关闭"
SAY_WAKE = "意识重启中…"


class NeuralLink(QWidget):
    """The progress bar as a cable plugged into the robot's neck port: from the port it runs out to
    where the bar is and along it; the progress lights it from the port outwards, and packets of
    light run back along the lit part into the port — the update flowing into its head. Drawn over
    the window (the QProgressBar keeps the value and the place, invisibly)."""

    WIDTH, PULSES, PULSE_S = 6.0, 3, 1.2

    def __init__(self, dlg: "UpdateProgress") -> None:
        super().__init__(dlg)
        self.dlg = dlg
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.clock = time.monotonic
        self._t0 = self.clock()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.update)
        dlg.bar.valueChanged.connect(self.update)

    def showEvent(self, e) -> None:
        if not self.dlg.robot.still:
            self._timer.start(33)
        super().showEvent(e)

    def hideEvent(self, e) -> None:
        self._timer.stop()
        super().hideEvent(e)

    def path(self) -> QPainterPath:
        """From the port to the bar's right end, in this widget's (the window's) coordinates."""
        port = self.dlg.robot.mapTo(self.dlg, self.dlg.robot.port())
        bar = self.dlg.bar.geometry()
        bar = QRectF(self.dlg.bar.parentWidget().mapTo(self.dlg, bar.topLeft()), bar.size()) \
            if self.dlg.bar.parentWidget() is not self.dlg else QRectF(bar)
        y, x0, x1 = bar.center().y(), bar.left(), bar.right()
        p = QPainterPath(QPointF(port))
        bend = max(24.0, (x0 - port.x()) * 0.55)
        p.cubicTo(QPointF(port.x() + bend, port.y()), QPointF(x0 - bend, y), QPointF(x0, y))
        p.lineTo(QPointF(x1, y))
        return p

    def paintEvent(self, _e) -> None:
        path = self.path()
        t = theme.current()
        done = self.dlg.bar.value() / max(1, self.dlg.bar.maximum())
        pts = [path.pointAtPercent(i / 200) for i in range(201)]  # (equal steps along the length)
        lit = pts[:max(1, int(round(done * 200))) + 1]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        def stroke(points, color: QColor, width: float) -> None:
            if len(points) < 2:
                return
            q = QPainterPath(points[0])
            for pt in points[1:]:
                q.lineTo(pt)
            p.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(q)

        blue = QColor(ROBOT_BLUE)
        stroke(pts, QColor(t.pressed), self.WIDTH)                 # the cable
        glow = QColor(blue)
        glow.setAlpha(70)
        stroke(lit, glow, self.WIDTH + 5)                          # lit: a halo …
        stroke(lit, blue, self.WIDTH)                              # … and the light
        if len(lit) > 8 and not self.dlg.robot.still:              # packets running back into the port
            phase = ((self.clock() - self._t0) / self.PULSE_S) % 1.0
            for k in range(self.PULSES):
                f = 1.0 - ((phase + k / self.PULSES) % 1.0)        # 1 at the lit end -> 0 at the port
                i = int(f * (len(lit) - 1))
                seg = lit[max(0, i - 5):i + 1]
                stroke(seg, QColor(220, 246, 255, 230), self.WIDTH - 2)
        port = pts[0]                                              # the plug, at the port
        p.setPen(QPen(QColor(t.text_2), 1.4))
        p.setBrush(blue if done > 0 else QColor(t.pressed))
        p.drawEllipse(port, 4.2, 4.2)
        p.end()


class UpdateProgress(QDialog):
    """Download progress, with a picture beside it: her floating, curling up and plugging in (虚拟形象
    on), or the robot hanging from cables a light runs down (off) — flush with the window's top-left,
    the cables coming in from its top edge, the bar in their blue. The text: what happens now
    (head), how far (label)."""

    def __init__(self, parent: QWidget | None, text: str, mascot: bool = True, head: str = SAY_LINK) -> None:
        super().__init__(parent)
        self.setWindowTitle("更新翻了么")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        self.canceled = threading.Event()
        self._locked = False
        self.mascot = UpdateMascot()
        self.mascot.setVisible(mascot)
        self.robot = UpdateRobot()
        self.robot.setVisible(not mascot)
        self.head = QLabel(head)
        f = self.head.font()
        f.setPointSizeF(f.pointSizeF() * 1.25)
        f.setBold(True)
        self.head.setFont(f)
        self.label = QLabel(text)
        self.label.setWordWrap(True)
        mono = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)  # (the readout of a machine)
        mono.setPointSizeF(self.label.font().pointSizeF() * 0.92)
        self.label.setFont(mono)
        self.label.setStyleSheet(f"color: {theme.current().text_2};")
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(12)  # (room for the cable drawn over it, halo included)
        if not mascot:  # the bar is NeuralLink's: here it only keeps the value and the place
            self.bar.setStyleSheet("QProgressBar, QProgressBar::chunk { background: transparent; border: none; }")
        else:
            self.bar.setFixedHeight(6)
        self.cancel = QPushButton("断开")
        self.cancel.setToolTip("取消这次更新")
        self.cancel.clicked.connect(self.reject)
        right = QVBoxLayout()
        right.setContentsMargins(4 if mascot else 18, 20, 22, 18)
        right.setSpacing(8)
        right.addStretch(1)
        right.addWidget(self.head)
        right.addWidget(self.bar)
        right.addWidget(self.label)
        right.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel)
        right.addLayout(buttons)
        lay = QHBoxLayout(self)
        if mascot:
            lay.setContentsMargins(12, 12, 0, 12)
            lay.addWidget(self.mascot, 0, Qt.AlignmentFlag.AlignVCenter)
        else:  # (no margin: the cables run in from the window's edge, the bottom fades into it)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self.robot, 0, Qt.AlignmentFlag.AlignTop)
        lay.setSpacing(0)
        lay.addLayout(right, 1)
        self.setFixedWidth(500 if mascot else 500)
        self.link = None if mascot else NeuralLink(self)

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        if self.link is not None:
            self.link.setGeometry(self.rect())
            self.link.raise_()

    def picture(self) -> UpdateMascot | UpdateRobot:
        return self.robot if self.mascot.isHidden() else self.mascot

    def finish(self) -> float:
        """The update is in: the picture's last moment (her landing, the robot waking). Seconds it takes."""
        return self.picture().finish()

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
        flow = SAY_FLOW.format(done=mb(0), total=mb(rel.size), old=__version__, new=rel.version)
        self._dlg = UpdateProgress(self.parent, flow, self.s.mascot, SAY_LINK)
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
            dlg.label.setText(SAY_FLOW.format(done=mb(done), total=mb(total), old=__version__, new=self._rel.version))
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
        dlg.head.setText(SAY_WRITE)
        dlg.label.setText(SAY_HOLD)
        QApplication.processEvents()
        try:
            self.before_install()
            updater.install(self._path, self._rel)
        except Exception as e:  # noqa: BLE001
            dlg.dismiss()
            QMessageBox.warning(self.parent, "更新失败", str(e))
            return
        dlg.head.setText(SAY_DONE.format(v=self._rel.version))
        dlg.label.setText(SAY_WAKE)
        # let the picture finish first (her landing, the robot waking): the hand-over waits for this
        # process to exit, then switches to the patch (or replaces the app) and relaunches it. Not
        # the Windows installer — it starts at once and closes the app itself.
        landing = self._path.suffix.lower() == ".zip" or not updater.IS_WIN
        QTimer.singleShot(int(1000 * dlg.finish()) + 300 if landing else 0, lambda: self._restart(dlg))

    @staticmethod
    def _restart(dlg: UpdateProgress) -> None:
        # Qt 6's quit() first asks every window to close and gives up if one refuses — and the
        # locked progress window refuses (it stayed on "更新好了…" for good, the hand-over script
        # waiting for an exit that never came). Close it first; if another window still holds
        # the quit up, leave anyway.
        dlg.dismiss()
        QApplication.quit()
        QTimer.singleShot(3000, lambda: QApplication.exit(0))
