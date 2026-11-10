"""Online translation through any OpenAI-compatible chat-completions API (DeepSeek, 通义, 硅基流动,
OpenAI, OpenRouter, a local Ollama / LM Studio, …).

Tuned for live use, not batch jobs: one sentence per request, an 8 s timeout, at most one quick
retry (a dropped connection, or a 429 / 5xx that asks to wait ≤ 1 s). FailoverChat switches to the
local model for a minute after repeated failures — when the local model is downloaded.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from typing import Callable

from . import __version__, keystore, providers
from .net import urlopen

TIMEOUT_S = 8.0
UA = f"MirrorVoice/{__version__}"


class TranslateError(RuntimeError):
    """kind: auth | quota | model | rate | timeout | network | server | response | config."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind

    PERSISTENT = ("auth", "quota", "model", "config")  # retrying the same request will not help


def _detail(body: bytes) -> str:
    try:
        d = json.loads(body.decode("utf-8", "ignore"))
        err = d.get("error", d)
        msg = err.get("message") if isinstance(err, dict) else str(err)
    except Exception:  # noqa: BLE001 — not JSON
        msg = body.decode("utf-8", "ignore")
    return " ".join((msg or "").split())[:160]


def _http_error(e: urllib.error.HTTPError) -> TranslateError:
    detail = _detail(e.read() if e.fp else b"")
    code = e.code
    if code in (401, 403):
        return TranslateError("auth", f"API key 无效或没有权限（{code}）{('：' + detail) if detail else ''}")
    if code == 402:
        return TranslateError("quota", f"账户余额不足（402）{('：' + detail) if detail else ''}")
    if code == 404:
        return TranslateError("model", f"模型不存在或地址不对（404）{('：' + detail) if detail else ''}")
    if code == 429:
        return TranslateError("rate", f"请求太频繁或额度用完（429）{('：' + detail) if detail else ''}")
    if code >= 500:
        return TranslateError("server", f"服务商出错（{code}）{('：' + detail) if detail else ''}")
    return TranslateError("response", f"请求被拒绝（{code}）{('：' + detail) if detail else ''}")


def _retry_after(e: urllib.error.HTTPError) -> float:
    try:
        return float(e.headers.get("Retry-After", "0"))
    except (TypeError, ValueError):
        return 0.0


