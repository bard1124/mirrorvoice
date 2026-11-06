"""Supported languages and their names for each component."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Lang:
    code: str        # our code (= SenseVoice language code)
    label: str       # shown in the UI
    english: str     # used in LLM prompts
    tts: str | None  # Qwen3-TTS lang_code; None = cannot be spoken (subtitle only)


LANGS: dict[str, Lang] = {
    "zh": Lang("zh", "中文（普通话）", "Mandarin Chinese", "chinese"),
    "en": Lang("en", "English", "English", "english"),
    "ja": Lang("ja", "日本語", "Japanese", "japanese"),
    "ko": Lang("ko", "한국어", "Korean", "korean"),
    "yue": Lang("yue", "粤语", "Cantonese", None),
}


def label(code: str) -> str:
    return LANGS[code].label if code in LANGS else code


def sensevoice_lang_tag(tag: str) -> str:
    """SenseVoice result.lang looks like '<|en|>'; return 'en'."""
    return tag.strip("<|>") if tag else ""
