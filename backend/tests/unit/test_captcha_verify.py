"""Unit tests for `captcha.verify` — the fail-CLOSED verification path (R19a.12).

`public_config` is covered in test_email_k6.py; this file pins the verify-side
guarantees added in the K.6 polish pass: strict provider allowlist (no posting
the secret to the wrong host) and Vault errors surfacing as CaptchaError.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from shared_kernel.auth import captcha


def _prod(monkeypatch) -> None:
    monkeypatch.setattr(captcha, "get_settings", lambda: SimpleNamespace(app=SimpleNamespace(env="prod")))


def _vault(monkeypatch, **cfg) -> None:
    fake = Mock()
    fake.kv_get.return_value = cfg
    monkeypatch.setattr(captcha, "get_vault_client", lambda: fake)


class _FakeResp:
    def __init__(self, body: dict) -> None:
        self.status_code = 200
        self._body = body

    def json(self) -> dict:
        return self._body


class _FakeClient:
    last_url: str | None = None

    def __init__(self, *a, **k) -> None:
        pass

    async def __aenter__(self) -> _FakeClient:
        return self

    async def __aexit__(self, *a) -> bool:
        return False

    async def post(self, url, data=None):
        _FakeClient.last_url = url
        return _FakeResp({"success": True})


async def test_verify_bypasses_in_test_env(monkeypatch) -> None:
    monkeypatch.setattr(captcha, "get_settings", lambda: SimpleNamespace(app=SimpleNamespace(env="test")))
    # No Vault wired — must return without touching it.
    await captcha.verify("anything", remote_ip=None)


async def test_verify_returns_when_mode_off(monkeypatch) -> None:
    _prod(monkeypatch)
    _vault(monkeypatch, provider="hcaptcha", secret="s", mode="off")
    await captcha.verify(None, remote_ip=None)


async def test_verify_unknown_provider_fails_closed(monkeypatch) -> None:
    _prod(monkeypatch)
    _vault(monkeypatch, provider="bogus", secret="s", mode="on")
    # Must raise BEFORE any HTTP call — never POST the secret to a fallback host.
    monkeypatch.setattr(captcha.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_url = None
    with pytest.raises(captcha.CaptchaError):
        await captcha.verify("tok", remote_ip=None)
    assert _FakeClient.last_url is None


async def test_verify_vault_error_fails_closed(monkeypatch) -> None:
    _prod(monkeypatch)

    def _boom():
        raise RuntimeError("vault down")

    monkeypatch.setattr(captcha, "get_vault_client", _boom)
    with pytest.raises(captcha.CaptchaError):
        await captcha.verify("tok", remote_ip=None)


async def test_verify_missing_token_fails_closed(monkeypatch) -> None:
    _prod(monkeypatch)
    _vault(monkeypatch, provider="hcaptcha", secret="s", mode="on")
    with pytest.raises(captcha.CaptchaError):
        await captcha.verify(None, remote_ip=None)


@pytest.mark.parametrize(
    ("provider", "expected_url"),
    [("hcaptcha", captcha._HCAPTCHA_URL), ("turnstile", captcha._TURNSTILE_URL)],
)
async def test_verify_posts_to_correct_host(monkeypatch, provider, expected_url) -> None:
    _prod(monkeypatch)
    _vault(monkeypatch, provider=provider, secret="s", mode="on")
    monkeypatch.setattr(captcha.httpx, "AsyncClient", _FakeClient)
    _FakeClient.last_url = None
    await captcha.verify("tok", remote_ip="1.2.3.4")
    assert _FakeClient.last_url == expected_url
