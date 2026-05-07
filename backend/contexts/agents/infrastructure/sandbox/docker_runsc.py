"""Docker + ``runsc`` implementation of :class:`SandboxRunner` (R12.03 / R12.05).

Every call runs an ephemeral container with:

- ``runtime=runsc`` (gVisor user-space kernel)
- ``user`` ≥ 10000, ``no-new-privileges``, read-only root FS
- 100 MB tmpfs at ``/workspace`` (for ``file`` the tmpfs is swapped for the
  per-agent named volume ``smap-agent-fs-{agent_id}``)
- memory=512m, cpus=0.5, pids_limit=128, nofile ulimit 512
- Network attached to ``smap_egress_net`` only; DNS restricted
- ``--rm`` at exit

Docker SDK imports are **lazy** — the module imports cleanly without Docker
installed, so unit tests can exercise the pure bits without requiring a
daemon. Tests should swap in a fake :class:`SandboxRunner` rather than touch
this class.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

from contexts.agents.domain.errors import McpEgressDenied, McpTimeout
from contexts.agents.domain.mcp import McpTestResult, ToolCallResult

# Pinned by digest in production; the tag here is a placeholder used only as
# a repr. Ops-side rebuild pipeline re-stamps the digest per agent-version
# every 24 hours (R12.03 "package rebuild cached per-agent-version 24h").
_DEFAULT_MCP_IMAGE = "smap/mcp-runtime:pinned"
_DEFAULT_CODE_IMAGE = "smap/code-exec:pinned"
_EGRESS_NETWORK = "smap_egress_net"

_SANDBOX_UID = 10001
_MEMORY = "512m"
_CPUS = 0.5
_PIDS_LIMIT = 128
_NOFILE_LIMIT = 512
_WORKSPACE_TMPFS_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DockerRunscSandbox:
    """Concrete :class:`SandboxRunner` backed by the local Docker daemon."""

    mcp_image: str = _DEFAULT_MCP_IMAGE
    code_exec_image: str = _DEFAULT_CODE_IMAGE
    egress_network: str = _EGRESS_NETWORK

    def _client(self) -> Any:
        """Lazy-import the docker SDK so unit tests don't need it installed."""
        import docker

        return docker.from_env()

    def _base_host_config(self) -> dict[str, Any]:
        return {
            "runtime": "runsc",
            "network_mode": self.egress_network,
            "mem_limit": _MEMORY,
            "nano_cpus": int(_CPUS * 1_000_000_000),
            "pids_limit": _PIDS_LIMIT,
            "read_only": True,
            "security_opt": ["no-new-privileges"],
            "cap_drop": ["ALL"],
            "ulimits": [{"Name": "nofile", "Soft": _NOFILE_LIMIT, "Hard": _NOFILE_LIMIT}],
            "auto_remove": True,
        }

    async def probe(
        self,
        *,
        agent_id: uuid.UUID,
        source: str,
        reference: str,
        allowed_tools: Sequence[str],
        auth: dict[str, Any] | None,
        project_id: uuid.UUID,
        timeout_s: float = 20.0,
    ) -> McpTestResult:
        start = time.monotonic()
        try:
            tools = await self._run_mcp_probe(
                agent_id=agent_id,
                source=source,
                reference=reference,
                auth=auth,
                project_id=project_id,
                timeout_s=timeout_s,
            )
        except TimeoutError as exc:
            raise McpTimeout(
                f"MCP probe exceeded {timeout_s:.1f}s budget",
            ) from exc
        duration_ms = int((time.monotonic() - start) * 1000)
        allowed_set = set(allowed_tools)
        # If the caller pre-declared an allowlist, intersect; else accept all.
        if allowed_set:
            tools = tuple(t for t in tools if t in allowed_set)
        return McpTestResult(
            ok=True,
            tool_names=tuple(tools),
            duration_ms=duration_ms,
        )

    async def _run_mcp_probe(
        self,
        *,
        agent_id: uuid.UUID,
        source: str,
        reference: str,
        auth: dict[str, Any] | None,
        project_id: uuid.UUID,
        timeout_s: float,
    ) -> tuple[str, ...]:
        """Invoke ``initialize`` + ``tools/list`` inside a one-shot container.

        The actual MCP wire-protocol driver is shipped inside ``mcp_image``
        as an entrypoint that prints JSON on stdout. Egress denial surfaces
        as a specific exit code the driver sets.
        """
        client = self._client()
        host_config = self._base_host_config()
        # Probe container: no volume, 100 MB tmpfs workspace.
        env = {
            "SMAP_AGENT_ID": str(agent_id),
            "SMAP_PROJECT_ID": str(project_id),
            "SMAP_MCP_SOURCE": source,
            "SMAP_MCP_REFERENCE": reference,
            "SMAP_MCP_AUTH_JSON": json.dumps(auth or {}),
        }
        container = client.containers.run(
            image=self.mcp_image,
            command=["probe"],
            environment=env,
            user=_SANDBOX_UID,
            tmpfs={"/workspace": f"size={_WORKSPACE_TMPFS_BYTES}"},
            detach=True,
            **host_config,
        )
        exit_status = container.wait(timeout=timeout_s)
        status_code = int(exit_status.get("StatusCode", 1))
        logs = container.logs(stdout=True, stderr=False).decode(
            "utf-8",
            errors="replace",
        )
        if status_code == 42:
            raise McpEgressDenied("egress proxy denied MCP probe")
        if status_code != 0:
            # Last-ditch — surface exit code in the raised message.
            raise RuntimeError(f"probe container exited {status_code}: {logs[:512]}")
        try:
            parsed = json.loads(logs)
            return tuple(str(t) for t in parsed.get("tools", []))
        except ValueError as exc:
            raise RuntimeError(f"probe container returned non-JSON: {logs[:512]}") from exc

    async def invoke_mcp_tool(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        tool_name: str,
        arguments: dict[str, Any],
        project_id: uuid.UUID,
        timeout_s: float = 60.0,
    ) -> ToolCallResult:
        client = self._client()
        host_config = self._base_host_config()
        env = {
            "SMAP_AGENT_ID": str(agent_id),
            "SMAP_PROJECT_ID": str(project_id),
            "SMAP_BINDING_ID": str(binding_id),
            "SMAP_TOOL_NAME": tool_name,
            "SMAP_TOOL_ARGS_JSON": json.dumps(arguments),
        }
        start = time.monotonic()
        container = client.containers.run(
            image=self.mcp_image,
            command=["invoke"],
            environment=env,
            user=_SANDBOX_UID,
            tmpfs={"/workspace": f"size={_WORKSPACE_TMPFS_BYTES}"},
            detach=True,
            **host_config,
        )
        exit_status = container.wait(timeout=timeout_s)
        duration_ms = int((time.monotonic() - start) * 1000)
        status_code = int(exit_status.get("StatusCode", 1))
        stdout = container.logs(stdout=True, stderr=False).decode(
            "utf-8",
            errors="replace",
        )
        stderr = container.logs(stdout=False, stderr=True).decode(
            "utf-8",
            errors="replace",
        )
        if status_code == 42:
            raise McpEgressDenied("egress proxy denied MCP tool invocation")
        return ToolCallResult(
            ok=status_code == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=status_code,
            duration_ms=duration_ms,
        )

    async def run_file_op(
        self,
        *,
        agent_id: uuid.UUID,
        op: Literal["list", "read", "write"],
        path: str,
        data: bytes | None = None,
        timeout_s: float = 10.0,
    ) -> ToolCallResult:
        client = self._client()
        volume = f"smap-agent-fs-{agent_id}"
        host_config = self._base_host_config()
        # ``file`` container gets the per-agent volume rw on /workspace.
        host_config["volumes"] = {volume: {"bind": "/workspace", "mode": "rw"}}
        # We drop tmpfs for /workspace since it's a real volume; read-only root
        # still applies everywhere else.
        env = {
            "SMAP_AGENT_ID": str(agent_id),
            "SMAP_FILE_OP": op,
            "SMAP_FILE_PATH": path,
        }
        start = time.monotonic()
        if op == "write":
            if data is None:
                raise ValueError("write requires data bytes")
            # The driver reads stdin for the payload when op=write. Docker SDK
            # handles this via exec + stdin; simplest route is to pass data as
            # an environment variable when small, else via the container's
            # own stdin. Base64 to keep it JSON-safe.
            import base64 as _b64

            env["SMAP_FILE_DATA_B64"] = _b64.b64encode(data).decode("ascii")
        container = client.containers.run(
            image=self.mcp_image,
            command=["file"],
            environment=env,
            user=_SANDBOX_UID,
            detach=True,
            **host_config,
        )
        exit_status = container.wait(timeout=timeout_s)
        duration_ms = int((time.monotonic() - start) * 1000)
        status_code = int(exit_status.get("StatusCode", 1))
        stdout = container.logs(stdout=True, stderr=False).decode(
            "utf-8",
            errors="replace",
        )
        stderr = container.logs(stdout=False, stderr=True).decode(
            "utf-8",
            errors="replace",
        )
        return ToolCallResult(
            ok=status_code == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=status_code,
            duration_ms=duration_ms,
            metadata={"volume": volume, "op": op},
        )

    async def run_code_exec(
        self,
        *,
        agent_id: uuid.UUID,
        source: str,
        stdin: str = "",
        timeout_s: float = 30.0,
    ) -> ToolCallResult:
        client = self._client()
        host_config = self._base_host_config()
        # Code exec gets an ephemeral tmpfs workspace, never a named volume.
        env = {"SMAP_AGENT_ID": str(agent_id), "SMAP_CODE_EXEC_STDIN": stdin}
        start = time.monotonic()
        container = client.containers.run(
            image=self.code_exec_image,
            command=["python", "-c", source],
            environment=env,
            user=_SANDBOX_UID,
            tmpfs={"/workspace": f"size={_WORKSPACE_TMPFS_BYTES}"},
            detach=True,
            **host_config,
        )
        try:
            exit_status = container.wait(timeout=min(timeout_s, 30.0))
        except Exception as exc:
            # Force-kill and surface as timeout result rather than raising.
            with contextlib.suppress(Exception):
                container.kill()
            return ToolCallResult(
                ok=False,
                stdout="",
                stderr=f"timeout: {exc}",
                exit_code=124,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        duration_ms = int((time.monotonic() - start) * 1000)
        status_code = int(exit_status.get("StatusCode", 1))
        stdout = container.logs(stdout=True, stderr=False).decode(
            "utf-8",
            errors="replace",
        )
        stderr = container.logs(stdout=False, stderr=True).decode(
            "utf-8",
            errors="replace",
        )
        return ToolCallResult(
            ok=status_code == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=status_code,
            duration_ms=duration_ms,
        )


__all__ = ["DockerRunscSandbox"]
