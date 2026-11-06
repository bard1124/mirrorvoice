"""HTTPS helper. The frozen app has no system CA path for Python's ssl, so every download
(models, updates, VB-CABLE) failed with CERTIFICATE_VERIFY_FAILED; use certifi's bundle."""
from __future__ import annotations

import ssl
import urllib.request
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 — fall back to the platform store
        return ssl.create_default_context()


def urlopen(req, timeout: float = 30):
    if isinstance(req, str):
        req = urllib.request.Request(req)
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())
