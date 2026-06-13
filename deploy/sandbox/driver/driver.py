#!/usr/bin/env python3
"""In-image sandbox driver (K.5) — the guest side of ``docker_runsc.py``.

The host runs this image with ``command=[<subcommand>]`` and ``SMAP_*`` env vars,
reads one JSON/text result on **stdout**, and maps **exit 42** to egress-denied.
Subcommands (host contract):

  probe   — start an MCP server, ``initialize`` + ``tools/list``; print
            ``{"tools": [...]}`` on stdout.
  invoke  — call one MCP tool; print the tool's text result on stdout
            (diagnostics on stderr); exit code is the tool's success.
  file    — list / read / write inside ``/workspace`` (the per-agent volume).
            For ``write`` the host stages the payload into the volume with
            ``put_archive`` and this driver renames it onto the target path.
  exec    — run a Python snippet (the code-exec image normally runs
            ``python -c`` directly; this is here for protocol completeness).

MCP transport by ``SMAP_MCP_SOURCE``:
  package — a stdio server launched from the image's baked-in package cache
            (``npx`` / ``uvx``); the gateway-less sandbox network means runtime
            registry installs do NOT reach out, so servers must be pre-baked.
  url     — an HTTP MCP endpoint reached **through the egress proxy** (the proxy
            is a custom HMAC forwarder, not a transparent ``HTTP_PROXY``; the
            host pre-signs the per-project HMAC and passes it in the env, so the
            raw shared secret never enters the sandbox).

Heavy deps (the ``mcp`` SDK, ``httpx``) live only in the image and are imported
lazily so the pure helpers in :mod:`protocol` stay testable on any host.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

# When run as the image entrypoint the package dir is on sys.path; support both
# ``python -m driver`` and ``python /opt/driver/driver.py``.
try:
    from protocol import (
        EXIT_EGRESS_DENIED,
        EXIT_ERROR,
        EXIT_OK,
        egress_headers,
        frame_tools,
        is_egress_denied_response,
        parse_command,
        parse_package_reference,
        safe_workspace_path,
    )
except ImportError:  # pragma: no cover - packaged form
    from driver.protocol import (  # type: ignore[no-redef]
        EXIT_EGRESS_DENIED,
        EXIT_ERROR,
        EXIT_OK,
        egress_headers,
        frame_tools,
        is_egress_denied_response,
        parse_command,
        parse_package_reference,
        safe_workspace_path,
    )


class EgressDenied(Exception):
    """Raised when the egress proxy refuses a URL-source request (→ exit 42)."""


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


# --------------------------------------------------------------------------- #
# MCP — stdio (source="package")                                              #
# --------------------------------------------------------------------------- #


# Runtime vars the stdio child needs (npx/uvx caches + HOME live under the
# writable /tmp tmpfs). ``StdioServerParameters(env=...)`` REPLACES the child
# env rather than merging, so we must forward these explicitly — and we forward
# ONLY these, never the SMAP_* egress secrets, so a semi-trusted MCP server
# can't read the project's pre-signed HMAC.
_INHERIT_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "NODE_PATH",
    "NPM_CONFIG_CACHE",
    "UV_CACHE_DIR",
)


def _stdio_params(reference: str, auth: dict[str, Any]) -> Any:
    from mcp import StdioServerParameters  # type: ignore[import-not-found]

    spec = parse_package_reference(reference)
    child_env = {k: os.environ[k] for k in _INHERIT_ENV if k in os.environ}
    child_env.setdefault("PATH", "/usr/local/bin:/usr/bin:/bin")
    # Auth for stdio servers is conventionally injected as env vars.
    env_extra = auth.get("env") if isinstance(auth, dict) else None
    if isinstance(env_extra, dict):
        child_env.update({str(k): str(v) for k, v in env_extra.items()})
    return StdioServerParameters(command=spec.command, args=list(spec.args), env=child_env)


async def _with_stdio_session(reference: str, auth: dict[str, Any], fn: Any) -> Any:
    from mcp import ClientSession  # type: ignore[import-not-found]
    from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]

    params = _stdio_params(reference, auth)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        return await fn(session)


# --------------------------------------------------------------------------- #
# MCP — url (source="url") through the egress proxy                            #
# --------------------------------------------------------------------------- #


async def _proxy_jsonrpc(method: str, params: dict[str, Any], *, request_id: int) -> dict[str, Any]:
    """Send one JSON-RPC request to a URL MCP server via the egress proxy.

    Stateless JSON-RPC-over-HTTP (POST) subset — enough for initialize /
    tools/list / tools/call against HTTP MCP servers. The proxy authenticates
    the project and forwards to the real URL named in ``x-smap-egress-url``.
    """
    import httpx  # type: ignore[import-not-found]

    proxy_url = _env("SMAP_EGRESS_PROXY_URL")
    target_url = _env("SMAP_MCP_REFERENCE")
    hmac_sig = _env("SMAP_EGRESS_HMAC")
    project_id = _env("SMAP_PROJECT_ID")
    if not (proxy_url and target_url and hmac_sig):
        raise EgressDenied("egress proxy not configured for url-source MCP")

    headers = egress_headers(project_id=project_id, hmac_signature=hmac_sig, target_url=target_url)
    headers["content-type"] = "application/json"
    body = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(proxy_url, headers=headers, json=body)
    ctype = resp.headers.get("content-type", "")
    if is_egress_denied_response(resp.status_code, ctype, resp.content):
        raise EgressDenied(f"egress proxy denied url MCP request: {resp.text[:200]}")
    if resp.status_code >= 400:
        raise RuntimeError(f"url MCP server returned {resp.status_code}: {resp.text[:200]}")
    parsed = resp.json()
    if "error" in parsed:
        raise RuntimeError(f"url MCP error: {parsed['error']}")
    return parsed.get("result", {})  # type: ignore[no-any-return]


# --------------------------------------------------------------------------- #
# Subcommands                                                                  #
# --------------------------------------------------------------------------- #


async def cmd_probe() -> int:
    source = _env("SMAP_MCP_SOURCE")
    reference = _env("SMAP_MCP_REFERENCE")
    auth = json.loads(_env("SMAP_MCP_AUTH_JSON", "{}") or "{}")

    if source == "url":
        result = await _proxy_jsonrpc("tools/list", {}, request_id=1)
        names = [str(t.get("name", "")) for t in result.get("tools", [])]
    else:  # package (and any stdio-style source)
        async def _list(session: Any) -> list[str]:
            listed = await session.list_tools()
            return [t.name for t in listed.tools]

        names = await _with_stdio_session(reference, auth, _list)

    sys.stdout.write(frame_tools([n for n in names if n]))
    return EXIT_OK


async def cmd_invoke() -> int:
    source = _env("SMAP_MCP_SOURCE")
    reference = _env("SMAP_MCP_REFERENCE")
    auth = json.loads(_env("SMAP_MCP_AUTH_JSON", "{}") or "{}")
    tool_name = _env("SMAP_TOOL_NAME")
    arguments = json.loads(_env("SMAP_TOOL_ARGS_JSON", "{}") or "{}")

    if source == "url":
        result = await _proxy_jsonrpc("tools/call", {"name": tool_name, "arguments": arguments}, request_id=1)
        text = _render_tool_content(result.get("content", []))
        sys.stdout.write(text)
        return EXIT_OK if not result.get("isError") else EXIT_ERROR

    async def _call(session: Any) -> tuple[str, bool]:
        res = await session.call_tool(tool_name, arguments)
        # mcp SDK CallToolResult: ``.content`` (list of content blocks),
        # ``.isError`` (bool).
        blocks = [_block_to_dict(b) for b in (res.content or [])]
        return _render_tool_content(blocks), bool(getattr(res, "isError", False))

    text, is_error = await _with_stdio_session(reference, auth, _call)
    sys.stdout.write(text)
    return EXIT_ERROR if is_error else EXIT_OK


def _block_to_dict(block: Any) -> dict[str, Any]:
    if isinstance(block, dict):
        return block
    return {"type": getattr(block, "type", "text"), "text": getattr(block, "text", "")}


def _render_tool_content(content: list[Any]) -> str:
    """Flatten MCP tool content blocks to plain text for the host's stdout."""
    parts: list[str] = []
    for block in content:
        b = _block_to_dict(block)
        if b.get("type") == "text" and b.get("text"):
            parts.append(str(b["text"]))
        else:
            parts.append(json.dumps(b, separators=(",", ":")))
    return "\n".join(parts)


