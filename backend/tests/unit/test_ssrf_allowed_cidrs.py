"""SMAP_SSRF_ALLOWED_CIDRS — whitelist private CIDRs for validate_base_url."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from contexts.keys.infrastructure.probes.base import (
    _allowed_cidrs,
    _is_ssrf_blocked,
    validate_base_url,
)


class TestIsSSRFBlocked:
    def test_public_ip_always_passes(self) -> None:
        assert _is_ssrf_blocked("8.8.8.8") is False

    def test_private_ip_blocked_by_default(self) -> None:
        assert _is_ssrf_blocked("172.20.0.5") is True
        assert _is_ssrf_blocked("10.0.0.1") is True
        assert _is_ssrf_blocked("192.168.1.1") is True

    def test_loopback_blocked_by_default(self) -> None:
        assert _is_ssrf_blocked("127.0.0.1") is True

    @patch.dict("os.environ", {"SMAP_SSRF_ALLOWED_CIDRS": "172.20.0.0/16"})
    def test_private_ip_allowed_when_in_cidr(self) -> None:
        _allowed_cidrs.cache_clear()
        try:
            assert _is_ssrf_blocked("172.20.0.5") is False
            assert _is_ssrf_blocked("172.20.255.255") is False
        finally:
            _allowed_cidrs.cache_clear()

    @patch.dict("os.environ", {"SMAP_SSRF_ALLOWED_CIDRS": "172.20.0.0/16"})
    def test_private_ip_outside_cidr_still_blocked(self) -> None:
        _allowed_cidrs.cache_clear()
        try:
            assert _is_ssrf_blocked("10.0.0.1") is True
            assert _is_ssrf_blocked("192.168.1.1") is True
        finally:
            _allowed_cidrs.cache_clear()

    @patch.dict("os.environ", {"SMAP_SSRF_ALLOWED_CIDRS": "172.20.0.0/16,10.0.0.0/8"})
    def test_multiple_cidrs(self) -> None:
        _allowed_cidrs.cache_clear()
        try:
            assert _is_ssrf_blocked("172.20.0.5") is False
            assert _is_ssrf_blocked("10.0.0.1") is False
            assert _is_ssrf_blocked("192.168.1.1") is True
        finally:
            _allowed_cidrs.cache_clear()

    @patch.dict("os.environ", {"SMAP_SSRF_ALLOWED_CIDRS": "100.64.0.0/10"})
    def test_tailscale_cgnat_range(self) -> None:
        _allowed_cidrs.cache_clear()
        try:
            assert _is_ssrf_blocked("100.108.250.62") is False
            assert _is_ssrf_blocked("100.64.0.1") is False
            assert _is_ssrf_blocked("100.127.255.254") is False
        finally:
            _allowed_cidrs.cache_clear()

    def test_invalid_addr_blocked(self) -> None:
        assert _is_ssrf_blocked("not-an-ip") is True


class TestAllowedCidrs:
    @patch.dict("os.environ", {"SMAP_SSRF_ALLOWED_CIDRS": ""})
    def test_empty_returns_empty(self) -> None:
        _allowed_cidrs.cache_clear()
        try:
            assert _allowed_cidrs() == ()
        finally:
            _allowed_cidrs.cache_clear()

    @patch.dict("os.environ", {}, clear=False)
    def test_unset_returns_empty(self) -> None:
        import os

        os.environ.pop("SMAP_SSRF_ALLOWED_CIDRS", None)
        _allowed_cidrs.cache_clear()
        try:
            assert _allowed_cidrs() == ()
        finally:
            _allowed_cidrs.cache_clear()

    @patch.dict("os.environ", {"SMAP_SSRF_ALLOWED_CIDRS": " 172.20.0.0/16 , 10.0.0.0/8 "})
    def test_whitespace_tolerance(self) -> None:
        _allowed_cidrs.cache_clear()
        try:
            nets = _allowed_cidrs()
            assert len(nets) == 2
        finally:
            _allowed_cidrs.cache_clear()


class TestValidateBaseUrlWithAllowedCidrs:
    @patch("contexts.keys.infrastructure.probes.base.socket.getaddrinfo")
    @patch.dict(
        "os.environ",
        {"SMAP_SSRF_ALLOWED_CIDRS": "172.20.0.0/16", "SMAP_ALLOW_HTTP_PROVIDERS": "1"},
    )
    def test_private_ip_allowed_by_cidr(self, mock_getaddrinfo: object) -> None:
        _allowed_cidrs.cache_clear()
        try:
            mock_getaddrinfo.return_value = [  # type: ignore[union-attr]
                (2, 1, 6, "", ("172.20.0.5", 0)),
            ]
            result = validate_base_url("http://tailscale-nexus:8000")
            assert result == "http://tailscale-nexus:8000"
        finally:
            _allowed_cidrs.cache_clear()

    @patch("contexts.keys.infrastructure.probes.base.socket.getaddrinfo")
    @patch.dict("os.environ", {"SMAP_SSRF_ALLOWED_CIDRS": ""})
    def test_private_ip_blocked_without_cidr(self, mock_getaddrinfo: object) -> None:
        _allowed_cidrs.cache_clear()
        try:
            mock_getaddrinfo.return_value = [  # type: ignore[union-attr]
                (2, 1, 6, "", ("172.20.0.5", 0)),
            ]
            with pytest.raises(ValueError, match="private address"):
                validate_base_url("https://some-host:8000")
        finally:
            _allowed_cidrs.cache_clear()
