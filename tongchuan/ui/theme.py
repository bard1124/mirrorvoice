"""Light theme shared by every window: Fusion style + palette + one stylesheet.

Fusion draws the same on macOS and Windows, so the stylesheet below is the whole look: a
sidebar on pale grey, white content, rounded cards, one accent colour for the primary action.
"""
from __future__ import annotations

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QVBoxLayout, QWidget

ACCENT = "#E8432E"
ACCENT_DARK = "#CF3621"
ACCENT_SOFT = "#FFF1EE"
TEXT = "#1F2329"
TEXT_2 = "#6B7280"
LINE = "#E8EAEE"
BG = "#FFFFFF"
BG_SIDE = "#F5F6F8"
BG_SOFT = "#F7F8FA"

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

QComboBox {{ background: {BG}; border: 1px solid #DFE2E7; border-radius: 8px; padding: 5px 10px; min-height: 20px; }}
QComboBox:hover {{ border-color: #C9CDD4; }}
QComboBox:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
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


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
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
