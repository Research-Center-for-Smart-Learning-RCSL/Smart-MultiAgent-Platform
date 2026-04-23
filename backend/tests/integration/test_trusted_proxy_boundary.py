"""Trust boundary for X-Forwarded-For parsing (R19a.10, R19a.11).

Validates `resolve_actor_ip` under every scenario enumerated in C.11:
- untrusted peer forwarding XFF → header ignored, peer wins
- trusted peer with single XFF entry → entry wins
- trusted peer with chain of trusted + one untrusted → right-most
  non-trusted wins (walked right-to-left)
- chain of only trusted addresses → fall back to peer
- malformed XFF entries are skipped, not fatal
"""

from __future__ import annotations

from shared_kernel.auth.trusted_proxy import resolve_actor_ip


TRUSTED = ["127.0.0.0/8", "10.0.0.0/8"]


def test_untrusted_peer_xff_ignored() -> None:
    # Evil client spoofs XFF; peer is public → header is ignored entirely.
    got = resolve_actor_ip(
        peer_ip="203.0.113.9",
        forwarded_for="1.2.3.4, 5.6.7.8",
        trusted_cidrs=TRUSTED,
    )
    assert got == "203.0.113.9"


def test_trusted_peer_single_xff() -> None:
    got = resolve_actor_ip(
        peer_ip="127.0.0.1",
        forwarded_for="203.0.113.9",
        trusted_cidrs=TRUSTED,
    )
    assert got == "203.0.113.9"


def test_trusted_peer_chain_right_to_left() -> None:
    # Shape: [client, trusted-proxy-A, trusted-proxy-B]. Walking R→L we
    # skip B, skip A, land on client.
    got = resolve_actor_ip(
        peer_ip="127.0.0.1",
        forwarded_for="203.0.113.9, 10.0.0.1, 10.0.0.2",
        trusted_cidrs=TRUSTED,
    )
    assert got == "203.0.113.9"


def test_trusted_peer_xff_contains_only_trusted_hops() -> None:
    got = resolve_actor_ip(
        peer_ip="127.0.0.1",
        forwarded_for="10.0.0.1, 10.0.0.2",
        trusted_cidrs=TRUSTED,
    )
    # No non-trusted address in the chain → fall back to peer.
    assert got == "127.0.0.1"


def test_trusted_peer_no_xff_returns_peer() -> None:
    got = resolve_actor_ip(
        peer_ip="127.0.0.1",
        forwarded_for=None,
        trusted_cidrs=TRUSTED,
    )
    assert got == "127.0.0.1"


def test_malformed_entries_are_skipped() -> None:
    got = resolve_actor_ip(
        peer_ip="127.0.0.1",
        forwarded_for="not-an-ip, , 203.0.113.9, 10.0.0.1",
        trusted_cidrs=TRUSTED,
    )
    assert got == "203.0.113.9"
