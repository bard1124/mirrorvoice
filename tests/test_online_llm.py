"""Online translation over an OpenAI-compatible API, against a local fake server."""
import http.server
import json
import os
import sys
import threading
import time

import pytest

from mirrorvoice import keystore, models, providers
from mirrorvoice.config import Settings
from mirrorvoice.online_llm import FailoverChat, OnlineChat, TranslateError, list_models
from mirrorvoice.translate import Translator


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self):
        srv = self.server
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n)) if n else None
        srv.seen.append({"path": self.path, "auth": self.headers.get("Authorization", ""), "body": body})
        status, payload, headers, delay = srv.script.pop(0) if srv.script else srv.default
        if delay:
            time.sleep(delay)
        data = json.dumps(payload).encode()
        self.send_response(status)
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = do_POST = _reply


def ok(text):
    return 200, {"choices": [{"message": {"role": "assistant", "content": text}}]}, None, 0


@pytest.fixture
def api():
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    srv.seen, srv.script, srv.default = [], [], ok("Hello.")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv, f"http://127.0.0.1:{srv.server_address[1]}/v1"
    srv.shutdown()


KEY = "sk-test-0123456789abcdef"


def test_translates_with_bearer_key_and_live_friendly_request(api):
    srv, url = api
    srv.script = [ok("Let's discuss the budget first.")]
    tr = Translator(OnlineChat("deepseek", "deepseek-chat", KEY, url))
    assert tr.translate("我们先讨论预算。", "zh", "en") == "Let's discuss the budget first."
    req = srv.seen[0]
    assert req["path"] == "/v1/chat/completions" and req["auth"] == f"Bearer {KEY}"
    b = req["body"]
    assert b["model"] == "deepseek-chat" and b["temperature"] == 0 and b["max_tokens"] == 200 and not b["stream"]
    assert not b["messages"][-1]["content"].endswith("/no_think")  # Qwen-only prompt tag
    assert "enable_thinking" not in b


def test_qwen3_thinking_is_switched_off_per_host(api):
    srv, url = api
    Translator(OnlineChat("siliconflow", "Qwen/Qwen3-8B", KEY, url)).translate("你好", "zh", "en")
    b = srv.seen[-1]["body"]
    assert b["enable_thinking"] is False and not b["messages"][-1]["content"].endswith("/no_think")
    Translator(OnlineChat("custom", "qwen3:8b", "", url)).translate("你好", "zh", "en")  # e.g. Ollama
    b = srv.seen[-1]["body"]
    assert "enable_thinking" not in b and b["messages"][-1]["content"].endswith("/no_think")


def test_doubao_seed_models_get_thinking_disabled(api):
    srv, url = api
    Translator(OnlineChat("doubao", "doubao-seed-2-0-mini-260428", KEY, url)).translate("你好", "zh", "en")
    b = srv.seen[-1]["body"]
    assert b["thinking"] == {"type": "disabled"} and not b["messages"][-1]["content"].endswith("/no_think")
    Translator(OnlineChat("doubao", "ep-20260101-abcde", KEY, url)).translate("你好", "zh", "en")
    assert "thinking" not in srv.seen[-1]["body"]  # endpoint ids: unknown model, send nothing extra
    assert providers.get("doubao").base_url == "https://ark.cn-beijing.volces.com/api/v3"


def test_bad_key_is_a_persistent_error_without_the_key_in_it(api):
    srv, url = api
    srv.script = [(401, {"error": {"message": "Incorrect API key provided"}}, None, 0)]
    with pytest.raises(TranslateError) as e:
        OnlineChat("openai", "gpt-4.1-mini", KEY, url).chat([{"role": "user", "content": "hi"}])
    assert e.value.kind == "auth" and "401" in str(e.value) and KEY not in str(e.value)
    assert len(srv.seen) == 1  # no retry


def test_one_quick_retry_on_429(api):
    srv, url = api
    srv.script = [(429, {"error": {"message": "slow down"}}, {"Retry-After": "0"}, 0), ok("Hi.")]
    assert OnlineChat("openai", "m", KEY, url).chat([{"role": "user", "content": "hi"}]) == "Hi."
    assert len(srv.seen) == 2
    srv.script = [(429, {}, {"Retry-After": "30"}, 0)]  # asks for a long wait: give up at once
    with pytest.raises(TranslateError, match="429"):
        OnlineChat("openai", "m", KEY, url).chat([{"role": "user", "content": "hi"}])


