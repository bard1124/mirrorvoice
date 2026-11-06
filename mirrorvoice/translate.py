"""LLM translation: MLX (Apple Silicon) or llama.cpp's llama-server (Windows / fallback)."""
from __future__ import annotations

import csv
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

from .config import GLOSSARY_FILE, IS_MAC, IS_WIN, resource_dir, kill_with_parent
from .languages import LANGS

# ------------------------------------------------------------------ glossary


def load_glossary() -> list[tuple[str, str]]:
    if not GLOSSARY_FILE.exists():
        return []
    with open(GLOSSARY_FILE, encoding="utf-8-sig", newline="") as f:
        return [(r[0].strip(), r[1].strip()) for r in csv.reader(f) if len(r) >= 2 and r[0].strip()]


def save_glossary(rows: list[tuple[str, str]]) -> None:
    with open(GLOSSARY_FILE, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([r for r in rows if r[0].strip() and r[1].strip()])


def glossary_hits(text: str, rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Terms present in `text`, in either direction (glossary is written my_lang -> their_lang)."""
    low = text.lower()
    hits = []
    for a, b in rows:
        if a.lower() in low:
            hits.append((a, b))
        elif b.lower() in low:
            hits.append((b, a))
    return hits


# ------------------------------------------------------------------ prompts


def system_prompt(src: str, tgt: str, subtitle: bool) -> str:
    s, t = LANGS[src].english, LANGS[tgt].english
    p = (f"You are a simultaneous interpreter. Translate the {s} speech transcript into natural, "
         f"concise spoken {t}. The transcript comes from speech recognition and may contain "
         "recognition errors or filler words; fix obvious errors and drop fillers. Keep names, "
         "product terms and numbers accurate. ")
    if tgt == "en" and not subtitle:
        p += ("Write every number, percentage, time and date out in English words "
              "(e.g. 'twenty percent', 'three p.m.'), never digits. ")
    if subtitle and tgt in ("zh", "yue"):
        p += "Use Simplified Chinese. Keep names, product names and technical terms in their original form. "
    return p + f"Output ONLY the {t} translation, no notes, no quotes."


def user_prompt(text: str, glossary: list[tuple[str, str]], continuation: bool = False) -> str:
    g = ""
    if glossary:
        g = "Use these fixed translations: " + "; ".join(f"{a} -> {b}" for a, b in glossary) + "\n\n"
    if continuation:
        return (f"{g}The speaker continues the same sentence. Translate ONLY this new part so that it reads "
                f"naturally right after your previous output; do not repeat what you already said: {text}")
    return f"{g}Translate ONLY this sentence: {text}"


def last_line(text: str) -> str:
    """Drop <think> blocks; if the model echoed extra lines keep only the final one."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    return (lines[-1] if lines else "").strip('"“”')


_NUM = re.compile(r"(?<![\w.])(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(st|nd|rd|th)?(%)?")


def spoken_en(text: str) -> str:
    """Spell out digits so a cloned (non-English reference) voice reads them in English."""
    from num2words import num2words

    def sub(m: re.Match) -> str:
        whole, frac, ordinal, pct = m.groups()
        n = int(whole.replace(",", ""))
        if ordinal:
            words = num2words(n, to="ordinal")
        elif frac:
            words = num2words(float(f"{n}.{frac}"))
        elif 1900 <= n <= 2099 and not pct and "," not in whole:
            words = num2words(n, to="year")
        else:
            words = num2words(n)
        return words + (" percent" if pct else "")

    text = re.sub(r"(\d{1,2}):(\d{2})",
                  lambda m: f"{int(m[1])} {m[2]}" if m[2] != "00" else f"{int(m[1])} o'clock", text)
    text = _NUM.sub(sub, text)
    return text.replace("%", " percent").replace("&", " and ").replace("$", " dollars ")


# ------------------------------------------------------------------ backends


class Translator:
    """translate(text, src, tgt, subtitle) -> str. Create and use on ONE thread (MLX)."""

    def __init__(self, model_path: Path) -> None:
        self.glossary = load_glossary()
        self.backend = _MLX(model_path) if IS_MAC else _LlamaServer(model_path)

    def reload_glossary(self) -> None:
        self.glossary = load_glossary()

    def translate(self, text: str, src: str, tgt: str, subtitle: bool = False,
                  context: tuple[str, str] | None = None) -> str:
        """context = (previous part's source, its translation) when `text` continues that part
        (a long sentence cut early): shown to the model as the preceding exchange."""
        msgs = [{"role": "system", "content": system_prompt(src, tgt, subtitle)}]
        if context:
            msgs += [{"role": "user", "content": user_prompt(context[0], glossary_hits(context[0], self.glossary))},
                     {"role": "assistant", "content": context[1]}]
        msgs.append({"role": "user", "content": user_prompt(text, glossary_hits(text, self.glossary),
                                                            continuation=context is not None)})
        return last_line(self.backend.chat(msgs))

    def close(self) -> None:
        self.backend.close()


class _MLX:
    def __init__(self, path: Path) -> None:
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler

        self.model, self.tok = load(str(path))
        self.sampler = make_sampler(temp=0.0)

    def chat(self, msgs: list[dict]) -> str:
        from mlx_lm import generate

        prompt = self.tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                              enable_thinking=False)
        return generate(self.model, self.tok, prompt, max_tokens=200, sampler=self.sampler)

    def close(self) -> None:
        pass


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def llama_server_candidates() -> list[Path]:
    """GPU (Vulkan) build first, CPU build as fallback; MIRRORVOICE_LLAMA_SERVER overrides."""
    if os.environ.get("MIRRORVOICE_LLAMA_SERVER"):
        return [Path(os.environ["MIRRORVOICE_LLAMA_SERVER"])]
    exe = "llama-server.exe" if IS_WIN else "llama-server"
    base = resource_dir() / "llama"
    return [p for p in (base / "vulkan" / exe, base / "cpu" / exe, base / exe) if p.exists()]


class _LlamaServer:
    def __init__(self, gguf: Path) -> None:
        errors = []
        for exe in llama_server_candidates():
            self.port = _free_port()
            self.proc = subprocess.Popen(
                [str(exe), "-m", str(gguf), "--port", str(self.port), "--host", "127.0.0.1",
                 "-ngl", "99", "-c", "4096", "--jinja", "--no-webui"],
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            kill_with_parent(self.proc)
            if self._wait_ready(90):
                return
            err = self.proc.stderr.read(4000).decode(errors="ignore") if self.proc.poll() is not None else "timeout"
            errors.append(f"{exe.parent.name}: {err[-500:]}")
            self.close()
        raise RuntimeError("llama-server 启动失败：\n" + "\n".join(errors or ["未找到 llama-server"]))

    def _wait_ready(self, timeout: float) -> bool:
        t0 = time.time()
        while time.time() - t0 < timeout:
            if self.proc.poll() is not None:
                return False
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health", timeout=2) as r:
                    if r.status == 200:
                        return True
            except Exception:  # noqa: BLE001 — still loading
                time.sleep(0.5)
        return False

    def chat(self, msgs: list[dict]) -> str:
        body = json.dumps({"messages": msgs, "temperature": 0, "max_tokens": 200,
                           "chat_template_kwargs": {"enable_thinking": False}}).encode()
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)["choices"][0]["message"]["content"]

    def close(self) -> None:
        if getattr(self, "proc", None) and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
