"""API keys for online providers: the OS keychain (macOS Keychain / Windows Credential Manager).

Never in settings.json. Order on read: environment variable MIRRORVOICE_<PROVIDER>_API_KEY, the OS
keychain, then keys.json in the data folder (owner-only permissions) — the fallback when no
usable keychain exists, as Poetry does: the "fail" / "null" / plaintext keyring backends are not
accepted as a keychain. MIRRORVOICE_KEYSTORE=file forces the file (tests, portable installs).

Values are cached in memory after the first read, so a macOS keychain prompt (shown once after
every update of an ad-hoc signed app) comes when the app opens, not in the middle of a meeting.
"""
from __future__ import annotations

import json
import os
import threading

from .config import HOME

SERVICE = "MirrorVoice"
KEYS_FILE = HOME / "keys.json"
_cache: dict[str, str] = {}
_lock = threading.Lock()


def env_name(pid: str) -> str:
    return f"MIRRORVOICE_{pid.upper()}_API_KEY"


def _keyring():
    """The OS keychain backend, or None when only a fail / null / plaintext backend exists."""
    if os.environ.get("MIRRORVOICE_KEYSTORE") == "file":
        return None
    try:
        import keyring
        from keyring.backends import fail

        kr = keyring.get_keyring()
    except Exception:  # noqa: BLE001 — keyring missing or broken: use the file
        return None
    name = f"{type(kr).__module__}.{type(kr).__name__}".lower()
    if isinstance(kr, fail.Keyring) or any(w in name for w in ("null", "plaintext", "fail", "keyrings.alt")):
        return None
    if "chainer" in name and not getattr(kr, "backends", None):
        return None
    return kr


def where() -> str:
    """'系统钥匙串' / '凭据管理器' / '本地文件' — shown next to the key field."""
    if _keyring() is None:
        return "本地文件（仅你的账户可读）"
    import sys

    return "系统钥匙串" if sys.platform == "darwin" else "Windows 凭据管理器"


def _read_file() -> dict[str, str]:
    try:
        return json.loads(KEYS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_file(d: dict[str, str]) -> None:
    KEYS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = KEYS_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(d, f)
    os.replace(tmp, KEYS_FILE)
    try:
        os.chmod(KEYS_FILE, 0o600)
    except OSError:
        pass


def get(pid: str) -> str:
    if os.environ.get(env_name(pid)):
        return os.environ[env_name(pid)].strip()
    with _lock:
        if pid in _cache:
            return _cache[pid]
        val = ""
        kr = _keyring()
        if kr is not None:
            try:
                val = kr.get_password(SERVICE, pid) or ""
            except Exception:  # noqa: BLE001 — locked / denied keychain: fall back to the file
                val = ""
        if not val:
            val = _read_file().get(pid, "")
        _cache[pid] = val
        return val


def save(pid: str, key: str) -> str:
    """Store (or with key="" remove) a key; returns where it went."""
    key = key.strip()
    with _lock:
        kr = _keyring()
        stored_in_keyring = False
        if kr is not None:
            try:
                if key:
                    kr.set_password(SERVICE, pid, key)
                else:
                    try:
                        kr.delete_password(SERVICE, pid)
                    except Exception:  # noqa: BLE001 — nothing stored
                        pass
                stored_in_keyring = True
            except Exception:  # noqa: BLE001 — denied: keep it in the file instead
                stored_in_keyring = False
        d = _read_file()
        if stored_in_keyring or not key:
            changed = d.pop(pid, None) is not None
        else:
            d[pid], changed = key, True
        if changed:
            _write_file(d)
        _cache[pid] = key
    return where() if stored_in_keyring or not key else "本地文件（仅你的账户可读）"


def mask(key: str) -> str:
    """'sk-…a1b2' — enough to recognise which key it is."""
    key = key or ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:3]}…{key[-4:]}"


def forget_cache() -> None:
    with _lock:
        _cache.clear()
