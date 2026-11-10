"""Online translation providers: presets over one OpenAI-compatible chat-completions client.

Every provider here speaks `POST {base_url}/chat/completions` and `GET {base_url}/models`; a preset
only fills in the address and a sensible default model. "custom" takes any OpenAI-compatible
address (a local Ollama / LM Studio too — those need no key).
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str                 # "" for custom (the user types it)
    default_model: str
    models: tuple[str, ...] = ()  # suggestions shown before "获取模型列表"
    key_url: str = ""             # where to create an API key
    # (model-name pattern, request fields) that switch "thinking" off on this host; thinking models
    # spend seconds per sentence. Only sent to matching models: others may reject unknown fields.
    thinking_off: tuple[str, dict] | None = None


_QWEN_HYBRID = (r"qwen3|qwen-(plus|turbo|flash)", {"enable_thinking": False})

PRESETS: dict[str, Provider] = {p.id: p for p in (
    Provider("deepseek", "DeepSeek", "https://api.deepseek.com/v1", "deepseek-chat",
             ("deepseek-chat",), "https://platform.deepseek.com/api_keys"),
    Provider("doubao", "豆包（火山方舟）", "https://ark.cn-beijing.volces.com/api/v3", "doubao-seed-2-0-mini-260428",
             ("doubao-seed-2-0-mini-260428", "doubao-seed-2-0-lite-260428", "doubao-seed-2-1-pro-260915"),
             "https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey",
             thinking_off=(r"seed", {"thinking": {"type": "disabled"}})),
    Provider("dashscope", "通义千问（阿里云百炼）", "https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus",
             ("qwen-plus", "qwen-turbo", "qwen-max"), "https://bailian.console.aliyun.com/?apiKey=1",
             thinking_off=_QWEN_HYBRID),
    Provider("siliconflow", "硅基流动", "https://api.siliconflow.cn/v1", "Qwen/Qwen3-8B",
             ("Qwen/Qwen3-8B", "Qwen/Qwen3-32B", "deepseek-ai/DeepSeek-V3"), "https://cloud.siliconflow.cn/account/ak",
             thinking_off=_QWEN_HYBRID),
    Provider("openai", "OpenAI", "https://api.openai.com/v1", "gpt-4.1-mini",
             ("gpt-4.1-mini", "gpt-4.1", "gpt-4o-mini"), "https://platform.openai.com/api-keys"),
    Provider("openrouter", "OpenRouter", "https://openrouter.ai/api/v1", "openai/gpt-4.1-mini",
             ("openai/gpt-4.1-mini", "deepseek/deepseek-chat", "google/gemini-2.5-flash"), "https://openrouter.ai/keys"),
    Provider("custom", "自定义（OpenAI 兼容地址）", "", "", (), ""),
)}

DEFAULT_PROVIDER = "deepseek"

# Models that "think" before answering: several seconds per sentence, unusable live.
_REASONING = re.compile(r"(reasoner|thinking|\bo[134](-|$)|qwq|-r1\b|deepseek-r1|/r1)", re.I)


def get(pid: str) -> Provider:
    return PRESETS.get(pid) or PRESETS[DEFAULT_PROVIDER]


def extra_body(pid: str, model: str) -> dict:
    """Request fields that turn thinking off for this host's thinking-capable models."""
    off = get(pid).thinking_off
    if off and re.search(off[0], model or "", re.I):
        return dict(off[1])
    return {}


def is_reasoning_model(model: str) -> bool:
    return bool(_REASONING.search(model or ""))


def normalize_base_url(url: str) -> str:
    """Accept what people paste: with or without /v1, with /chat/completions or /models on the end."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = ("http://" if is_local(u) else "https://") + u
    u = re.sub(r"/(chat/completions|completions|models)$", "", u, flags=re.I)
    path = urlsplit(u).path
    if not path or path == "/":
        u += "/v1"  # bare host: OpenAI-compatible servers (Ollama, LM Studio, vLLM) live under /v1
    return u


def is_local(url: str) -> bool:
    """localhost / private network address: no API key needed (Ollama, LM Studio, a LAN box)."""
    host = urlsplit(url if "://" in url else "http://" + url).hostname or ""
    if host in ("localhost",) or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private


def base_url(pid: str, custom_url: str = "") -> str:
    p = get(pid)
    return normalize_base_url(custom_url) if p.id == "custom" else p.base_url


def needs_key(pid: str, custom_url: str = "") -> bool:
    return not (get(pid).id == "custom" and is_local(base_url(pid, custom_url)))


def label(pid: str, model: str) -> str:
    """'DeepSeek · deepseek-chat' for status lines."""
    p = get(pid)
    name = "自定义" if p.id == "custom" else p.name.split("（")[0]
    if re.match(r"^(/|[A-Za-z]:[\\/])", model or ""):  # a local server named after the model's folder path
        model = re.split(r"[\\/]", model.rstrip("/\\"))[-1]
    return f"{name} · {model}" if model else name
