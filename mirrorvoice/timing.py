"""Per-sentence latency breakdown and a rolling summary (shared by the control panel and the CLI).

Stages, all in seconds, measured from the moment the sentence was cut by the VAD:
  asr_s       speech recognition
  queue_s     waiting behind earlier sentences on the model thread
  translate_s LLM translation
  voice_s     until the first synthesized audio is playing (my side only)
  latency     total, cut -> first audio (my side) or cut -> subtitle (their side)
"""
from __future__ import annotations

from collections import deque

STAGES = (("asr_s", "识别"), ("queue_s", "排队"), ("translate_s", "翻译"), ("voice_s", "发声"))


def breakdown(d: dict) -> str:
    """'识别 0.3s · 翻译 0.8s · 发声 0.5s = 1.6s' (queue only shown when it mattered)."""
    parts = []
    for key, name in STAGES:
        v = d.get(key)
        if v is None or (key == "queue_s" and v < 0.3):
            continue
        parts.append(f"{name} {v:.1f}s")
    total = d.get("latency")
    if not parts:
        return f"{total:.1f}s" if total is not None else ""
    return " · ".join(parts) + (f" = {total:.1f}s" if total is not None else "")


class LatencyStats:
    """Averages over the last `window` translated sentences of my side, with the slowest stage."""

    def __init__(self, window: int = 20) -> None:
        self.rows: deque[dict] = deque(maxlen=window)

    def add(self, d: dict) -> None:
        if d.get("passthrough") or d.get("latency") is None:
            return
        self.rows.append(d)

    def averages(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for key in [k for k, _ in STAGES] + ["latency"]:
            vals = [r[key] for r in self.rows if r.get(key) is not None]
            if vals:
                out[key] = sum(vals) / len(vals)
        return out

    def summary(self, quality: str = "") -> str:
        if not self.rows:
            return ""
        avg = self.averages()
        stages = {k: avg[k] for k, _ in STAGES if k in avg}
        slow_key = max(stages, key=stages.get) if stages else ""
        names = dict(STAGES)
        text = f"最近 {len(self.rows)} 句平均延迟 {avg.get('latency', 0):.1f}s"
        if slow_key:
            text += f"，最慢的是{names[slow_key]}（{stages[slow_key]:.1f}s）"
        if slow_key == "translate_s" and stages[slow_key] > 1.2 and quality in ("fast", "quality"):
            text += "，可把“模型”改成更快的一档"
        elif slow_key == "queue_s" and stages[slow_key] > 1.0:
            text += "，句子在排队：说话时稍作停顿，或换更快的模型"
        elif slow_key == "voice_s" and stages[slow_key] > 1.5:
            text += "，语音合成较慢"
        return text
