"""Unit tests for the Egress Proxy IP policy — E.10 / R12.04."""

from __future__ import annotations

import pytest

from services.egress_proxy.ip_policy import is_blocked_ip


@pytest.mark.parametrize(
    "ip",
    [
        "10.0.0.1",  # RFC 1918
        "10.255.255.255",
        "172.16.0.1",  # RFC 1918
        "172.31.255.254",
        "192.168.1.1",  # RFC 1918
        "127.0.0.1",  # loopback
        "127.255.255.255",
        "169.254.0.1",  # link-local
        "169.254.169.254",  # AWS / GCP / Azure IMDS
        "169.254.170.2",  # AWS ECS metadata
        "100.100.100.200",  # Alibaba
        "192.0.0.192",  # Oracle
        "161.26.1.1",  # IBM (explicit block)
        "100.64.0.1",  # CGNAT (RFC 6598) — SEC-H4
        "100.127.255.254",  # CGNAT upper edge — SEC-H4
        "64:ff9b::1.1.1.1",  # NAT64 well-known prefix (RFC 6052) — SEC-H4
        "64:ff9b::ffff:ffff",  # NAT64 wrapping a public-looking v4 — SEC-H4
        "::ffff:100.100.100.200",  # IPv4-mapped Alibaba metadata — SEC-H4
        "::ffff:169.254.169.254",  # IPv4-mapped IMDS — SEC-H4
        "::ffff:10.0.0.1",  # IPv4-mapped RFC1918 — SEC-H4
        "2002::1",  # 6to4 tunnel prefix — SEC-H4
        "2001::1",  # Teredo tunnel prefix — SEC-H4
        "::1",  # IPv6 loopback
        "fe80::1",  # IPv6 link-local
        "fc00::1",  # IPv6 unique-local
        "fd12:3456::1",  # IPv6 ULA
        "::",  # unspecified
        "not-an-ip",  # malformed → blocked
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
        "93.184.216.34",  # example.com
        "2001:4860:4860::8888",  # Google public IPv6 DNS
        "2606:4700:4700::1111",  # Cloudflare public IPv6 DNS
        "::ffff:1.1.1.1",  # IPv4-mapped *public* address must still pass
    ],
)
def test_public_passes(ip: str) -> None:
    assert is_blocked_ip(ip) is False, ip
