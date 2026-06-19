"""Pure, stdlib-only helpers for the in-image sandbox driver (K.5).

This module is deliberately dependency-free (no ``mcp`` SDK, no ``httpx``) so the
host-contract logic — command parsing, package-reference → launch spec, the
``/workspace`` path guard, egress header construction, and the exit-code map —
can be unit-tested on any machine without building the image. The I/O-bearing
entrypoint (:mod:`driver`) imports these helpers and adds the MCP-SDK / httpx
parts that only exist inside the container.

The HOST is the contract. ``contexts/agents/infrastructure/sandbox/docker_runsc.py``
runs this image with ``command=[<cmd>]`` (one of ``probe`` / ``invoke`` / ``file``)
and a fixed set of ``SMAP_*`` env vars, reads **stdout** for the JSON/text result,
and maps **exit code 42** to "egress denied". Everything here conforms to that.
"""

from __future__ import annotations

import json
import posixpath
from dataclasses import dataclass

# Exit codes the host (docker_runsc.py) interprets. 0 = ok, 42 = egress denied
# (McpEgressDenied), anything else = generic failure surfaced in logs.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_EGRESS_DENIED = 42

WORKSPACE_ROOT = "/workspace"

# Reference prefixes for source="package". A bare reference defaults to npx
# (the npm MCP-server ecosystem is the common case). Servers must be present in
# the image's package cache — the sandbox network is gateway-less, so runtime
# registry installs do not reach out (see deploy/sandbox/README → "baked-in").
_RUNNERS: dict[str, str] = {
    "npx": "npx",
    "uvx": "uvx",
    "node": "node",
    "python": "python",
}


@dataclass(frozen=True, slots=True)
class LaunchSpec:
    """A stdio MCP server's launch command (source="package")."""

    command: str
    args: tuple[str, ...]


def parse_command(argv: list[str]) -> str:
    """Return the driver subcommand from argv, or '' if absent."""
    return argv[1] if len(argv) > 1 else ""


def parse_package_reference(reference: str) -> LaunchSpec:
    """Map a ``source="package"`` reference to a stdio launch command.

    Forms (the part before the first ``:`` selects the runner):
      - ``npx:@scope/pkg``        → ``npx -y @scope/pkg``
      - ``uvx:mcp-server-git``    → ``uvx mcp-server-git``
      - ``node:/srv/server.js``   → ``node /srv/server.js``
      - ``python:-m pkg.server``  → ``python -m pkg.server``
      - bare ``@scope/pkg``       → ``npx -y @scope/pkg`` (default runner)

    Extra whitespace-separated tokens after the spec become additional args, so
    ``uvx:mcp-server-git --repo /workspace`` is honoured.
    """
    ref = (reference or "").strip()
    if not ref:
        raise ValueError("empty package reference")

    runner_key, sep, rest = ref.partition(":")
    if sep and runner_key in _RUNNERS:
        command = _RUNNERS[runner_key]
        tokens = rest.split()
        if command == "npx":
            return LaunchSpec("npx", ("-y", *tokens))
        return LaunchSpec(command, tuple(tokens))

    # No recognised runner prefix → treat the whole thing as an npm package.
    tokens = ref.split()
    return LaunchSpec("npx", ("-y", *tokens))


def safe_workspace_path(raw: str) -> str:
    """Resolve ``raw`` to a ``/workspace``-rooted path or raise ``ValueError``.

    Mirrors the backend ``file_tool._safe_relpath`` guard so the driver enforces
    the same containment the API surface already rejects (defence in depth — the
    read-only root FS is the real boundary, this is the fast fail)."""
    if not isinstance(raw, str) or not raw:
        raise ValueError("path must be a non-empty string")
    if "\x00" in raw:
        raise ValueError("null byte in path")
    candidate = raw if raw.startswith("/") else posixpath.join(WORKSPACE_ROOT, raw)
    normed = posixpath.normpath(candidate)
    if not (normed == WORKSPACE_ROOT or normed.startswith(WORKSPACE_ROOT + "/")):
        raise ValueError(f"path escapes sandbox: {raw!r}")
    return normed


def egress_headers(
    *, project_id: str, hmac_signature: str, target_url: str
) -> dict[str, str]:
    """Build the custom egress-proxy headers (mirrors HttpxEgressProxyClient).

    The proxy is NOT a transparent forward proxy — it authenticates with a
    per-project HMAC and forwards the absolute URL named in ``x-smap-egress-url``.
    The host pre-computes the HMAC (so the raw shared secret never enters the
    sandbox) and passes it via ``SMAP_EGRESS_HMAC``; the driver only relays it.
    """
    return {
        "x-smap-project-id": project_id,
        "x-smap-egress-hmac": hmac_signature,
        "x-smap-egress-url": target_url,
    }


def is_egress_denied_response(status_code: int, content_type: str, body: bytes) -> bool:
    """True iff a proxy response is the proxy's OWN denial (→ exit 42).

    The proxy flags its denials with ``application/problem+json`` whose ``type``
    ends in ``mcp-egress-denied``; an upstream 4xx/5xx is relayed 1:1 and must
    NOT be treated as a denial."""
    if status_code not in (400, 401, 403, 502):
        return False
    if not content_type.startswith("application/problem+json"):
        return False
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False
    return str(parsed.get("type", "")).endswith("mcp-egress-denied")


def frame_tools(tool_names: list[str]) -> str:
    """The host's probe reader does ``json.loads(stdout).get('tools', [])``."""
    return json.dumps({"tools": list(tool_names)})


__all__ = [
    "EXIT_EGRESS_DENIED",
    "EXIT_ERROR",
    "EXIT_OK",
    "WORKSPACE_ROOT",
    "LaunchSpec",
    "egress_headers",
    "frame_tools",
    "is_egress_denied_response",
    "parse_command",
    "parse_package_reference",
    "safe_workspace_path",
]
