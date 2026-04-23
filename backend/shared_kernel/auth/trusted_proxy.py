"""X-Forwarded-For parser with a hard trust boundary (R19a.10, R19a.11).

Operator configures `security.trusted_proxies` as CIDRs. For each request we
walk `X-Forwarded-For` from right to left, skipping trusted-proxy addresses,
and stop at the first address NOT in the list. That address is the
user-facing IP. If the immediate peer itself is not in the trust list we
*ignore* the header entirely and use the peer address.

This parser is pure — give it the raw header + peer + CIDR list and it
returns the actor_ip. The FastAPI middleware wraps it.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence


def resolve_actor_ip(
    *,
    peer_ip: str,
    forwarded_for: str | None,
    trusted_cidrs: Sequence[str],
) -> str:
    networks = [ipaddress.ip_network(c, strict=False) for c in trusted_cidrs]
    peer_addr = _parse(peer_ip)
    if peer_addr is None or not any(peer_addr in n for n in networks):
        # Peer itself is untrusted — ignore whatever header was sent.
        return peer_ip

    if not forwarded_for:
        return peer_ip

    # Walk right-to-left; first non-trusted address wins.
    for raw in reversed([s.strip() for s in forwarded_for.split(",")]):
        if not raw:
            continue
        addr = _parse(raw)
        if addr is None:
            continue
        if any(addr in n for n in networks):
            continue
        return str(addr)
    # Everything in the chain was a trusted proxy — fall back to peer.
    return peer_ip


def _parse(raw: str) -> ipaddress._BaseAddress | None:
    try:
        return ipaddress.ip_address(raw)
    except ValueError:
        return None


__all__ = ["resolve_actor_ip"]
