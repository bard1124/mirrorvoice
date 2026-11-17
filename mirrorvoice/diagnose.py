"""诊断包: one file to send when the app is slow or wrong on somebody's machine.

Every review of a meeting so far began with "send me your debug.log" — and, for the other side's
machine, usually ended there: the log sits in a hidden folder, and it holds every word that was
said. build() packs what is needed to see where the time goes: the machine, the settings, the
models, and per meeting the figures that were worked out by hand each time (how long each stage
took, how the speech was cut, how often the microphone was overloaded).

By default nothing that was said leaves the machine: the log goes in with every quoted text
replaced by its length, and no meeting record is added. with_text=True adds the words (the log as
it is, the last meeting records, the glossary) for when the translation itself is the question.
"""
from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import __version__
from .config import GLOSSARY_FILE, HOME, IS_MAC, IS_WIN, LOG_FILE, MODELS, SETTINGS_FILE, TRANSCRIPTS

SESSIONS = 5      # the last meetings in the report
RECORDS = 3       # with_text: the last meeting records added
START = "===== 开始"
# settings that are words of the user's, not switches (left out unless with_text)
PRIVATE_SETTINGS = ("meeting_context", "llm_base_url", "extra")

_QUOTED = re.compile(r"""'(?:[^'\\]|\\.)*'|"(?:[^"\\]|\\.)*\"""")


def redact(line: str) -> str:
    """The line without what was said: every quoted text becomes its length. The home folder in a
    path (a traceback) becomes ~."""
    line = _QUOTED.sub(lambda m: f"‹{max(0, len(m.group()) - 2)} 字符›", line)
    return line.replace(str(Path.home()), "~")


# ---------------------------------------------------------------- the machine
def _run(cmd: list[str], timeout: float = 8.0) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout.strip()
    except Exception:  # noqa: BLE001 — a figure less in the report
        return ""


def machine() -> dict:
    """CPU, memory and graphics as the user would name them (the first thing asked on 2026-11-17:
    "can this laptop run it smoothly?")."""
    info = {"系统": f"{platform.system()} {platform.release()} ({platform.machine()})", "处理器": "", "内存": "", "显卡": ""}
    if IS_MAC:
        info["系统"] = f"macOS {platform.mac_ver()[0]} ({platform.machine()})"
        info["处理器"] = _run(["sysctl", "-n", "machdep.cpu.brand_string"])
        mem = _run(["sysctl", "-n", "hw.memsize"])
        info["内存"] = f"{int(mem) / (1 << 30):.0f} GB" if mem.isdigit() else ""
        info["显卡"] = "Apple GPU（与处理器一体）" if platform.machine() == "arm64" else ""
    elif IS_WIN:
        out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-CimInstance Win32_Processor).Name; "
                    "[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB); "
                    "(Get-CimInstance Win32_VideoController | ForEach-Object { $_.Name }) -join ' / '"], timeout=15)
        rows = [r.strip() for r in out.splitlines() if r.strip()]
        if len(rows) >= 3:
            info["处理器"], info["内存"], info["显卡"] = rows[0], f"{rows[1]} GB", rows[2]
    info["处理器"] = info["处理器"] or platform.processor() or "未知"
    info["CPU 线程数"] = str(os.cpu_count() or "")
    return info


def installed_models() -> list[str]:
    rows = []
    try:
        for p in sorted(MODELS.iterdir()):
            if p.name.startswith("."):
                continue
            files = [f for f in p.rglob("*") if f.is_file()] if p.is_dir() else [p]
            size = sum(f.stat().st_size for f in files)
            whole = "" if not p.is_dir() or (p / ".complete").exists() else "（未下完）"
            rows.append(f"{p.name}  {size / (1 << 30):.2f} GB{whole}")
    except OSError:
        pass
    return rows


def settings_view(with_text: bool = False) -> dict:
    try:
        raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not with_text:
        for k in PRIVATE_SETTINGS:
            if raw.get(k):
                raw[k] = "‹已省略›"
    home = str(Path.home())
    return {k: (v.replace(home, "~") if isinstance(v, str) else v) for k, v in raw.items()}


