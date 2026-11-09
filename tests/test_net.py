import ssl
import urllib.error

import pytest

from mirrorvoice import net


class _Resp:
    status = 200


@pytest.fixture
def stores(monkeypatch):
    net._contexts.clear()
    net._preferred.clear()
    calls = []
    ctx = {name: object() for name in ("certifi", "system", "default")}
    monkeypatch.setattr(net, "STORES", [(n, (lambda n=n: ctx[n])) for n in ctx])
    monkeypatch.delenv("MIRRORVOICE_TLS", raising=False)
    monkeypatch.delenv("TONGCHUAN_TLS", raising=False)

    def fake_urlopen(trusted, other_error=None):
        def opener(req, timeout, context):
            name = next(n for n, c in ctx.items() if c is context)
            calls.append(name)
            if other_error:
                raise other_error
            if name not in trusted:
                raise urllib.error.URLError(ssl.SSLCertVerificationError("CERTIFICATE_VERIFY_FAILED"))
            return _Resp()
        monkeypatch.setattr(net.urllib.request, "urlopen", opener)
    return calls, fake_urlopen


def test_falls_back_to_system_store_and_remembers_it(stores):
    calls, fake = stores
    fake(trusted={"system"})
    assert net.urlopen("https://example.invalid").status == 200
    assert calls == ["certifi", "system"]
    calls.clear()
    net.urlopen("https://example.invalid")
    assert calls == ["system"]  # the working store is tried first next time


def test_all_stores_fail_with_a_readable_message(stores):
    _, fake = stores
    fake(trusted=set())
    with pytest.raises(urllib.error.URLError, match="证书校验失败"):
        net.urlopen("https://example.invalid")


def test_non_certificate_errors_are_not_retried(stores):
    calls, fake = stores
    fake(trusted={"certifi"}, other_error=urllib.error.URLError(OSError("connection refused")))
    with pytest.raises(urllib.error.URLError, match="connection refused"):
        net.urlopen("https://example.invalid")
    assert calls == ["certifi"]
