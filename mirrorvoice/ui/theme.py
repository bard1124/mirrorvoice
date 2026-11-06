"""Light theme shared by every window: Fusion style + palette + one stylesheet.

Fusion draws the same on macOS and Windows, so the stylesheet below is the whole look: a
sidebar on pale grey, white content, rounded cards, one accent colour for the primary action.
"""
from __future__ import annotations

from PySide6.QtCore import QLibraryInfo, QLocale, Qt, QTranslator
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

ACCENT = "#E8432E"
ACCENT_DARK = "#CF3621"
ACCENT_SOFT = "#FFF1EE"
TEXT = "#1F2329"
TEXT_2 = "#6B7280"
LINE = "#E8EAEE"
BG = "#FFFFFF"
BG_SIDE = "#F5F6F8"
BG_SOFT = "#F7F8FA"


def _asset(name: str) -> str:
    """Stylesheet url(): forward slashes, works from a source checkout and the frozen app."""
    from .icons import assets_dir

    return str(assets_dir() / name).replace("\\", "/")


CHEVRON = _asset("chevron-down.svg")

QSS = f"""
* {{ font-family: "PingFang SC", "Microsoft YaHei UI", "Segoe UI", "Helvetica Neue", sans-serif; font-size: 13px;
     color: {TEXT}; }}
QMainWindow, QDialog, QWidget#content, QMessageBox {{ background: {BG}; }}
QToolTip {{ background: {TEXT}; color: white; border: none; padding: 6px 8px; border-radius: 6px; }}

QFrame#sidebar {{ background: {BG_SIDE}; border: none; border-right: 1px solid {LINE}; }}
QLabel#brand {{ font-size: 16px; font-weight: 700; }}
QLabel#brandSub {{ font-size: 11px; color: {TEXT_2}; }}
QPushButton#nav {{ text-align: left; padding: 9px 14px; border: none; border-radius: 10px; background: transparent;
                   font-size: 14px; color: #3B414B; }}
QPushButton#nav:hover {{ background: #ECEEF2; }}
QPushButton#nav:checked {{ background: {BG}; color: {ACCENT}; font-weight: 600; }}

QLabel#h1 {{ font-size: 20px; font-weight: 700; }}
QLabel#status {{ padding: 5px 12px; border-radius: 13px; background: #F2F3F5; color: {TEXT_2}; font-size: 13px; }}
QLabel#status[state="running"] {{ background: #E6F7EC; color: #1A9E4B; }}
QLabel#status[state="busy"] {{ background: #FFF4E0; color: #B26A00; }}

QPushButton {{ background: {BG}; border: 1px solid #DFE2E7; border-radius: 8px; padding: 6px 14px; min-height: 20px; }}
QPushButton:hover {{ background: {BG_SOFT}; border-color: #C9CDD4; }}
QPushButton:pressed {{ background: #EEF0F3; }}
QPushButton:disabled {{ color: #A0A4AB; border-color: #E8EAEE; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#primary {{ background: {ACCENT}; color: white; border: none; border-radius: 10px; padding: 0 28px;
                       min-height: 40px; font-size: 15px; font-weight: 600; }}
QPushButton#primary:hover {{ background: {ACCENT_DARK}; }}
QPushButton#primary[running="true"] {{ background: #3B414B; }}
QPushButton#primary[running="true"]:hover {{ background: #2A2F38; }}
QPushButton#link {{ border: none; background: transparent; color: {ACCENT}; padding: 2px 4px; }}

QFrame#card {{ background: {BG}; border: 1px solid {LINE}; border-radius: 12px; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 600; }}
QFrame#banner {{ background: #FFF7E6; border: 1px solid #FFE1A8; border-radius: 10px; }}
QFrame#banner QLabel {{ color: #8A5300; }}
QLabel#muted {{ color: {TEXT_2}; }}
QDialog#msgbox {{ background: {BG}; }}
QLabel#msgTitle {{ font-size: 16px; font-weight: 700; }}
QLabel#msgText {{ font-size: 13px; color: #3B414B; }}
QLabel#msgBadge {{ min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; border-radius: 18px;
                   font-size: 18px; font-weight: 700; qproperty-alignment: AlignCenter; }}
QLabel#msgBadge[kind="info"] {{ background: #E8F1FF; color: #2F6FED; }}
QLabel#msgBadge[kind="warning"] {{ background: #FFF4E0; color: #B26A00; }}
QLabel#msgBadge[kind="question"] {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QLabel#msgBadge[kind="critical"] {{ background: #FDECEC; color: #C62828; }}

QComboBox {{ background: {BG}; border: 1px solid #DFE2E7; border-radius: 8px; padding: 5px 10px; min-height: 20px; }}
QComboBox:hover {{ border-color: #C9CDD4; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{CHEVRON}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QComboBox QAbstractItemView {{ background: {BG}; border: 1px solid {LINE}; border-radius: 8px; padding: 4px;
                               selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT}; outline: 0; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox {{ background: {BG}; border: 1px solid #DFE2E7; border-radius: 8px;
                                                   padding: 6px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}
QCheckBox, QRadioButton {{ spacing: 8px; padding: 1px 0; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: 12px; margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; background: {BG}; }}

QProgressBar {{ border: none; background: #EEF0F3; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QSlider::horizontal {{ min-height: 24px; }}
QSlider::groove:horizontal {{ height: 4px; background: #EEF0F3; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: {BG};
                              border: 2px solid {ACCENT}; }}

QListWidget#log {{ background: {BG_SOFT}; border: none; border-radius: 10px; padding: 6px; outline: 0; }}
QListWidget#log::item {{ padding: 8px 10px; border-radius: 8px; margin: 2px 0; background: {BG}; }}
QListWidget#log::item:selected {{ background: {ACCENT_SOFT}; }}
QTableWidget {{ background: {BG}; border: 1px solid {LINE}; border-radius: 8px; gridline-color: {LINE}; }}
QHeaderView::section {{ background: {BG_SOFT}; border: none; border-bottom: 1px solid {LINE}; padding: 6px; font-weight: 600; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #D5D8DE; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: #BFC3CA; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #D5D8DE; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea#page, QScrollArea#page > QWidget > QWidget#content {{ background: {BG}; }}
"""


