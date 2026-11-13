"""The look shared by every window: Fusion style + palette + one stylesheet, in three themes.

Fusion draws the same on macOS and Windows, so the stylesheet below is the whole look. Every
colour and corner in it is a token of the Theme in use (THEMES): 静场 (quiet light), 调音台 (dark
console) and 对话 (soft, rounded). use() switches while the app runs.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QLibraryInfo, QLocale, QObject, Qt, QTimer, QTranslator
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                               QVBoxLayout, QWidget)

@dataclass(frozen=True)
class Theme:
    key: str
    name: str
    # one accent for what is on / chosen, and the start button (which turns into "stop" while running)
    accent: str
    accent_soft: str
    primary: str
    primary_hover: str
    on_primary: str
    stop: str
    stop_hover: str
    on_stop: str
    # text, from strongest to disabled
    text: str
    text_2: str
    text_3: str
    nav_text: str
    nav_on_bg: str
    nav_on_fg: str
    # grounds: the window, the sidebar, the well things sit in, and what controls and cards are made of
    bg: str
    bg_side: str
    bg_soft: str
    surface: str
    chip: str
    pressed: str
    line: str
    border: str
    border_hover: str
    scroll: str
    scroll_hover: str
    tip_bg: str
    tip_fg: str
    # states
    ok_bg: str
    ok_fg: str
    busy_bg: str
    busy_fg: str
    warn_bg: str
    warn_line: str
    warn_fg: str
    info_bg: str
    info_fg: str
    danger_bg: str
    danger_fg: str
    # corners: controls, larger controls (menus, the log), cards, the start button, small bits
    r: int
    r2: int
    rc: int
    rp: int
    rs: int
    # the conversation: how a sentence is drawn, and who is which colour
    convo: str  # rows | cards | bubbles
    me: str
    them: str
    live: str
    me_bubble: str
    on_me_bubble: str
    them_bubble: str
    on_them_bubble: str
    switch_off: str
    dark: bool = False


QUIET = Theme(
    key="quiet", name="静场",
    accent="#C9371F", accent_soft="#FBECE8", primary="#C9371F", primary_hover="#A32C18", on_primary="#FFFFFF",
    stop="#16181C", stop_hover="#2B2F36", on_stop="#FFFFFF",
    text="#16181C", text_2="#5B6270", text_3="#A0A4AB", nav_text="#3A3F47", nav_on_bg="#EFEEEA", nav_on_fg="#16181C",
    bg="#FFFFFF", bg_side="#FBFBFA", bg_soft="#F7F7F5", surface="#FFFFFF", chip="#EFEEEA", pressed="#E7E6E2",
    line="#E7E6E2", border="#DAD9D4", border_hover="#BEBDB7", scroll="#D8D7D2", scroll_hover="#BEBDB7",
    tip_bg="#16181C", tip_fg="#FFFFFF",
    ok_bg="#E6F4EB", ok_fg="#1F7A46", busy_bg="#FFF4E0", busy_fg="#8A5300",
    warn_bg="#FFF7E6", warn_line="#FFE1A8", warn_fg="#8A5300", info_bg="#E8F1FF", info_fg="#2F5BB7",
    danger_bg="#FDECEC", danger_fg="#B3261E",
    r=8, r2=10, rc=12, rp=10, rs=6,
    convo="rows", me="#C9371F", them="#3A3F47", live="#5B6270",
    me_bubble="#FBECE8", on_me_bubble="#16181C", them_bubble="#EFEEEA", on_them_bubble="#16181C",
    switch_off="#B9BDC6")

STUDIO = Theme(
    key="studio", name="调音台",
    accent="#5EE0A8", accent_soft="#14201C", primary="#5EE0A8", primary_hover="#49C592", on_primary="#0F1215",
    stop="#FF8A65", stop_hover="#FF7043", on_stop="#0F1215",
    text="#E8ECF1", text_2="#9AA4B2", text_3="#5C6673", nav_text="#AEB8C6", nav_on_bg="#1E242B", nav_on_fg="#FFFFFF",
    bg="#0F1215", bg_side="#12161A", bg_soft="#12161A", surface="#171B20", chip="#1E242B", pressed="#262C34",
    line="#262C34", border="#3A424D", border_hover="#55606E", scroll="#3A424D", scroll_hover="#55606E",
    tip_bg="#E8ECF1", tip_fg="#0F1215",
    ok_bg="#14201C", ok_fg="#5EE0A8", busy_bg="#2A2413", busy_fg="#F2C14E",
    warn_bg="#2A2413", warn_line="#5A4A1A", warn_fg="#F2C14E", info_bg="#14202B", info_fg="#7CC4FF",
    danger_bg="#2B1717", danger_fg="#FF9A8A",
    r=6, r2=8, rc=8, rp=6, rs=4,
    convo="cards", me="#5EE0A8", them="#7CC4FF", live="#F2C14E",
    me_bubble="#171B20", on_me_bubble="#E8ECF1", them_bubble="#171B20", on_them_bubble="#E8ECF1",
    switch_off="#3A424D", dark=True)

CHAT = Theme(
    key="chat", name="对话",
    accent="#4B3FD6", accent_soft="#ECEAFB", primary="#4B3FD6", primary_hover="#3B31B0", on_primary="#FFFFFF",
    stop="#1F1B3A", stop_hover="#342E5C", on_stop="#FFFFFF",
    text="#1F1B3A", text_2="#4A4670", text_3="#A5A2C0", nav_text="#4A4670", nav_on_bg="#4B3FD6", nav_on_fg="#FFFFFF",
    bg="#F1F0FA", bg_side="#F1F0FA", bg_soft="#FFFFFF", surface="#FFFFFF", chip="#E7E5F7", pressed="#DCD9F3",
    line="#E7E5F7", border="#D5D2EE", border_hover="#B9B4E3", scroll="#D5D2EE", scroll_hover="#B9B4E3",
    tip_bg="#1F1B3A", tip_fg="#FFFFFF",
    ok_bg="#E3F5EA", ok_fg="#1C6B45", busy_bg="#FFF1D2", busy_fg="#7A5A00",
    warn_bg="#FFF1D2", warn_line="#F7D48A", warn_fg="#7A5A00", info_bg="#ECEAFB", info_fg="#3B31B0",
    danger_bg="#FDECEC", danger_fg="#B3261E",
    r=15, r2=16, rc=22, rp=20, rs=8,
    convo="bubbles", me="#4B3FD6", them="#7A5A00", live="#4B3FD6",
    me_bubble="#4B3FD6", on_me_bubble="#FFFFFF", them_bubble="#FFF1D2", on_them_bubble="#1F1B3A",
    switch_off="#C9C6E6")

THEMES: dict[str, Theme] = {t.key: t for t in (QUIET, STUDIO, CHAT)}
DEFAULT = QUIET.key
_current = QUIET


def current() -> Theme:
    return _current


def _asset(name: str) -> str:
    """Stylesheet url(): forward slashes, works from a source checkout and the frozen app."""
    from .icons import assets_dir

    return str(assets_dir() / name).replace("\\", "/")


def _chevron(t: Theme) -> str:
    """The drop-down arrow in the theme's secondary text colour. The bundled one is grey on white;
    a recoloured copy is written to the data folder (a new image in the bundle would be a new
    runtime, i.e. a full download for everyone)."""
    if not t.dark:
        return _asset("chevron-down.svg")
    try:
        from ..config import HOME
        from .icons import assets_dir

        svg = (assets_dir() / "chevron-down.svg").read_text(encoding="utf-8").replace("#6B7280", t.text_2)
        out = HOME / "ui" / f"chevron-{t.key}.svg"
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.is_file() or out.read_text(encoding="utf-8") != svg:
            out.write_text(svg, encoding="utf-8")
        return str(out).replace("\\", "/")
    except OSError:
        return _asset("chevron-down.svg")


# Only fonts that exist on this platform: a missing family made Qt search every alias at startup
FONTS = '"PingFang SC", "Helvetica Neue"' if sys.platform == "darwin" else '"Microsoft YaHei UI", "Segoe UI"'

_QSS = """
* {{ font-family: {FONTS}; font-size: 13px;
     color: {TEXT}; }}
