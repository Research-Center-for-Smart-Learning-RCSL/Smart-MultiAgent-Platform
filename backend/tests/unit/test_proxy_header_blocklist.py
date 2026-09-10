"""Regression tests: proxy_headers blocklist, CRLF, and size limits (AC-2, AC-5)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from contexts.keys.application.openai_compat_config import OpenAICompatConfig

BASE = {"base_url": "https://proxy.example.com"}

BLOCKLISTED_NAMES = [
    "Authorization",
    "authorization",
    "AUTHORIZATION",
    "Content-Type",
    "content-type",
    "Content-Length",
    "Host",
    "Transfer-Encoding",
    "Connection",
    "Upgrade",
    "Proxy-Authorization",
    "proxy-authorization",
]


@pytest.mark.parametrize("header_name", BLOCKLISTED_NAMES)
def test_blocklisted_header_rejected(header_name: str) -> None:
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers={header_name: "value"})


def test_crlf_in_header_name_rejected() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers={"X-Foo\r\nEvil: bar": "ok"})


def test_crlf_in_header_value_rejected() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers={"X-Foo": "ok\r\nEvil: injected"})


def test_lf_in_header_name_rejected() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers={"X-Foo\nEvil": "ok"})


def test_lf_in_header_value_rejected() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers={"X-Foo": "ok\nEvil"})


def test_max_entries_exceeded() -> None:
    headers = {f"X-H-{i}": f"v{i}" for i in range(21)}
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers=headers)


def test_max_entries_at_limit_accepted() -> None:
    headers = {f"X-H-{i}": f"v{i}" for i in range(20)}
    cfg = OpenAICompatConfig(**BASE, proxy_headers=headers)
    assert len(cfg.proxy_headers) == 20  # type: ignore[arg-type]


def test_header_name_too_long() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers={"X" * 129: "ok"})


def test_header_name_at_limit_accepted() -> None:
    cfg = OpenAICompatConfig(**BASE, proxy_headers={"X" * 128: "ok"})
    assert cfg.proxy_headers is not None


def test_header_value_too_long() -> None:
    with pytest.raises(ValidationError):
        OpenAICompatConfig(**BASE, proxy_headers={"X-Foo": "v" * 4097})


def test_header_value_at_limit_accepted() -> None:
    cfg = OpenAICompatConfig(**BASE, proxy_headers={"X-Foo": "v" * 4096})
    assert cfg.proxy_headers is not None


def test_safe_headers_accepted() -> None:
    cfg = OpenAICompatConfig(
        **BASE,
        proxy_headers={"X-Custom-Auth": "token123", "X-Region": "us-east-1"},
    )
    assert cfg.proxy_headers == {"X-Custom-Auth": "token123", "X-Region": "us-east-1"}


def test_none_proxy_headers_accepted() -> None:
    cfg = OpenAICompatConfig(**BASE, proxy_headers=None)
    assert cfg.proxy_headers is None


def test_empty_proxy_headers_accepted() -> None:
    cfg = OpenAICompatConfig(**BASE, proxy_headers={})
    assert cfg.proxy_headers == {}