def test_slow_provider_times_out_quickly(api):
    srv, url = api
    srv.script = [(200, {}, None, 1.5)]
    t0 = time.monotonic()
    with pytest.raises(TranslateError) as e:
        OnlineChat("openai", "m", KEY, url, timeout=0.4).chat([{"role": "user", "content": "hi"}])
    assert e.value.kind == "timeout" and time.monotonic() - t0 < 1.4


def test_missing_key_or_model_is_reported_before_any_request():
    with pytest.raises(TranslateError, match="API key") as e:
        OnlineChat("deepseek", "deepseek-chat", "", providers.get("deepseek").base_url)
    assert e.value.kind == "config"
    OnlineChat("custom", "llama3", "", "http://localhost:11434/v1")  # local server: no key needed
    with pytest.raises(TranslateError, match="模型"):
        OnlineChat("openai", "", KEY, "https://api.openai.com/v1")


def test_model_list_keeps_chat_models_only(api):
    srv, url = api
    srv.script = [(200, {"data": [{"id": "gpt-4.1-mini"}, {"id": "text-embedding-3-small"}, {"id": "whisper-1"},
                                  {"id": "deepseek-chat"}, {"id": "tts-1"}]}, None, 0)]
    assert list_models(url, KEY) == ["deepseek-chat", "gpt-4.1-mini"]
    assert srv.seen[0]["path"] == "/v1/models"


class _Local:
    def __init__(self):
        self.calls = 0

    def chat(self, msgs):
        self.calls += 1
        return "local translation"

    def close(self):
        pass


def test_failover_uses_the_local_model_for_a_while_after_repeated_failures(api, monkeypatch):
    srv, url = api
    srv.default = (503, {}, {"Retry-After": "5"}, 0)
    local, notes = _Local(), []
    fo = FailoverChat(OnlineChat("openai", "m", KEY, url), lambda: local, notes.append)
    msgs = [{"role": "user", "content": "hi"}]
    for _ in range(3):
        with pytest.raises(TranslateError):
            fo.chat(msgs)
    assert fo.using_local and fo.no_think and "本地模型" in notes[-1]
    assert fo.chat(msgs) == "local translation" and local.calls == 1
    fo.local_until = time.monotonic() - 1  # the minute is over
    srv.default = ok("Back online.")
    assert fo.chat(msgs) == "Back online." and "在线" in notes[-1] and not fo.using_local


def test_bad_key_switches_to_local_at_once_and_no_local_means_errors(api):
    srv, url = api
    srv.default = (401, {}, None, 0)
    local = _Local()
    fo = FailoverChat(OnlineChat("openai", "m", KEY, url), lambda: local)
    with pytest.raises(TranslateError):
        fo.chat([{"role": "user", "content": "hi"}])
    assert fo.using_local
    bare = FailoverChat(OnlineChat("openai", "m", KEY, url))
    for _ in range(4):
        with pytest.raises(TranslateError):
            bare.chat([{"role": "user", "content": "hi"}])
    assert not bare.using_local


def test_online_translation_skips_the_translation_model_download(home):
    s = Settings(llm_mode="online", subtitles=False)
    assert all(m.key != models.LLM[s.quality].key for m in models.required(s.quality, s))
    s.subtitles = True  # subtitles stay on the local model unless the other side may go online too
    assert any(m.key == models.LLM[s.quality].key for m in models.required(s.quality, s))
    s.online_their = True
    assert all(m.key != models.LLM[s.quality].key for m in models.required(s.quality, s))
    assert any(m.key == models.LLM["fast"].key for m in models.required("fast", Settings()))


def test_base_url_normalisation_and_local_detection():
    n = providers.normalize_base_url
    assert n("localhost:11434") == "http://localhost:11434/v1"
    assert n("https://api.example.com/v1/chat/completions/") == "https://api.example.com/v1"
    assert n("https://open.bigmodel.cn/api/paas/v4") == "https://open.bigmodel.cn/api/paas/v4"
    assert providers.is_local("http://192.168.1.20:1234/v1") and not providers.is_local("https://api.deepseek.com/v1")
    assert providers.is_reasoning_model("deepseek-reasoner") and not providers.is_reasoning_model("deepseek-chat")
    assert providers.label("custom", "/Users/me/models/Qwen3-4B-MLX-4bit") == "自定义 · Qwen3-4B-MLX-4bit"
    assert providers.label("custom", "C:\\models\\qwen3-4b\\") == "自定义 · qwen3-4b"
    assert providers.label("siliconflow", "Qwen/Qwen3-8B") == "硅基流动 · Qwen/Qwen3-8B"


# ---------------------------------------------------------------- keys