QMainWindow, QDialog, QWidget#content, QMessageBox {{ background: {BG}; }}
QLabel#toast {{ background: rgba(32,36,44,0.92); color: white; padding: 9px 16px; border-radius: {R2}px; }}
QToolTip {{ background: {TIP_BG}; color: {TIP_FG}; border: none; padding: 6px 8px; border-radius: {RS}px; }}

QFrame#sidebar {{ background: {BG_SIDE}; border: none; border-right: 1px solid {LINE}; }}
QLabel#brand {{ font-size: 16px; font-weight: 700; }}
QLabel#brandSub {{ font-size: 11px; color: {TEXT_2}; }}
QLabel#sideOption {{ font-size: 13px; color: {NAV_TEXT}; }}
QPushButton#nav {{ text-align: left; padding: 9px 14px; border: none; border-radius: {R2}px; background: transparent;
                   font-size: 14px; color: {NAV_TEXT}; }}
QPushButton#nav:hover {{ background: {CHIP}; }}
QPushButton#nav:checked {{ background: {NAV_ON_BG}; color: {NAV_ON_FG}; font-weight: 600; }}

QLabel#h1 {{ font-size: 20px; font-weight: 700; }}
QFrame#topbar {{ background: {BG_SIDE}; border: none; border-bottom: 1px solid {LINE}; }}
QFrame#topbar QPushButton#nav {{ padding: 8px 11px; }}
QFrame#rail {{ background: transparent; border: none; }}
QFrame#chain {{ background: transparent; border: none; }}
QFrame#cell {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {R2}px; }}
QFrame#cellOn {{ background: {ACCENT_SOFT}; border: 1px solid {ACCENT}; border-radius: {R2}px; }}
QLabel#cellCaption {{ font-size: 12px; color: {TEXT_2}; }}
QLabel#cellValue {{ font-size: 14px; font-weight: 600; }}
QLabel#status {{ padding: 5px 12px; border-radius: 13px; background: {CHIP}; color: {TEXT_2}; font-size: 13px; }}
QLabel#status[state="running"] {{ background: {OK_BG}; color: {OK_FG}; }}
QLabel#status[state="busy"] {{ background: {BUSY_BG}; color: {BUSY_FG}; }}

