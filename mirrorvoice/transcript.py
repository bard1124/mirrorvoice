"""Per-session bilingual transcript: Markdown for reading + CSV for spreadsheets."""
from __future__ import annotations

import csv
import threading
import time
from pathlib import Path

from .config import TRANSCRIPTS
from .languages import label


class Transcript:
    def __init__(self, my_lang: str, their_lang: str) -> None:
        stamp = time.strftime("%Y-%m-%d_%H%M%S")
        self.md = TRANSCRIPTS / f"{stamp}.md"
        self.csv = TRANSCRIPTS / f"{stamp}.csv"
        self.lock = threading.Lock()
        self.md.write_text(
            f"# 会议记录 {time.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"我说 {label(my_lang)} · 对方说 {label(their_lang)}\n\n", encoding="utf-8")
        with open(self.csv, "w", encoding="utf-8-sig", newline="") as f:
            csv.writer(f).writerow(["time", "speaker", "original", "translation"])

    def add(self, speaker: str, original: str, translation: str) -> None:
        t = time.strftime("%H:%M:%S")
        who = "我" if speaker == "me" else "对方"
        with self.lock:
            with open(self.md, "a", encoding="utf-8") as f:
                f.write(f"**{who}** `{t}`  \n{original}  \n> {translation}\n\n")
            with open(self.csv, "a", encoding="utf-8-sig", newline="") as f:
                csv.writer(f).writerow([t, who, original, translation])

    @property
    def folder(self) -> Path:
        return TRANSCRIPTS
