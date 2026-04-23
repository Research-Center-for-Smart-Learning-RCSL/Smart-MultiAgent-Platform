"""Unit tests for the Egress Proxy IP policy — E.10 / R12.04."""

from __future__ import annotations

import pytest

from services.egress_proxy.ip_policy import is_blocked_ip


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",         # RFC 1918
        "10.255.255.255",
        "172.16.0.1",       # RFC 1918
        "172.31.255.254",
        "192.168.1.1",      # RFC 1918
        "127.0.0.1",        # loopback
        "127.255.255.255",
        "169.254.0.1",      # link-local
        "169.254.169.254",  # AWS / GCP / Azure IMDS
        "169.254.170.2",    # AWS ECS metadata
        "100.100.100.200",  # Alibaba
        "192.0.0.192",      # Oracle
        "161.26.1.1",       # IBM (explicit block)
        "::1",              # IPv6 loopback
        "fe80::1",          # IPv6 link-local
        "fc00::1",          # IPv6 unique-local
        "fd12:3456::1",     # IPv6 ULA
        "::",               # unspecified
        "not-an-ip",        # malformed → blocked
    ],
)
def test_blocked(ip: str) -> None:
    assert is_blocked_ip(ip) is True, ip


@pytest.mark.parametrize(
    "ip",
    [
        "1.1.1.1",
        "8.8.8.8",
        "208.67.222.222",
        "93.184.216.34",     # example.com
        "2001:4860:4860::8888",  # Google public IPv6 DNS
        "2606:4700:4700::1111",  # Cloudflare public IPv6 DNS
    ],
)
def test_public_passes(ip: str) -> None:
    assert is_blocked_ip(ip) is False, ip
