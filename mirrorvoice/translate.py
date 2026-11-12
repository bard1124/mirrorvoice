"""LLM translation: MLX (Apple Silicon), llama.cpp's llama-server (Windows), or an online
OpenAI-compatible API (online_llm.py) — every backend has chat(messages) -> str."""
from __future__ import annotations

import csv
import json
import os
import re
import socket
import subprocess
import time
import urllib.request
from collections import OrderedDict
from pathlib import Path
from typing import Callable, Iterator

from .config import GLOSSARY_FILE, IS_MAC, IS_WIN, resource_dir, kill_with_parent
from .languages import LANGS

# ------------------------------------------------------------------ glossary


Term = tuple[str, str, str]  # (my wording, their wording, "misheard1|misheard2")


def load_glossary() -> list[Term]:
    """Rows of the glossary CSV: my_lang term, their_lang term, optional ASR mis-hearings ('|'-separated)."""
    if not GLOSSARY_FILE.exists():
        return []
    with open(GLOSSARY_FILE, encoding="utf-8-sig", newline="") as f:
        return [(r[0].strip(), r[1].strip(), r[2].strip() if len(r) > 2 else "")
                for r in csv.reader(f) if len(r) >= 2 and r[0].strip()]


def save_glossary(rows: list[tuple[str, ...]]) -> None:
    keep = [(r[0].strip(), r[1].strip(), (r[2] if len(r) > 2 else "").strip()) for r in rows]
    with open(GLOSSARY_FILE, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([(a, b, c) if c else (a, b) for a, b, c in keep if a and b])


def glossary_hits(text: str, rows: list[Term]) -> list[tuple[str, str]]:
    """Terms present in `text`, in either direction (glossary is written my_lang -> their_lang)."""
    low = text.lower()
    hits = []
    for a, b, *_ in rows:
        if a.lower() in low:
            hits.append((a, b))
        elif b.lower() in low:
            hits.append((b, a))
    return hits


def _alias_pattern(alias: str) -> re.Pattern:
    """Latin aliases match whole words only ('scientific' must not hit 'unscientifically')."""
    esc = re.escape(alias)
    if re.fullmatch(r"[\w\s.\-']+", alias, flags=re.ASCII):
        esc = rf"(?<![A-Za-z0-9]){esc}(?![A-Za-z0-9])"
    return re.compile(esc, re.IGNORECASE)


def correct_asr(text: str, rows: list[Term], side: str) -> str:
    """Replace known speech-recognition mistakes before translating.

    side='me'   -> the mistake becomes column 1 (how I say the term)
    side='them' -> the mistake becomes column 2 (how the other side says it)
    """
    for row in rows:
        a, b, aliases = (tuple(row) + ("",))[:3]
        target = a if side == "me" else b
        for alias in (x.strip() for x in aliases.split("|")):
            if alias and target and alias.lower() != target.lower():
                text = _alias_pattern(alias).sub(target, text)
    return text


# ------------------------------------------------------------------ prompts


# Sounds of hesitation, not words. Translating them is wasted work and worse: small models make
# something up ("呃。" came out as "I.", "嗯嗯。" as "Hmm." in my voice a second and a half later).
_FILLER_WORDS = {"uh", "um", "umm", "uhm", "hm", "hmm", "mm", "mmm", "mhm", "er", "erm", "ah", "eh", "ugh", "huh"}
_FILLER_CHARS = set("嗯呃额啊哦噢喔唔呣诶欸唉哎" "うえあはんおつー")  # (the kana: how noises are misheard)
_WORDS = re.compile(r"[a-z']+|[^\W\d_a-z]")  # Latin words; any other letter on its own


def is_filler(text: str) -> bool:
    """True when the sentence is nothing but hesitation ("嗯。", "呃，嗯", "Um."). Answers stay:
    "好", "对", "Yeah", "Okay" are words — and so does a question ("啊？")."""
    if "?" in text or "？" in text:
        return False
    words = _WORDS.findall(text.lower())
    return bool(words) and all(w in _FILLER_WORDS or w in _FILLER_CHARS for w in words)


def system_prompt(src: str, tgt: str, subtitle: bool, meeting: str = "", concise: int = 0) -> str:
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
    if concise:  # speech is piling up: the gist in about the time the speaker took ("be brief" alone did nothing)
        unit = "words" if tgt not in ("zh", "yue", "ja") else "characters"
        p += (f"Speech is falling behind: give the essential meaning in at most {concise} {unit}; "
              "drop hedges, fillers, repetitions and details the listener can do without. ")
    if meeting.strip():
        p += (f"Background of this meeting (use it to choose the right terminology, never translate it): "
              f"{meeting.strip()[:400]}. ")
    return p + f"Output ONLY the {t} translation, no notes, no quotes."


def user_prompt(text: str, glossary: list[tuple[str, str]], continuation: bool = False) -> str:
    g = ""
    if glossary:
        g = "Use these fixed translations: " + "; ".join(f"{a} -> {b}" for a, b in glossary) + "\n\n"
    if continuation:
        return (f"{g}The speaker continues the same sentence. Translate ONLY this new part so that it reads "
                f"naturally right after your previous output; do not repeat what you already said: {text}")
    return f"{g}Translate ONLY this sentence: {text}"


# Qwen3's own switch: chat_template_kwargs/enable_thinking alone was ignored by Qwen3-1.7B, which then
# "thought out loud" in its answer (11 of 12 test sentences); with this suffix 0 of 12.
NO_THINK = " /no_think"
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def looks_like_reasoning(out: str, tgt: str) -> bool:
    """An English translation never contains Chinese characters; reasoning about the source does."""
    return LANGS[tgt].english == "English" and bool(_CJK.search(out))


# Words a small model uses when it talks about the task instead of doing it ("Okay, let's take this
# sentence:", "the user specified that numbers…") — each only counts when the source has nothing
# it could be translating (a sentence that mentions 翻译 may well come out as "translation").
_TASK_WORDS = (
    (re.compile(r"\bsentences?\b|句子", re.I), re.compile(r"句|sentence", re.I)),
    (re.compile(r"\btranslat\w*|翻译|译文", re.I), re.compile(r"译|translat", re.I)),
    (re.compile(r"\bthe user\b|用户", re.I), re.compile(r"用户|使用者|user", re.I)),
    (re.compile(r"\btranscripts?\b", re.I), re.compile(r"转写|记录|transcript", re.I)),
    (re.compile(r"\binstructions?\b", re.I), re.compile(r"指令|说明|指示|instruct", re.I)),
)


def off_task(out: str, src_text: str, tgt: str) -> bool:
    """The output is reasoning about the sentence, not its translation: never speak or show it."""
    return looks_like_reasoning(out, tgt) or any(o.search(out) and not s.search(src_text) for o, s in _TASK_WORDS)


def token_budget(text: str) -> int:
    """Generous room for the translation of `text` (a runaway model stops here instead of talking
    for seconds): ~1 token per Chinese character / 4 Latin characters, times two, plus slack."""
    return min(200, 32 + 2 * len(text))


# the switch the prompt carries, sometimes copied into the answer by small models ("… say? /no_think")
_TAGS = re.compile(r"\s*/(?:no_)?think\b", re.I)


def strip_tags(text: str) -> str:
    return _TAGS.sub("", text)


def last_line(text: str) -> str:
    """Drop <think> blocks and echoed /no_think tags; if the model echoed extra lines keep only the
    final one."""
    text = strip_tags(re.sub(r"<think>.*?</think>", "", text, flags=re.S))
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


def local_backend(model_path: Path):
    """The local model: MLX on macOS, llama-server elsewhere. Create and use on ONE thread (MLX)."""
    return _MLX(model_path) if IS_MAC else _LlamaServer(model_path)


class Translator:
    """translate(text, src, tgt, subtitle) -> str over a backend with chat(messages) -> str.

    The local Qwen3 models get the "/no_think" tag; online backends say whether they want it
    (backend.no_think). Online failures raise online_llm.TranslateError."""

    def __init__(self, backend, meeting: str = "") -> None:
        self.glossary = load_glossary()
        self.meeting = meeting  # free-text background of the meeting, added to the system prompt
        self.backend = backend
        self.label = getattr(backend, "label", "")

    def reload_glossary(self) -> None:
        self.glossary = load_glossary()

    def translate(self, text: str, src: str, tgt: str, subtitle: bool = False,
                  context: tuple[str, str] | None = None,
                  history: list[tuple[str, str]] | tuple = (), concise: int = 0,
                  on_clause: Callable[[str], None] | None = None,
                  on_text: Callable[[str], None] | None = None) -> str:
        """context = (previous part's source, its translation) when `text` continues that part
        (a long sentence cut early): shown to the model as the preceding exchange.
        history = the same speaker's last few (source, translation) pairs, oldest first: earlier
        turns of the conversation, so pronouns, terms and tone carry over between sentences.
        concise = a length budget (words, or characters for zh/ja) when speech is backing up. on_clause(text) is called once with
        the first clause while the model is still writing, when the backend streams — so speech
        can start before the sentence is translated; the return value is always the whole text.
        on_text(text) gets the translation so far while it is being written (live subtitles)."""
        tag = NO_THINK if getattr(self.backend, "no_think", True) else ""
        msgs = [{"role": "system", "content": system_prompt(src, tgt, subtitle, self.meeting, concise)}]
        for h_src, h_tgt in history:
            msgs += [{"role": "user", "content": user_prompt(h_src, [])},
                     {"role": "assistant", "content": h_tgt}]
        if context:
            msgs += [{"role": "user", "content": user_prompt(context[0], glossary_hits(context[0], self.glossary))},
                     {"role": "assistant", "content": context[1]}]
        msgs.append({"role": "user", "content": user_prompt(text, glossary_hits(text, self.glossary),
                                                            continuation=context is not None) + tag})
        if hasattr(self.backend, "max_tokens"):  # local models: stop a runaway answer early
            self.backend.max_tokens = token_budget(text)
        stream = getattr(self.backend, "stream_chat", None)
        if (on_clause is not None or on_text is not None) and stream is not None:
            out = last_line(self._streamed(stream(msgs), text, tgt, on_clause, on_text))
        else:
            out = last_line(self.backend.chat(msgs))
        if off_task(out, text, tgt):
            # small models sometimes explain the sentence instead of translating it: one plain retry,
            # and never speak the explanation to the other side
            out = last_line(self.backend.chat([msgs[0], {"role": "user", "content": user_prompt(text, []) + tag}]))
            if off_task(out, text, tgt):
                return ""
        return out

    @staticmethod
    def _streamed(pieces: Iterator[str], src_text: str, tgt: str, on_clause: Callable[[str], None] | None,
                  on_text: Callable[[str], None] | None = None) -> str:
        text, sent = "", on_clause is None
        for piece in pieces:
            text += piece
            body = strip_tags(text.split("</think>")[-1]).strip()
            if off_task(body, src_text, tgt):
                break  # stop generating: translate() retries plainly
            if on_text is not None and body and "\n" not in body and "<think>" not in body:
                on_text(body.strip('"“”'))
            if not sent:
                head = first_clause(text, tgt, src_text=src_text)
                if head:
                    on_clause(head)
                    sent = True
        return text

    def close(self) -> None:
        self.backend.close()


_CLAUSE = re.compile(r"[,.;:?!，。；：？！]")


def first_clause(text: str, tgt: str, min_words: int = 4, src_text: str | None = None) -> str:
    """The finished first clause of a translation still being written ('' = not yet / not safe):
    ends at punctuation that already has more text after it, has at least min_words words (6
    characters for Chinese / Japanese / Korean), sits on the first line, and is not reasoning."""
    body = strip_tags(text.split("</think>")[-1]).lstrip()
    if "\n" in body.strip():
        return ""  # several lines: last_line() will pick one, the first clause may not be in it
    for m in _CLAUSE.finditer(body):
        end = m.end()
        if end >= len(body.rstrip()):
            return ""  # punctuation at the very end: wait to see whether the sentence goes on
        head = body[:end].strip()
        if m.group() == "." and end < len(body) and not body[end].isspace():
            continue  # 3.5, e.g.
        size = len(head.split()) if tgt in ("en",) or " " in head else len(re.sub(r"\W", "", head)) // 1.5
        bad = looks_like_reasoning(head, tgt) if src_text is None else off_task(head, src_text, tgt)
        if size >= min_words and not bad:
            return head
    return ""


class _MLX:
    PREFIXES = 4  # system prompts kept computed: me / them, each with and without "be concise"

    def __init__(self, path: Path) -> None:
        from mlx_lm import load
        from mlx_lm.sample_utils import make_sampler

        self.model, self.tok = load(str(path))
        self.sampler = make_sampler(temp=0.0)
        self.max_tokens = 200
        self._prefix: OrderedDict[str, tuple[list[int], list]] = OrderedDict()

    def _prompt(self, msgs: list[dict], generation: bool = True) -> str:
        return self.tok.apply_chat_template(msgs, add_generation_prompt=generation, tokenize=False,
                                            enable_thinking=False)

    def _encode(self, prompt: str) -> list[int]:  # as mlx_lm does for a text prompt
        bos = self.tok.bos_token
        return self.tok.encode(prompt, add_special_tokens=bos is None or not prompt.startswith(bos))

    def _with_prefix(self, msgs: list[dict]) -> tuple[list[int], list | None]:
        """The prompt's tokens after the system message, plus a copy of the model's cache already
        holding that system message — it is the same for every sentence, so it is computed once
        (0.67 -> 0.39 s to the first spoken clause with Qwen3-4B)."""
        ids = self._encode(self._prompt(msgs))
        if not msgs or msgs[0].get("role") != "system":
            return ids, None
        try:
            import copy

            key = msgs[0]["content"]
            hit = self._prefix.get(key)
            if hit is None:
                import mlx.core as mx
                from mlx_lm.models.cache import make_prompt_cache

                pre = self._encode(self._prompt(msgs[:1], generation=False))
                if len(pre) >= len(ids) or ids[:len(pre)] != pre:
                    return ids, None  # this chat template renders the system message differently
                cache = make_prompt_cache(self.model)
                self.model(mx.array(pre)[None], cache=cache)
                mx.eval([c.state for c in cache])
                hit = self._prefix[key] = (pre, cache)
                while len(self._prefix) > self.PREFIXES:
                    self._prefix.popitem(last=False)
            self._prefix.move_to_end(key)
            pre, cache = hit
            if ids[:len(pre)] != pre:
                return ids, None
            return ids[len(pre):], copy.deepcopy(cache)
        except Exception:  # noqa: BLE001 — a model without a reusable cache: just compute it all
            return ids, None

    def chat(self, msgs: list[dict]) -> str:
        return "".join(self.stream_chat(msgs))

    def stream_chat(self, msgs: list[dict]) -> Iterator[str]:
        from mlx_lm import stream_generate

        ids, cache = self._with_prefix(msgs)
        kw = {"prompt_cache": cache} if cache is not None else {}
        for r in stream_generate(self.model, self.tok, ids, max_tokens=self.max_tokens, sampler=self.sampler, **kw):
            yield r.text

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
        self.max_tokens = 200
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

    def _request(self, msgs: list[dict], stream: bool) -> urllib.request.Request:
        body = json.dumps({"messages": msgs, "temperature": 0, "max_tokens": self.max_tokens, "stream": stream,
                           "cache_prompt": True,  # reuse the system prompt already computed
                           "chat_template_kwargs": {"enable_thinking": False}}).encode()
        return urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/chat/completions", data=body,
                                      headers={"Content-Type": "application/json"})

    def chat(self, msgs: list[dict]) -> str:
        with urllib.request.urlopen(self._request(msgs, False), timeout=60) as r:
            return json.load(r)["choices"][0]["message"]["content"]

    def stream_chat(self, msgs: list[dict]) -> Iterator[str]:
        """Server-sent events: data: {"choices":[{"delta":{"content":"…"}}]} … data: [DONE]"""
        with urllib.request.urlopen(self._request(msgs, True), timeout=60) as r:
            for raw in r:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    return
                try:
                    delta = json.loads(data)["choices"][0].get("delta", {}).get("content")
                except (ValueError, KeyError, IndexError):
                    continue
                if delta:
                    yield delta

    def close(self) -> None:
        if getattr(self, "proc", None) and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