# ---------------------------------------------------------------- the meetings
def sessions(lines: list[str]) -> list[list[str]]:
    """The log, one list per 开始."""
    out: list[list[str]] = []
    for line in lines:
        if START in line:
            out.append([line])
        elif out:
            out[-1].append(line)
    return out


def _secs(line: str) -> int:
    try:
        return int(line[0:2]) * 3600 + int(line[3:5]) * 60 + int(line[6:8])
    except ValueError:
        return -1


def _q(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else 0.0


def _spread(xs: list[float]) -> dict:
    return {"n": len(xs), "中位": round(_q(xs, 0.5), 2), "九成": round(_q(xs, 0.9), 2), "最长": round(max(xs), 2)} if xs else {"n": 0}


_ME = re.compile(r"\[me\] 排队 ([\d.]+)s 翻译 ([\d.]+)s（先念 ([\d.]+)s） 出声 (-?[\d.]+)s.*?说完到出声 ([\d.]+)s")
_THEM = re.compile(r"\[them\] 排队 ([\d.]+)s 翻译 ([\d.]+)s")
_SEG = re.compile(r"\[(me|them)\] segment ([\d.]+)s lang=(\w*)")
_BEAT = re.compile(r"\[(me|them)\] 10s: .*?峰值=([\d.]+)")
_PREVIEW = re.compile(r"\[them\] 预览译文：已说 [\d.]+s，停顿后 ([\d.]+)s")
# what is counted, by the words the engine logs
COUNTS = {"我方": {"接上一小段": "接上一小段", "连续讲述": "连续讲述", "太短未送出": "不当作原声", "声音太轻未翻译": "声音太轻",
                   "原声透传": "原声透传", "原声直说": "原声直说：'", "边说边译": "边说边译", "翻译失败": "在线翻译失败"},
          "对方": {"小段先显示": "小段先显示", "并入上一小段": "并入上一小段", "整段重译": "整段重译", "预览": "预览原文",
                   "长句提前断句": "长句提前断句", "按另一语言重新识别": "重新识别", "未翻译": "未翻译", "回声": "是我自己的发声"}}


def session_stats(lines: list[str]) -> dict:
    """One meeting in figures (no words): what was counted by hand in every review."""
    times = [t for t in (_secs(x) for x in lines) if t >= 0]
    out: dict = {"开始": lines[0][:8] + " " + lines[0].split(START)[-1].strip(" ="), "结束": lines[-1][:8],
                 "时长(分)": round((times[-1] - times[0]) % 86400 / 60, 1) if times else 0}
    me = [tuple(float(x) for x in m.groups()) for m in map(_ME.search, lines) if m]
    out["我方发声"] = {"说完到出声(秒)": _spread([r[4] for r in me]), "超过2.5秒": sum(r[4] > 2.5 for r in me),
                       "排队中位": round(_q([r[0] for r in me], 0.5), 2), "翻译中位": round(_q([r[1] for r in me], 0.5), 2),
                       "出声中位": round(_q([r[3] for r in me if r[3] >= 0], 0.5), 2)}
    them = [tuple(float(x) for x in m.groups()) for m in map(_THEM.search, lines) if m]
    out["对方字幕"] = {"排队(秒)": _spread([r[0] for r in them]), "翻译(秒)": _spread([r[1] for r in them]),
                       "合计超过3秒": sum(r[0] + r[1] > 3 for r in them)}
    previews = [float(m.group(1)) for m in map(_PREVIEW.search, lines) if m]
    if previews:
        out["对方字幕"]["预览译文在停顿后(秒)"] = _spread(previews)
    for who, key in (("me", "我方片段"), ("them", "对方片段")):
        segs = [(float(m.group(2)), m.group(3)) for m in map(_SEG.search, lines) if m and m.group(1) == who]
        langs: dict[str, int] = {}
        for _, lang in segs:
            langs[lang] = langs.get(lang, 0) + 1
        out[key] = {"时长(秒)": _spread([d for d, _ in segs]), "8秒以上": sum(d >= 8 for d, _ in segs), "语言": langs}
    for side, words in COUNTS.items():
        tag = "[me]" if side == "我方" else "[them]"
        out[side + "计数"] = {name: n for name, word in words.items() if (n := sum(tag in x and word in x for x in lines))}
    for who, key in (("me", "麦克风"), ("them", "对方线路")):
        peaks = [float(m.group(2)) for m in map(_BEAT.search, lines) if m and m.group(1) == who]
        out[key] = {"10秒窗口": len(peaks), "超过满刻度": sum(p > 1.0 for p in peaks), "无声": sum(p < 0.01 for p in peaks),
                    "最高峰值": round(max(peaks), 2) if peaks else 0}
    plain = [redact(x) for x in lines]  # (not in what was said: "重新打开那个…" is a sentence, not a fault)
    bad = [x for x in plain if re.search(r"err=(?!None)|overflows=[1-9]|Traceback|失败|不可用|重新打开", x)]
    out["异常行"] = len(bad)
    out["异常示例"] = [x[:160] for x in bad[:5]]
    return out


def _lines(d: dict, pad: str = "  ") -> list[str]:
    rows = []
    for k, v in d.items():
        if isinstance(v, dict):
            flat = "，".join(f"{a} {b}" for a, b in v.items()) if not any(isinstance(b, dict) for b in v.values()) else None
            rows += [f"{pad}{k}：{flat}"] if flat is not None else [f"{pad}{k}：", *_lines(v, pad + "  ")]
        elif isinstance(v, list):
            rows += [f"{pad}{k}：" + ("无" if not v else "")] + [f"{pad}  {x}" for x in v]
        else:
            rows.append(f"{pad}{k}：{v}")
    return rows


def report(with_text: bool = False) -> str:
    """The page that is read first: machine, version, settings, models, the last meetings."""
    try:
        log = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        log = []
    from . import patches

    active = patches._read(patches.APP / "active.json", {}).get("version", "")
    rows = [f"翻了么 诊断报告  {time.strftime('%Y-%m-%d %H:%M')}",
            f"版本：v{__version__}" + (f"（补丁 {active}）" if active and active != __version__ else "")
            + f"　运行方式：{'安装包' if getattr(sys, 'frozen', False) else '源码'}",
            f"对话内容：{'已包含（原始日志、最近的会议记录、术语表）' if with_text else '不包含（日志里说过的话都已替换成长度）'}",
            "", "【这台电脑】", *_lines(machine()),
            "", "【已下载的模型】", *(f"  {m}" for m in installed_models() or ["（没有）"]),
            "", "【设置】", *_lines(settings_view(with_text))]
    recent = sessions(log)[-SESSIONS:]
    rows += ["", f"【最近 {len(recent)} 场】（日志里共 {len(sessions(log))} 场）"]
    for s in reversed(recent):
        rows += ["", *_lines(session_stats(s))]
    return "\n".join(rows) + "\n"


def default_folder() -> Path:
    d = Path.home() / "Downloads"
    return d if d.is_dir() else HOME


def build(folder: Path | None = None, with_text: bool = False) -> Path:
    """Write the zip and return where it is."""
    folder = folder or default_folder()
    dest = folder / f"翻了么诊断-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("诊断报告.txt", report(with_text))
        try:
            log = LOG_FILE.read_text(encoding="utf-8", errors="replace")
        except OSError:
            log = ""
        z.writestr("debug.log", log if with_text else "\n".join(redact(x) for x in log.splitlines()) + "\n")
        z.writestr("settings.json", json.dumps(settings_view(with_text), ensure_ascii=False, indent=2))
        for extra in (HOME / "app" / "boot.log", HOME / "updates" / "update.log", HOME / "tts_server.log"):
            if extra.is_file():
                z.writestr(extra.name, redact(extra.read_text(encoding="utf-8", errors="replace")))
        if with_text:
            for rec in sorted(TRANSCRIPTS.glob("*.csv"))[-RECORDS:]:
                z.write(rec, f"会议记录/{rec.name}")
            if GLOSSARY_FILE.is_file():
                z.write(GLOSSARY_FILE, GLOSSARY_FILE.name)
    return dest
