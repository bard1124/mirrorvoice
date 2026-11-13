"""How a sentence is drawn in the main window's conversation, per theme.

    rows     (静场)   who · original (small) / translation (larger), time at the right
    cards    (调音台) the same on a dark card, the time in a fixed-width face
    bubbles  (对话)   mine at the right in the accent colour, theirs at the left

Each row of the list keeps its plain text (copying, tests, screen readers) and carries what it is
made of in ROLE: {"who": "me" | "them" | "note", "src", "tgt", "time", "live"}. A row without it
is drawn by Qt as before.
"""
from __future__ import annotations

from PySide6.QtCore import QModelIndex, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

from . import theme

ROLE = Qt.ItemDataRole.UserRole + 1
WRAP = int(Qt.TextFlag.TextWordWrap)  # (not WrapAnywhere: it cut English words in two)
WHO = {"me": "我", "me_raw": "我·原声", "them": "对方"}


def entry(who: str, src: str = "", tgt: str = "", time: str = "", live: bool = False) -> dict:
    return {"who": who, "src": src, "tgt": tgt, "time": time, "live": live}


class ConvoDelegate(QStyledItemDelegate):
    PAD = 10        # inside a row / card / bubble
    GAP = 6         # between rows
    TAG_W = 52      # rows: the "我 / 对方" column
    BUBBLE = 0.8    # bubbles: widest, as a share of the list

    def _fonts(self, option: QStyleOptionViewItem) -> tuple[QFont, QFont]:
        small = QFont(option.font)
        small.setPixelSize(12)
        big = QFont(option.font)
        big.setPixelSize(15)
        big.setWeight(QFont.Weight.DemiBold if theme.current().convo == "bubbles" else QFont.Weight.Medium)
        return small, big

    @staticmethod
    def _h(font: QFont, text: str, width: int) -> int:
        if not text:
            return 0
        return QFontMetrics(font).boundingRect(QRect(0, 0, max(40, width), 100000), WRAP, text).height()

    @staticmethod
    def _w(font: QFont, text: str, width: int) -> int:
        if not text:
            return 0
        return QFontMetrics(font).boundingRect(QRect(0, 0, max(40, width), 100000), WRAP, text).width() + 6

    def _width(self, option: QStyleOptionViewItem) -> int:
        view = self.parent()
        w = view.viewport().width() if view is not None else option.rect.width()
        return max(160, w)

    def _layout(self, d: dict, option: QStyleOptionViewItem) -> dict:
        """Where everything goes in a row `full` px wide: text widths and heights, the box's size."""
        t, full = theme.current(), self._width(option)
        small, big = self._fonts(option)
        time_w = QFontMetrics(small).horizontalAdvance(d["time"]) + 8 if d["time"] else 0
        if d["who"] == "note":
            w = full - 2 * self.PAD
            return {"mode": "note", "w": w, "h": self._h(small, d["src"], w) + 8, "small": small, "big": big}
        if t.convo == "bubbles":
            inner = int(full * self.BUBBLE) - 2 * self.PAD - 8
            text_w = max(self._w(small, d["src"], inner), self._w(big, d["tgt"], inner), 60)
            src_h, tgt_h = self._h(small, d["src"], text_w), self._h(big, d["tgt"], text_w)
            box_h = src_h + tgt_h + 2 * self.PAD + (2 if src_h and tgt_h else 0)
            return {"mode": "bubbles", "text_w": text_w, "src_h": src_h, "tgt_h": tgt_h, "box_w": text_w + 2 * self.PAD + 8,
                    "box_h": box_h, "h": box_h + (16 if d["time"] else 0) + self.GAP, "small": small, "big": big}
        lead = self.TAG_W if t.convo == "rows" else 0
        text_w = full - lead - 2 * self.PAD - 4
        src_w = text_w - time_w
        src_h, tgt_h = self._h(small, d["src"], src_w), self._h(big, d["tgt"], text_w)
        box_h = max(src_h, 16 if time_w else 0) + tgt_h + 2 * self.PAD + (2 if tgt_h else 0)
        return {"mode": t.convo, "lead": lead, "text_w": text_w, "src_w": src_w, "src_h": src_h, "tgt_h": tgt_h,
                "time_w": time_w, "box_h": box_h, "h": box_h + self.GAP, "small": small, "big": big}

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        d = index.data(ROLE)
        if not isinstance(d, dict):
            return super().sizeHint(option, index)
        return QSize(self._width(option), self._layout(d, option)["h"])

    def paint(self, p: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        d = index.data(ROLE)
        if not isinstance(d, dict):
            super().paint(p, option, index)
            return
        t, lay, r = theme.current(), self._layout(d, option), option.rect
        mine = d["who"].startswith("me")
        who_color = QColor(t.me if mine else t.them)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if lay["mode"] == "note":
            p.setFont(lay["small"])
            p.setPen(QColor(t.text_2))
            p.drawText(QRect(r.x() + self.PAD, r.y() + 2, lay["w"], lay["h"]), WRAP, d["src"])
            p.restore()
            return
        if lay["mode"] == "bubbles":
            x = r.right() - lay["box_w"] - 4 if mine else r.x() + 4
            box = QRectF(x, r.y(), lay["box_w"], lay["box_h"])
            if d["live"]:
                pen = QPen(QColor(t.live), 1.5, Qt.PenStyle.DashLine)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                ink_small, ink_big = QColor(t.text_2), QColor(t.live)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.me_bubble if mine else t.them_bubble))
                ink_big = QColor(t.on_me_bubble if mine else t.on_them_bubble)
                ink_small = QColor(ink_big)
                ink_small.setAlphaF(0.78)
            p.drawRoundedRect(box, 16, 16)
            tx, ty = int(x) + self.PAD + 4, r.y() + self.PAD
            p.setFont(lay["small"])
            p.setPen(ink_small)
            p.drawText(QRect(tx, ty, lay["text_w"], lay["src_h"]), WRAP, d["src"])
            p.setFont(lay["big"])
            p.setPen(ink_big)
            p.drawText(QRect(tx, ty + lay["src_h"] + (2 if lay["src_h"] else 0), lay["text_w"], lay["tgt_h"]), WRAP, d["tgt"])
            if d["time"]:
                p.setFont(lay["small"])
                p.setPen(QColor(t.text_2))
                flag = Qt.AlignmentFlag.AlignRight if mine else Qt.AlignmentFlag.AlignLeft
                p.drawText(QRect(int(x) + 6, r.y() + lay["box_h"] + 1, lay["box_w"] - 12, 15), int(flag), d["time"])
            p.restore()
            return
        box = QRectF(r.x(), r.y(), r.width(), lay["box_h"])
        if lay["mode"] == "cards":
            if d["live"]:
                p.setPen(QPen(QColor(t.border), 1, Qt.PenStyle.DashLine))
                p.setBrush(Qt.BrushStyle.NoBrush)
            else:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(t.surface))
            p.drawRoundedRect(box.adjusted(0.5, 0.5, -0.5, -0.5), t.r2, t.r2)
        x, y = r.x() + self.PAD, r.y() + self.PAD
        p.setFont(lay["small"])
        if lay["lead"]:  # rows: who, in their colour, in a column of its own
            tag = QFont(lay["small"])
            tag.setWeight(QFont.Weight.DemiBold)
            p.setFont(tag)
            p.setPen(who_color)
            p.drawText(QRect(x, y, lay["lead"] - 6, 16), int(Qt.AlignmentFlag.AlignLeft), WHO.get(d["who"], ""))
            x += lay["lead"]
            p.setFont(lay["small"])
            p.setPen(QColor(t.text_2))
            p.drawText(QRect(x, y, lay["src_w"], lay["src_h"]), WRAP, d["src"])
        else:  # cards: who leads the original line
            p.setPen(QColor(t.text_2))
            p.drawText(QRect(x, y, lay["src_w"], lay["src_h"]), WRAP, d["src"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(who_color)
            p.drawEllipse(QRectF(r.x() + 3, y + 5, 5, 5))  # a dot of the speaker's colour
        if d["time"]:
            f = QFont(lay["small"])
            if lay["mode"] == "cards":
                f.setFamilies(["JetBrains Mono", "Menlo", "Consolas"])
            p.setFont(f)
            p.setPen(QColor(t.live if d["live"] else t.text_2))
            p.drawText(QRect(r.right() - self.PAD - lay["time_w"], y, lay["time_w"], 16),
                       int(Qt.AlignmentFlag.AlignRight), d["time"])
        p.setFont(lay["big"])
        p.setPen(QColor(t.live if d["live"] else t.text))
        top = y + max(lay["src_h"], 16 if lay["time_w"] else 0) + 2
        p.drawText(QRect(x, top, lay["text_w"], lay["tgt_h"]), WRAP, d["tgt"])
        p.restore()