def _request(url: str, key: str, body: dict | None, timeout: float) -> dict:
    headers = {"User-Agent": UA, "Accept": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def call(url: str, key: str, body: dict | None, timeout: float = TIMEOUT_S) -> dict:
    """One request with at most one quick retry; raises TranslateError (never with the key in it)."""
    for attempt in (1, 2):
        t0 = time.monotonic()
        try:
            return _request(url, key, body, timeout)
        except urllib.error.HTTPError as e:
            err = _http_error(e)
            wait = _retry_after(e)
            if attempt == 1 and e.code in (429, 500, 502, 503) and wait <= 1.0:
                time.sleep(wait or 0.3)
                continue
            raise err from None
        except (TimeoutError, socket.timeout):
            raise TranslateError("timeout", f"等待超过 {timeout:.0f} 秒没有回应") from None
        except urllib.error.URLError as e:
            reason = getattr(e, "reason", e)
            if isinstance(reason, (TimeoutError, socket.timeout)):
                raise TranslateError("timeout", f"等待超过 {timeout:.0f} 秒没有回应") from None
            # a refused / reset connection right away: one more try; DNS failures and the like: no
            if attempt == 1 and time.monotonic() - t0 < 1.0 and isinstance(reason, (ConnectionError, OSError)) \
                    and not isinstance(reason, socket.gaierror):
                continue
            raise TranslateError("network", f"连不上服务商：{reason}") from None
        except (ValueError, KeyError) as e:
            raise TranslateError("response", f"返回内容看不懂：{e}") from None
    raise TranslateError("network", "连不上服务商")  # pragma: no cover — loop always returns/raises


class OnlineChat:
    """chat(messages) -> str against one provider/model. Thread-safe (no shared state)."""

    def __init__(self, pid: str, model: str, key: str, base_url: str, timeout: float = TIMEOUT_S) -> None:
        if not base_url:
            raise TranslateError("config", "还没填服务地址")
        if not model:
            raise TranslateError("config", "还没选模型")
        if not key and providers.needs_key(pid, base_url):
            raise TranslateError("config", f"还没填 {providers.get(pid).name} 的 API key")
        self.pid, self.model, self.key, self.base_url, self.timeout = pid, model, key, base_url, timeout
        self.extra = providers.extra_body(pid, model)
        # Qwen3 hosted without a thinking switch (OpenRouter, Ollama…) still honours the prompt tag
        self.no_think = "qwen3" in model.lower() and not self.extra
        self.label = providers.label(pid, model)

    @classmethod
    def from_settings(cls, s) -> "OnlineChat":
        url = providers.base_url(s.llm_provider, s.llm_base_url)
        model = s.llm_model or providers.get(s.llm_provider).default_model
        return cls(s.llm_provider, model, keystore.get(s.llm_provider), url)

    def chat(self, msgs: list[dict]) -> str:
        body = {"model": self.model, "messages": msgs, "temperature": 0, "max_tokens": 200, "stream": False,
                **self.extra}
        d = call(self.base_url + "/chat/completions", self.key, body, self.timeout)
        try:
            content = d["choices"][0]["message"].get("content")
        except (KeyError, IndexError, TypeError, AttributeError):
            raise TranslateError("response", "返回内容里没有译文") from None
        if not isinstance(content, str) or not content.strip():
            raise TranslateError("response", "服务商返回了空译文")
        return content.strip()

    def close(self) -> None:
        pass


_NOT_CHAT = ("embed", "whisper", "tts", "dall-e", "image", "audio", "transcribe", "moderation", "rerank",
             "realtime", "vision-preview", "bge-", "speech")


def list_models(base_url: str, key: str, timeout: float = 10.0) -> list[str]:
    """GET {base}/models -> chat model ids (embedding / speech / image models filtered out)."""
    d = call(base_url + "/models", key, None, timeout)
    items = d.get("data", d.get("models", [])) if isinstance(d, dict) else d
    ids = []
    for it in items or []:
        mid = it.get("id") or it.get("name") if isinstance(it, dict) else str(it)
        if mid and not any(w in mid.lower() for w in _NOT_CHAT):
            ids.append(mid)
    return sorted(set(ids), key=str.lower)


class FailoverChat:
    """Online first; after FAILS_TO_SWITCH failures in a row (or one persistent error such as a bad
    key) use the local model for LOCAL_FOR_S seconds, then try online again. Without a downloaded
    local model the error is raised and the sentence shows as failed."""

    FAILS_TO_SWITCH = 3
    LOCAL_FOR_S = 60.0

    def __init__(self, online: OnlineChat, local: Callable[[], object] | None = None,
                 notify: Callable[[str], None] | None = None) -> None:
        self.online, self._local_factory, self.notify = online, local, notify or (lambda _m: None)
        self._local = None
        self.fails = 0
        self.local_until = 0.0
        self.label = online.label

    @property
    def using_local(self) -> bool:
        return self._local_factory is not None and time.monotonic() < self.local_until

    @property
    def no_think(self) -> bool:  # read by Translator before it builds the prompt
        return True if self.using_local else self.online.no_think

    def _local_backend(self):
        if self._local is None:
            self._local = self._local_factory()
        return self._local

    def chat(self, msgs: list[dict]) -> str:
        if self.using_local:
            return self._local_backend().chat(msgs)
        if self.local_until:  # the local minute is over: back to online
            self.local_until = 0.0
            self.notify("重新改用在线翻译")
        try:
            out = self.online.chat(msgs)
        except TranslateError as e:
            self.fails = self.FAILS_TO_SWITCH if e.kind in TranslateError.PERSISTENT else self.fails + 1
            if self.fails >= self.FAILS_TO_SWITCH and self._local_factory is not None:
                self.fails = 0
                self.local_until = time.monotonic() + self.LOCAL_FOR_S
                self.notify(f"在线翻译不可用（{e}），接下来 {int(self.LOCAL_FOR_S)} 秒改用本地模型")
            raise
        self.fails = 0
        return out

    def close(self) -> None:
        if self._local is not None:
            self._local.close()