QPushButton {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {R}px; padding: 6px 14px; min-height: 20px; }}
QPushButton:hover {{ background: {BG_SOFT}; border-color: {BORDER_HOVER}; }}
QPushButton:pressed {{ background: {PRESSED}; }}
QPushButton:disabled {{ color: {TEXT_3}; border-color: {LINE}; }}
QPushButton:checked {{ background: {ACCENT_SOFT}; border-color: {ACCENT}; color: {ACCENT}; }}
QPushButton#primary {{ background: {PRIMARY}; color: {ON_PRIMARY}; border: none; border-radius: {RP}px; padding: 0 28px;
                       min-height: 40px; font-size: 15px; font-weight: 600; }}
QPushButton#primary:hover {{ background: {PRIMARY_HOVER}; }}
QPushButton#primary[running="true"] {{ background: {STOP}; color: {ON_STOP}; }}
QPushButton#primary[running="true"]:hover {{ background: {STOP_HOVER}; }}
QPushButton#link {{ border: none; background: transparent; color: {ACCENT}; padding: 2px 4px; }}
QPushButton#iconBtn {{ border: none; background: transparent; padding: 0; min-height: 0; min-width: 0;
                       color: rgba(255,255,255,0.65); font-size: 14px; }}
QPushButton#iconBtn:hover {{ color: white; background: rgba(255,255,255,0.12); border-radius: 11px; }}

