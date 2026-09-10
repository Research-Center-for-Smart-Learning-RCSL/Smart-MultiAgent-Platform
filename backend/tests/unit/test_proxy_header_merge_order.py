"""Regression tests: Authorization always survives proxy_headers (AC-4)."""

from __future__ import annotations

from contexts.keys.infrastructure.adapters.openai_compat import _headers


def test_authorization_survives_proxy_headers() -> None:
    """Even if proxy_headers contains Authorization (belt-and-suspenders),
    the adapter's own Authorization from the Vault-decrypted secret wins."""
    h = _headers("real-secret", proxy_headers={"Authorization": "Bearer wrong"})
    assert h["Authorization"] == "Bearer real-secret"


def test_content_type_survives_proxy_headers() -> None:
    h = _headers("s", proxy_headers={"Content-Type": "text/plain"})
    assert h["Content-Type"] == "application/json"


def test_proxy_headers_merged() -> None:
    h = _headers("s", proxy_headers={"X-Custom": "yes"})
    assert h["X-Custom"] == "yes"
    assert h["Authorization"] == "Bearer s"


def test_no_proxy_headers() -> None:
    h = _headers("s")
    assert h == {"Authorization": "Bearer s", "Content-Type": "application/json"}