def cmd_file() -> int:
    op = _env("SMAP_FILE_OP")
    path = safe_workspace_path(_env("SMAP_FILE_PATH", "/workspace"))

    if op == "list":
        entries = sorted(os.listdir(path)) if os.path.isdir(path) else [os.path.basename(path)]
        sys.stdout.write("\n".join(entries))
        return EXIT_OK
    if op == "read":
        with open(path, "rb") as fh:
            sys.stdout.buffer.write(fh.read())
        return EXIT_OK
    if op == "write":
        # The host stages the payload into the volume via put_archive before
        # starting this container (K.5 FIX 4 — env vars cap at ~128 KiB, far
        # below the tool's 10 MB budget). Same volume → os.replace is an
        # atomic rename, no second copy of the payload.
        staging = safe_workspace_path(_env("SMAP_FILE_STAGING"))
        os.makedirs(os.path.dirname(path) or "/workspace", exist_ok=True)
        try:
            size = os.path.getsize(staging)
            os.replace(staging, path)
        except OSError:
            # Don't leave a stray staged payload behind on failure.
            if os.path.exists(staging):
                os.unlink(staging)
            raise
        sys.stdout.write(json.dumps({"written": size, "path": path}))
        return EXIT_OK
    sys.stderr.write(f"unknown file op {op!r}")
    return EXIT_ERROR


def cmd_exec() -> int:
    # The code-exec image normally runs ``python -c <source>`` directly (no
    # driver). This path supports an explicit ``exec`` subcommand for protocol
    # completeness: source on argv[2] or SMAP_CODE_EXEC_SOURCE.
    source = sys.argv[2] if len(sys.argv) > 2 else _env("SMAP_CODE_EXEC_SOURCE")
    if not source:
        sys.stderr.write("exec: no source provided")
        return EXIT_ERROR
    ns: dict[str, Any] = {"__name__": "__main__"}
    exec(compile(source, "<sandbox>", "exec"), ns)  # noqa: S102 — sandboxed by gVisor
    return EXIT_OK


# --------------------------------------------------------------------------- #
# Entrypoint                                                                   #
# --------------------------------------------------------------------------- #


def main(argv: list[str]) -> int:
    command = parse_command(argv)
    try:
        if command == "probe":
            return asyncio.run(cmd_probe())
        if command == "invoke":
            return asyncio.run(cmd_invoke())
        if command == "file":
            return cmd_file()
        if command == "exec":
            return cmd_exec()
        sys.stderr.write(f"unknown driver command {command!r}")
        return EXIT_ERROR
    except EgressDenied as exc:
        sys.stderr.write(str(exc))
        return EXIT_EGRESS_DENIED
    except Exception as exc:  # top-level guard maps any fault to a non-zero exit
        sys.stderr.write(f"{type(exc).__name__}: {exc}")
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
