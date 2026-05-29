"""SEC-C1 regression guard — the MCP sandbox egress network must stay internal.

The critical finding C1 was that ``egress_net`` had a default gateway, so a
hostile MCP tool inside the sandbox could open a raw socket to cloud metadata,
the data plane (postgres/redis/vault), or any public host — bypassing the
egress proxy's HMAC + allowlist + IP policy entirely. The fix makes
``egress_net`` ``internal: true`` so the only reachable peer is the
egress-proxy (which straddles the internal sandbox network and the
non-internal ``backend_net`` for its own outbound route).

These assertions fail loudly if a future edit re-introduces the gateway.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_COMPOSE = Path(__file__).resolve().parents[3] / "deploy" / "compose" / "docker-compose.yml"


def _compose() -> dict:
    with _COMPOSE.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_compose_file_exists() -> None:
    assert _COMPOSE.is_file(), f"compose file not found at {_COMPOSE}"


def test_egress_net_is_internal() -> None:
    """The chokepoint: no gateway on the sandbox network."""
    doc = _compose()
    egress = doc["networks"]["egress_net"]
    assert egress.get("internal") is True


def test_egress_proxy_bridges_internal_and_outbound_networks() -> None:
    """The proxy must sit on BOTH egress_net (reachable by sandboxes) and the
    non-internal backend_net (its own internet route); otherwise the sandbox
    would have no working egress path at all once egress_net is internal."""
    doc = _compose()
    nets = doc["services"]["egress-proxy"]["networks"]
    assert "egress_net" in nets
    assert "backend_net" in nets


def test_backend_net_keeps_a_gateway() -> None:
    """The egress-proxy's outbound route depends on backend_net NOT being
    internal."""
    doc = _compose()
    assert doc["networks"]["backend_net"].get("internal") in (False, None)