QMenu {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {R2}px; padding: 8px 6px; }}
QMenu::item {{ padding: 8px 28px 8px 14px; border-radius: {RS}px; min-width: 150px; color: {TEXT}; }}
QMenu::item:selected {{ background: {CHIP}; }}
QMenu::item:disabled {{ color: {TEXT_3}; }}
QMenu::separator {{ height: 1px; background: {LINE}; margin: 6px 8px; }}
QMenu::indicator {{ width: 16px; height: 16px; border-radius: 8px; border: 1px solid {BORDER_HOVER}; background: {SURFACE};
                    margin-left: 4px; }}
QMenu::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{CHECK}"); }}
QMenu::indicator:unchecked {{ image: none; }}
QMenu::item:checked, QMenu::item:unchecked {{ padding-left: 14px; }}

QFrame#card {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {RC}px; }}
QLabel#cardTitle {{ font-size: 14px; font-weight: 600; }}
QFrame#banner {{ background: {WARN_BG}; border: 1px solid {WARN_LINE}; border-radius: {R2}px; }}
QFrame#banner QLabel {{ color: {WARN_FG}; }}
QLabel#muted {{ color: {TEXT_2}; }}
QLabel#devWarn {{ padding: 8px 10px; border-radius: {R}px; font-size: 12px; background: {INFO_BG}; color: {INFO_FG}; }}
QLabel#devWarn[level="conflict"] {{ background: {DANGER_BG}; color: {DANGER_FG}; }}
QDialog#msgbox {{ background: {BG}; }}
QLabel#msgTitle {{ font-size: 16px; font-weight: 700; }}
QLabel#msgText {{ font-size: 13px; color: {NAV_TEXT}; }}
QLabel#msgBadge {{ min-width: 36px; max-width: 36px; min-height: 36px; max-height: 36px; border-radius: 18px;
                   font-size: 18px; font-weight: 700; qproperty-alignment: AlignCenter; }}
QLabel#msgBadge[kind="info"] {{ background: {INFO_BG}; color: {INFO_FG}; }}
QLabel#msgBadge[kind="warning"] {{ background: {BUSY_BG}; color: {BUSY_FG}; }}
QLabel#msgBadge[kind="question"] {{ background: {ACCENT_SOFT}; color: {ACCENT}; }}
QLabel#msgBadge[kind="critical"] {{ background: {DANGER_BG}; color: {DANGER_FG}; }}

QComboBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {R}px; padding: 5px 10px; min-height: 20px;
             combobox-popup: 0; }}
QComboBox:hover {{ border-color: {BORDER_HOVER}; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url("{CHEVRON}"); width: 12px; height: 12px; }}
QComboBox::down-arrow:on {{ top: 1px; }}
QFrame#comboPopup {{ background: transparent; border: none; }}
QComboBox QAbstractItemView {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {R}px; padding: 4px;
                               selection-background-color: {ACCENT_SOFT}; selection-color: {ACCENT}; outline: 0; }}
QLineEdit, QPlainTextEdit, QTextEdit, QSpinBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: {R}px;
                                                   padding: 6px 8px; }}
QLineEdit:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {ACCENT}; }}
QCheckBox, QRadioButton {{ spacing: 8px; padding: 1px 0; }}
QGroupBox {{ border: 1px solid {LINE}; border-radius: {RC}px; margin-top: 14px; padding: 12px 10px 8px 10px; font-weight: 600; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; background: {BG}; }}