_translators: list[QTranslator] = []  # keep alive
_SB = QMessageBox.StandardButton
_LABELS = {_SB.Ok: "好的", _SB.Yes: "是", _SB.No: "否", _SB.Cancel: "取消", _SB.Close: "关闭", _SB.Save: "保存",
           _SB.Discard: "不保存", _SB.Retry: "重试", _SB.Ignore: "忽略", _SB.Open: "打开", _SB.Abort: "中止"}
_BADGE = {"info": "i", "warning": "!", "question": "?", "critical": "✕"}


class MessageDialog(QDialog):
    """Replacement for the static QMessageBox calls: badge + bold title + text + styled buttons."""

    def __init__(self, parent, kind: str, title: str, text: str, buttons, default) -> None:
        super().__init__(parent, objectName="msgbox")
        self.setWindowTitle(title)
        self.setModal(True)
        self.result_button = _SB.NoButton
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 18)
        lay.setSpacing(14)
        head = QHBoxLayout()
        head.setSpacing(14)
        badge = QLabel(_BADGE.get(kind, "i"), objectName="msgBadge")
        badge.setProperty("kind", kind)
        head.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(6)
        col.addWidget(QLabel(title, objectName="msgTitle"))
        body = QLabel(text, objectName="msgText", wordWrap=True)
        body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        body.setMinimumWidth(320)
        body.setMaximumWidth(520)
        col.addWidget(body)
        head.addLayout(col, 1)
        lay.addLayout(head)
        row = QHBoxLayout()
        row.addStretch()
        order = [b for b in (_SB.Yes, _SB.Ok, _SB.Save, _SB.Open, _SB.Retry, _SB.Ignore, _SB.Discard, _SB.No,
                             _SB.Close, _SB.Cancel, _SB.Abort) if buttons & b]
        primary = default if default in order else (order[0] if order else _SB.Ok)
        if not order:
            order = [_SB.Ok]
        for b in order:
            btn = QPushButton(_LABELS.get(b, "好的"))
            if b == primary:
                btn.setObjectName("primary")
                btn.setDefault(True)
            btn.setMinimumWidth(84)
            btn.clicked.connect(lambda _=False, b=b: self._pick(b))
            row.addWidget(btn)
        lay.addLayout(row)

    def _pick(self, b) -> None:
        self.result_button = b
        self.accept()

    def reject(self) -> None:  # Esc / window close = the dismissive choice
        if self.result_button == _SB.NoButton:
            self.result_button = _SB.Cancel
        super().reject()


def _ask(kind):
    def show(parent, title, text, buttons=_SB.Ok, default=_SB.NoButton):
        d = MessageDialog(parent, kind, title, text, buttons, default)
        d.exec()
        if d.result_button == _SB.Cancel and not (buttons & _SB.Cancel):
            return _SB.No if buttons & _SB.No else _SB.Ok  # dismissed: map to the safe answer
        return d.result_button
    return staticmethod(show)


def install_message_boxes() -> None:
    """Route QMessageBox.information/warning/question/critical to MessageDialog (same signatures
    and StandardButton return values, so callers need no change)."""
    QMessageBox.information = _ask("info")
    QMessageBox.warning = _ask("warning")
    QMessageBox.question = _ask("question")
    QMessageBox.critical = _ask("critical")


def load_translations(app: QApplication) -> None:
    """Qt's own strings (dialog buttons, context menus) in Chinese; silently skipped if missing."""
    for name in ("qtbase",):
        t = QTranslator(app)
        if t.load(QLocale(QLocale.Language.Chinese, QLocale.Country.China), name, "_",
                  QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(t)
            _translators.append(t)


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    load_translations(app)
    install_message_boxes()
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(BG))
    pal.setColor(QPalette.ColorRole.Base, QColor(BG))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(BG_SOFT))
    pal.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.ColorRole.Button, QColor(BG))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))   # check marks, selections
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("white"))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor("#A0A4AB"))
    app.setPalette(pal)
    app.setStyleSheet(QSS)


def card(title: str | None = None, parent: QWidget | None = None) -> tuple[QFrame, QVBoxLayout]:
    """A white rounded card with an optional bold title; returns (frame, body layout)."""
    f = QFrame(parent, objectName="card")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 12, 16, 12)
    lay.setSpacing(8)
    if title:
        lay.addWidget(QLabel(title, objectName="cardTitle"))
    return f, lay


def repolish(w: QWidget) -> None:
    """Re-apply the stylesheet after a dynamic property (state=…) changed."""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()
