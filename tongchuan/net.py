"""HTTPS helper used by every download (models, updates, VB-CABLE).

The frozen app has no system CA path for Python's ssl, so certifi's bundle is used first. Networks that
inspect HTTPS (company proxies, some antivirus) re-sign traffic with their own root certificate, which is
installed in the OS trust store but not in certifi: when verification fails we retry with the system store
(macOS Keychain / Windows certificate store, via truststore) and keep using whichever store worked.
"""
from __future__ import annotations

import os
import ssl
import threading
import urllib.error
import urllib.request
from typing import Callable


def _certifi() -> ssl.SSLContext:
    import certifi

    return ssl.create_default_context(cafile=certifi.where())


def _system() -> ssl.SSLContext:
    import truststore

    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


def _default() -> ssl.SSLContext:
    return ssl.create_default_context()


STORES: list[tuple[str, Callable[[], ssl.SSLContext]]] = [
    ("certifi", _certifi), ("system", _system), ("default", _default)]

_lock = threading.Lock()
_contexts: dict[str, ssl.SSLContext] = {}
_preferred: list[str] = []  # name of the store that last succeeded


def _context(name: str) -> ssl.SSLContext | None:
    with _lock:
        if name not in _contexts:
            try:
                _contexts[name] = dict(STORES)[name]()
            except Exception:  # noqa: BLE001 — store unavailable on this platform/build
                return None
        return _contexts[name]


def _is_cert_error(e: BaseException) -> bool:
    reason = getattr(e, "reason", e)
    return isinstance(reason, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(reason)


def urlopen(req, timeout: float = 30):
    if isinstance(req, str):
        req = urllib.request.Request(req)
    names = [n for n, _ in STORES]
    forced = os.environ.get("TONGCHUAN_TLS", "")  # diagnostics: use only this store (certifi/system/default)
    if forced in names:
        names = [forced]
    elif _preferred:
        names.remove(_preferred[0])
        names.insert(0, _preferred[0])
    first_error: BaseException | None = None
    for name in names:
        ctx = _context(name)
        if ctx is None:
            continue
        try:
            r = urllib.request.urlopen(req, timeout=timeout, context=ctx)
        except (urllib.error.URLError, ssl.SSLError) as e:
            if not _is_cert_error(e):
                raise
            first_error = first_error or e
            continue
        _preferred[:] = [name]
        return r
    raise urllib.error.URLError(
        "HTTPS 证书校验失败（已试过内置证书库和系统证书库）。如果在公司网络里，请确认公司根证书已经安装到系统；"
        f"原始错误：{getattr(first_error, 'reason', first_error)}")
