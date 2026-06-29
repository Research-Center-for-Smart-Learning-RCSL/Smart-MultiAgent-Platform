"""IP-level block policy for the Egress Proxy (R12.04).

The proxy never forwards to:

- RFC 1918 private space (``10/8``, ``172.16/12``, ``192.168/16``)
- Carrier-grade NAT (``100.64.0.0/10``) — backs cloud-internal load balancers,
  k8s node/pod ranges, and Tailscale; the stdlib does NOT flag it as private
  (SEC-H4), so it is enumerated explicitly below.
- NAT64 well-known prefix (``64:ff9b::/96``) — embeds IPv4 targets in IPv6 and
  is only caught incidentally / version-dependently by ``is_reserved``; blocked
  explicitly so the policy does not depend on stdlib version (SEC-H4).
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
_EXPLICIT_BLOCKED_IPS: frozenset[str] = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure IMDS (also link-local)
        "100.100.100.200",  # Alibaba
        "192.0.0.192",  # Oracle OCI
        "169.254.170.2",  # AWS ECS task metadata
        "169.254.170.23",  # AWS ECS exec
    }
)

# Networks the stdlib flags do NOT reliably catch, so we enumerate them.
_EXPLICIT_BLOCKED_NETWORKS: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...] = (
    # IBM metadata is an entire /16 carved out of a public range.
    ipaddress.ip_network("161.26.0.0/16"),
    # SEC-H4: Carrier-grade NAT (RFC 6598). `is_private` is False for this
    # range on CPython, so without this entry an allowlisted hostname resolving
    # into CGNAT space (cloud LBs, k8s, Tailscale) would be forwarded.
    ipaddress.ip_network("100.64.0.0/10"),
    # SEC-H4: NAT64 well-known prefix (RFC 6052) — `64:ff9b::/96` embeds an
    # IPv4 destination in an IPv6 address; only caught incidentally by
    # `is_reserved` and version-dependently, so block it explicitly.
    ipaddress.ip_network("64:ff9b::/96"),
    # SEC: 6to4 (RFC 3056) and Teredo (RFC 4380) tunnel an IPv4 destination
    # inside an IPv6 address. The embedded v4 (especially the obfuscated Teredo
    # client) is awkward to recover reliably and these are never legitimate MCP
    # egress targets, so block the whole prefixes outright. IPv4-mapped
    # (::ffff:a.b.c.d) is handled precisely below by re-running the policy on the
    # embedded address.
    ipaddress.ip_network("2002::/16"),
    ipaddress.ip_network("2001::/32"),
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

    # SEC-H4: an IPv4-mapped IPv6 address (``::ffff:a.b.c.d``) routes to the
    # embedded IPv4 on a dual-stack socket, but the stdlib `is_private` etc.
    # delegate to the (often public) embedded address and the version-guarded
    # explicit checks below would skip it — letting ``::ffff:100.100.100.200``
    # reach cloud metadata. Re-run the whole policy on the embedded v4 so it is
    # classified as the address it will actually reach.
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return is_blocked_ip(str(addr.ipv4_mapped))

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
    if addr.version == 6 and addr in ipaddress.ip_network("fc00::/7"):  # noqa: SIM103 (guard-clause chain)
        return True

    return False


__all__ = ["is_blocked_ip"]