QProgressBar {{ border: none; background: {PRESSED}; border-radius: 4px; text-align: center; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QSlider::horizontal {{ min-height: 24px; }}
QSlider::groove:horizontal {{ height: 4px; background: {PRESSED}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 16px; height: 16px; margin: -6px 0; border-radius: 8px; background: {SURFACE};
                              border: 2px solid {ACCENT}; }}

QListWidget#log {{ background: {BG_SOFT}; border: none; border-radius: {R2}px; padding: 6px; outline: 0; }}
QListWidget#log::item {{ padding: 8px 10px; border-radius: {R}px; margin: 2px 0; background: {SURFACE}; }}
QListWidget#log::item:selected {{ background: {ACCENT_SOFT}; }}
QTableWidget {{ background: {SURFACE}; border: 1px solid {LINE}; border-radius: {R}px; gridline-color: {LINE}; }}
QHeaderView::section {{ background: {BG_SOFT}; border: none; border-bottom: 1px solid {LINE}; padding: 6px; font-weight: 600; }}

QScrollBar:vertical {{ background: transparent; width: 8px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 4px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {SCROLL_HOVER}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{ background: transparent; height: 8px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 4px; min-width: 24px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea#page, QScrollArea#page > QWidget > QWidget#content {{ background: {BG}; }}
"""


def qss(t: Theme) -> str:
    tokens = {k.upper(): v for k, v in vars(t).items()}
    return _QSS.format(FONTS=FONTS, CHEVRON=_chevron(t), CHECK=_asset("check-white.svg"), **tokens)


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
    def show(parent, title, text, buttons=None, default=_SB.NoButton):
        if buttons is None:  # Qt's own defaults: a question is 是 / 否, the rest just 好的
            buttons = (_SB.Yes | _SB.No) if kind == "question" else _SB.Ok
        d = MessageDialog(parent, kind, title, text, buttons, default)
        d.exec()
        if d.result_button == _SB.Cancel and not (buttons & _SB.Cancel):
            return _SB.No if buttons & _SB.No else _SB.Ok  # dismissed: map to the safe answer
        return d.result_button
    return staticmethod(show)


def toast(parent, text: str, ms: int = 2500) -> None:
    """A short confirmation over the top of `parent`'s window that goes away by itself."""
    win = parent.window() if parent is not None else None
    if win is None:
        return
    lb = QLabel(text, win, objectName="toast")
    lb.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    lb.adjustSize()
    lb.move(max(8, (win.width() - lb.width()) // 2), 18)
    lb.show()
    lb.raise_()
    QTimer.singleShot(ms, lb.deleteLater)


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


class _ComboPopups(QObject):
    """Drop-down lists draw their own rounded box; the window Qt wraps them in painted a square
    frame and background around it. Make that window frameless and see-through."""

    def eventFilter(self, obj, ev) -> bool:  # noqa: N802 — Qt override
        if ev.type() == QEvent.Type.Polish and isinstance(obj, QComboBox) and not obj.property("mvPopup"):
            obj.setProperty("mvPopup", True)
            box = obj.view().parentWidget()  # Qt's private popup container (a QFrame)
            if box is not None and box.isWindow():
                flags = box.windowFlags() | Qt.WindowType.FramelessWindowHint
                if sys.platform != "darwin":  # Windows draws a square shadow; macOS shades the visible shape
                    flags |= Qt.WindowType.NoDropShadowWindowHint
                box.setWindowFlags(flags)
                box.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
                box.setObjectName("comboPopup")
                if isinstance(box, QFrame):
                    box.setFrameShape(QFrame.Shape.NoFrame)
        return False


_combo_popups = _ComboPopups()


def apply(app: QApplication, key: str = "") -> None:
    """Once at start-up: style, translations, message boxes, and the theme (key: Settings.theme)."""
    app.setStyle("Fusion")
    app.installEventFilter(_combo_popups)
    load_translations(app)
    install_message_boxes()
    use(app, key or DEFAULT)


def use(app: QApplication, key: str) -> Theme:
    """Switch to a theme now (an unknown key: the default). Windows restyle themselves; what is
    painted by hand (switches, the conversation) reads current() the next time it paints."""
    global _current
    t = _current = THEMES.get(key, THEMES[DEFAULT])
    pal = app.palette()
    pal.setColor(QPalette.ColorRole.Window, QColor(t.bg))
    pal.setColor(QPalette.ColorRole.Base, QColor(t.surface))
    pal.setColor(QPalette.ColorRole.AlternateBase, QColor(t.bg_soft))
    pal.setColor(QPalette.ColorRole.Text, QColor(t.text))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(t.text))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(t.text))
    pal.setColor(QPalette.ColorRole.Button, QColor(t.surface))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(t.accent))   # check marks, selections
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor(t.on_primary))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(t.text_3))
    app.setPalette(pal)
    app.setStyleSheet(qss(t))
    for w in app.allWidgets():
        w.update()
    return t


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