def test_keys_go_to_an_owner_only_file_when_there_is_no_keychain(home):
    keystore.forget_cache()
    assert keystore.save("deepseek", f"  {KEY} ") .startswith("本地文件")
    keystore.forget_cache()
    assert keystore.get("deepseek") == KEY
    if sys.platform != "win32":
        assert oct(keystore.KEYS_FILE.stat().st_mode & 0o777) == "0o600"
    assert KEY not in (home / "settings.json").read_text(encoding="utf-8") if (home / "settings.json").exists() else True
    keystore.save("deepseek", "")
    keystore.forget_cache()
    assert keystore.get("deepseek") == "" and "deepseek" not in json.loads(keystore.KEYS_FILE.read_text())
    assert keystore.mask(KEY) == "sk-…cdef"


def test_environment_variable_wins(monkeypatch):
    monkeypatch.setenv("MIRRORVOICE_OPENAI_API_KEY", "sk-from-env")
    assert keystore.get("openai") == "sk-from-env"


def test_keychain_backend_is_used_when_available(monkeypatch, home):
    keyring = pytest.importorskip("keyring")
    from keyring.backend import KeyringBackend

    class Memory(KeyringBackend):
        priority = 1
        store: dict = {}

        def get_password(self, service, user):
            return self.store.get((service, user))

        def set_password(self, service, user, pw):
            self.store[(service, user)] = pw

        def delete_password(self, service, user):
            self.store.pop((service, user), None)

    monkeypatch.delenv("MIRRORVOICE_KEYSTORE")
    old = keyring.get_keyring()
    keyring.set_keyring(Memory())
    try:
        keystore.forget_cache()
        keystore.save("dashscope", KEY)
        assert Memory.store[("MirrorVoice", "dashscope")] == KEY
        assert "dashscope" not in (json.loads(keystore.KEYS_FILE.read_text()) if keystore.KEYS_FILE.exists() else {})
        keystore.forget_cache()
        assert keystore.get("dashscope") == KEY
    finally:
        keyring.set_keyring(old)
        keystore.forget_cache()


# ---------------------------------------------------------------- engine


def _engine(events):
    from mirrorvoice.engine import Engine

    e = Engine(lambda kind, **d: events.append((kind, d)))
    e._closers = []
    return e


def test_engine_builds_online_and_local_translators(monkeypatch, api, home):
    from mirrorvoice import engine as eng

    _, url = api
    built = []
    monkeypatch.setattr(eng, "local_backend", lambda p: built.append(p) or _Local())
    keystore.save("custom", "")
    s = Settings(llm_mode="online", llm_provider="custom", llm_base_url=url, llm_model="m", subtitles=True,
                 their_source="BlackHole 16ch")
    mine, theirs = _engine([])._translators(s)
    assert isinstance(mine.backend, FailoverChat) and theirs is not mine and built  # subtitles stay local
    built.clear()
    s.online_their = True
    mine, theirs = _engine([])._translators(s)
    assert theirs is mine and not built  # nothing local loaded up front


def test_engine_reports_an_incomplete_online_setup(home):
    keystore.forget_cache()
    s = Settings(llm_mode="online", llm_provider="openai", subtitles=False)
    with pytest.raises(RuntimeError, match="在线翻译设置不完整"):
        _engine([])._translators(s)


def test_failed_sentence_is_shown_not_spoken(api, home):
    from mirrorvoice.asr import Utterance
    import numpy as np

    srv, url = api
    srv.default = (500, {}, {"Retry-After": "9"}, 0)
    events = []
    e = _engine(events)
    e.s = Settings(their_lang="en")
    e._voice = None
    e._transcript = None
    tr = Translator(FailoverChat(OnlineChat("custom", "m", "", url)))
    e._do_me(tr, Utterance("你好", "zh", np.zeros(1600, np.float32), time.time(), 0.1))
    kind, d = events[-1]
    assert kind == "me" and d["failed"] and d["tgt"].startswith("⚠")
    e._do_them(tr, Utterance("hello", "en", np.zeros(1600, np.float32), time.time() - 30, 0.1))
    kind, d = events[-1]
    assert kind == "them" and d["failed"] and "积压" in d["tgt"]


def test_local_subtitles_far_behind_are_skipped_too(home):
    """Local translation used to work through every queued subtitle: my sentences waited 8–11 s."""
    from mirrorvoice.asr import Utterance
    import numpy as np

    local = _Local()
    events = []
    e = _engine(events)
    e.s = Settings(their_lang="en")
    e._transcript = None
    e._do_them(Translator(local), Utterance("hello", "en", np.zeros(1600, np.float32), time.time() - 30, 0.1))
    kind, d = events[-1]
    assert kind == "them" and d["failed"] and "积压" in d["tgt"] and local.calls == 0
