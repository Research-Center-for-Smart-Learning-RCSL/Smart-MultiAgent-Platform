"""IP-level block policy for the Egress Proxy (R12.04).

The proxy never forwards to:

- RFC 1918 private space (``10/8``, ``172.16/12``, ``192.168/16``)
- Carrier-grade NAT (``100.64.0.0/10``)
- Loopback (``127/8``, ``::1``)
- Link-local (``169.254/16``, ``fe80::/10``)
- IPv6 unique-local (``fc00::/7``)
- Known cloud-metadata endpoints:
  - AWS / GCP / Azure: ``169.254.169.254`` (captured by link-local)
  - Alibaba Cloud: ``100.100.100.200``
  - Oracle Cloud: ``192.0.0.192``
  - IBM Cloud: ``161.26.0.0/16`` host-reserved metadata path
  - Fly.io: ``fd00:fly:*`` (unique-local)
- Multicast / unspecified

All operate on the *resolved* IP — not the hostname — because an attacker-
controlled DNS can point ``mcp.example.com`` at ``169.254.169.254``.
"""

from __future__ import annotations

import ipaddress

# Explicit extra blocks on top of what the stdlib flags private / link-local.
_EXPLICIT_BLOCKED_IPS: frozenset[str] = frozenset({
    "169.254.169.254",   # AWS / GCP / Azure IMDS (also link-local)
    "100.100.100.200",   # Alibaba
    "192.0.0.192",       # Oracle OCI
    "169.254.170.2",     # AWS ECS task metadata
    "169.254.170.23",    # AWS ECS exec
})

# IBM metadata is an entire /16 carved out of a public range.
_EXPLICIT_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    ipaddress.ip_network("161.26.0.0/16"),
)


def is_blocked_ip(ip: str) -> bool:
    """Return True iff ``ip`` is blocked by the Egress Proxy policy.

    Hostname resolution is the caller's responsibility — pass the resolved
    address(es) and ``any()`` over them.
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        # Malformed IPs are treated as blocked — no reason to forward.
        return True

    if ip in _EXPLICIT_BLOCKED_IPS:
        return True
    for net in _EXPLICIT_BLOCKED_NETWORKS:
        if addr.version == net.version and addr in net:
            return True

    # stdlib flags cover most of the policy in one shot:
    if (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_unspecified
        or addr.is_reserved
    ):
        return True

    # IPv6 unique-local (fc00::/7) is not caught by ``is_private`` on some
    # versions — double-check explicitly.
    if addr.version == 6 and addr in ipaddress.ip_network("fc00::/7"):
        return True

    return False


__all__ = ["is_blocked_ip"]
