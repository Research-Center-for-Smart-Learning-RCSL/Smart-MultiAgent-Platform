"""Docker + ``runsc`` implementation of :class:`SandboxRunner` (R12.03 / R12.05).

Every call runs an ephemeral container with:

- ``runtime=runsc`` (gVisor user-space kernel)
- ``user`` ≥ 10000, ``no-new-privileges``, read-only root FS
- 100 MB tmpfs at ``/workspace`` (for ``file`` the tmpfs is swapped for the
  per-agent named volume ``smap-agent-fs-{agent_id}``)
- memory=512m, cpus=0.5, pids_limit=128, nofile ulimit 512
- Network attached to ``smap_egress_net`` only. That network is declared
  ``internal: true`` in compose (SEC-C1), so the container has **no** default
  gateway: the egress proxy — the single host that straddles ``egress_net``
  and the outbound ``backend_net`` — is the only reachable peer. Do not give
  this container a second network or this network a gateway; that isolation is
  what forces all sandbox egress through the proxy's HMAC + allowlist policy.
- explicit ``remove(force=True)`` in a ``finally`` after every run. NOT
  ``auto_remove``: with auto-remove the daemon may reap the container between
  ``wait()`` returning and ``logs()`` being read, raising ``NotFound`` and
  losing the workload's output (K.5 FIX 6).

Docker SDK imports are **lazy** — the module imports cleanly without Docker
installed, so unit tests can exercise the pure bits without requiring a
daemon. Tests should swap in a fake :class:`SandboxRunner` rather than touch
this class.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import hmac
import json
import logging
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Literal

from contexts.agents.domain.errors import (
    McpEgressDenied,
    McpTimeout,
    SandboxRuntimeViolation,
)
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
# A small writable /tmp tmpfs. The root FS is read-only (SEC), but npx/uvx write
# their cache to $HOME/.npm and matplotlib writes MPLCONFIGDIR — all under /tmp.
# Without this, stdio MCP servers cannot launch and `import matplotlib` fails on
# the read-only rootfs. tmpfs stays writable even when root is read-only (K.5).
_TMP_TMPFS_BYTES = 64 * 1024 * 1024


def _sandbox_tmpfs() -> dict[str, str]:
    return {
        "/workspace": f"size={_WORKSPACE_TMPFS_BYTES}",
        "/tmp": f"size={_TMP_TMPFS_BYTES}",  # noqa: S108 — in-container tmpfs, not a host path
    }


def _tar_single_file(name: str, data: bytes) -> bytes:
    """One-member tar stream for ``put_archive`` (the write-payload transport).

    Owned by the sandbox uid so the driver (also uid 10001) can rename and,
    on failure, unlink the staged file inside the volume.
    """
    import io
    import tarfile

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=name)
        info.size = len(data)
        info.mode = 0o600
        info.uid = info.gid = _SANDBOX_UID
        tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


# Wrapper for ``code_exec`` when the caller supplies stdin (K.5 FIX 7): the
# code-exec image runs ``python -c`` directly (no driver), so a bare env var
# was never read and sys.stdin stayed attached to nothing. When stdin is
# provided we run this shim instead, which materialises SMAP_CODE_EXEC_STDIN
# as sys.stdin and then execs the real source (passed as the next argv token).
# Env-var transport caps stdin at ~128 KiB — fine for the tool's use case.
_STDIN_SHIM = (
    "import io, os, sys\n"
    "sys.stdin = io.StringIO(os.environ.pop('SMAP_CODE_EXEC_STDIN', ''))\n"
    "_src = sys.argv.pop(1)\n"
    "exec(compile(_src, '<code_exec>', 'exec'), {'__name__': '__main__'})\n"
)

_log = logging.getLogger("smap.sandbox")

_MAX_CONCURRENT_CONTAINERS = 8
_CONTAINER_LABEL = "smap.sandbox"
_container_semaphore: asyncio.Semaphore | None = None


def _get_semaphore() -> asyncio.Semaphore:
    global _container_semaphore
    if _container_semaphore is None:
        _container_semaphore = asyncio.Semaphore(_MAX_CONCURRENT_CONTAINERS)
    return _container_semaphore


@dataclass(frozen=True, slots=True)
class DockerRunscSandbox:
    """Concrete :class:`SandboxRunner` backed by the local Docker daemon."""

    mcp_image: str = _DEFAULT_MCP_IMAGE
    code_exec_image: str = _DEFAULT_CODE_IMAGE
    egress_network: str = _EGRESS_NETWORK
    egress_proxy_url: str = ""
    egress_shared_secret: bytes = b""

    def _client(self) -> Any:
        """Lazy-import the docker SDK so unit tests don't need it installed."""
        import docker

        return docker.from_env()

    def _egress_env(self, project_id: uuid.UUID) -> dict[str, str]:
        """Pre-signed egress credentials for the sandbox's URL-MCP path (K.5).

        Empty when egress is unconfigured — the driver then refuses url-source
        egress (exit 42) rather than calling out unauthenticated."""
        if not self.egress_proxy_url or not self.egress_shared_secret:
            return {}
        signature = hmac.new(self.egress_shared_secret, str(project_id).encode("ascii"), sha256).hexdigest()
        return {
            "SMAP_EGRESS_PROXY_URL": self.egress_proxy_url,
            "SMAP_EGRESS_HMAC": signature,
        }

    @staticmethod
    async def _assert_runsc(container: Any) -> None:
        """Verify the freshly-created container actually landed on gVisor."""
        try:
            await asyncio.to_thread(container.reload)
            runtime = (container.attrs.get("HostConfig") or {}).get("Runtime")
        except Exception as exc:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.kill)
            raise SandboxRuntimeViolation(
                "could not confirm sandbox container runtime",
            ) from exc
        if runtime != "runsc":
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.kill)
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.remove, force=True)
            raise SandboxRuntimeViolation(
                f"sandbox container runtime is {runtime!r}, expected 'runsc'; "
                "refusing to run untrusted workload without gVisor isolation",
            )

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
            "auto_remove": False,
            "labels": {_CONTAINER_LABEL: "1"},
        }

    @staticmethod
    async def _remove_quietly(container: Any) -> None:
        """Best-effort container removal — never masks the in-flight result."""
        with contextlib.suppress(Exception):
            await asyncio.to_thread(container.remove, force=True)

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
        async with _get_semaphore():
            client = self._client()
            host_config = self._base_host_config()
            env = {
                "SMAP_AGENT_ID": str(agent_id),
                "SMAP_PROJECT_ID": str(project_id),
                "SMAP_MCP_SOURCE": source,
                "SMAP_MCP_REFERENCE": reference,
                "SMAP_MCP_AUTH_JSON": json.dumps(auth or {}),
                **self._egress_env(project_id),
            }
            container = await asyncio.to_thread(
                client.containers.run,
                image=self.mcp_image,
                command=["probe"],
                environment=env,
                user=_SANDBOX_UID,
                tmpfs=_sandbox_tmpfs(),
                detach=True,
                **host_config,
            )
            try:
                await self._assert_runsc(container)
                try:
                    exit_status = await asyncio.to_thread(container.wait, timeout=timeout_s)
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(container.kill)
                    raise TimeoutError(f"probe container did not exit within {timeout_s:.1f}s") from exc
                status_code = int(exit_status.get("StatusCode", 1))
                raw_logs = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
                logs = raw_logs.decode("utf-8", errors="replace")
            finally:
                await self._remove_quietly(container)
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
        source: str = "",
        reference: str = "",
        auth: dict[str, Any] | None = None,
        timeout_s: float = 60.0,
    ) -> ToolCallResult:
        async with _get_semaphore():
            client = self._client()
            host_config = self._base_host_config()
            env = {
                "SMAP_AGENT_ID": str(agent_id),
                "SMAP_PROJECT_ID": str(project_id),
                "SMAP_BINDING_ID": str(binding_id),
                "SMAP_TOOL_NAME": tool_name,
                "SMAP_TOOL_ARGS_JSON": json.dumps(arguments),
                "SMAP_MCP_SOURCE": source,
                "SMAP_MCP_REFERENCE": reference,
                "SMAP_MCP_AUTH_JSON": json.dumps(auth or {}),
                **self._egress_env(project_id),
            }
            start = time.monotonic()
            container = await asyncio.to_thread(
                client.containers.run,
                image=self.mcp_image,
                command=["invoke"],
                environment=env,
                user=_SANDBOX_UID,
                tmpfs=_sandbox_tmpfs(),
                detach=True,
                **host_config,
            )
            try:
                await self._assert_runsc(container)
                try:
                    exit_status = await asyncio.to_thread(container.wait, timeout=timeout_s)
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(container.kill)
                    raise McpTimeout(
                        f"MCP tool invocation exceeded {timeout_s:.1f}s budget",
                    ) from exc
                duration_ms = int((time.monotonic() - start) * 1000)
                status_code = int(exit_status.get("StatusCode", 1))
                raw_out = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
                stdout = raw_out.decode("utf-8", errors="replace")
                raw_err = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
                stderr = raw_err.decode("utf-8", errors="replace")
            finally:
                await self._remove_quietly(container)
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
        async with _get_semaphore():
            client = self._client()
            volume = f"smap-agent-fs-{agent_id}"
            host_config = self._base_host_config()
            # M19: file_op needs no network — isolate completely.
            host_config["network_mode"] = "none"
            host_config["volumes"] = {volume: {"bind": "/workspace", "mode": "rw"}}
            env = {
                "SMAP_AGENT_ID": str(agent_id),
                "SMAP_FILE_OP": op,
                "SMAP_FILE_PATH": path,
            }
            start = time.monotonic()
            if op == "write":
                if data is None:
                    raise ValueError("write requires data bytes")
                stage_name = f".smap-stage-{uuid.uuid4().hex}"
                env["SMAP_FILE_STAGING"] = f"/workspace/{stage_name}"
                container = await asyncio.to_thread(
                    client.containers.create,
                    image=self.mcp_image,
                    command=["file"],
                    environment=env,
                    user=_SANDBOX_UID,
                    **host_config,
                )
                try:
                    archive = _tar_single_file(stage_name, data)
                    await asyncio.to_thread(container.put_archive, "/workspace", archive)
                    await asyncio.to_thread(container.start)
                except Exception:
                    await self._remove_quietly(container)
                    raise
            else:
                container = await asyncio.to_thread(
                    client.containers.run,
                    image=self.mcp_image,
                    command=["file"],
                    environment=env,
                    user=_SANDBOX_UID,
                    detach=True,
                    **host_config,
                )
            try:
                await self._assert_runsc(container)
                try:
                    exit_status = await asyncio.to_thread(container.wait, timeout=timeout_s)
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(container.kill)
                    return ToolCallResult(
                        ok=False,
                        stdout="",
                        stderr=f"timeout: {exc}",
                        exit_code=124,
                        duration_ms=int((time.monotonic() - start) * 1000),
                        metadata={"volume": volume, "op": op},
                    )
                duration_ms = int((time.monotonic() - start) * 1000)
                status_code = int(exit_status.get("StatusCode", 1))
                raw_out = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
                stdout = raw_out.decode("utf-8", errors="replace")
                raw_err = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
                stderr = raw_err.decode("utf-8", errors="replace")
            finally:
                await self._remove_quietly(container)
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
        async with _get_semaphore():
            client = self._client()
            host_config = self._base_host_config()
            # M19: code_exec needs no network — isolate completely.
            host_config["network_mode"] = "none"
            env = {"SMAP_AGENT_ID": str(agent_id)}
            command = ["python", "-c", source]
            if stdin:
                env["SMAP_CODE_EXEC_STDIN"] = stdin
                command = ["python", "-c", _STDIN_SHIM, source]
            start = time.monotonic()
            container = await asyncio.to_thread(
                client.containers.run,
                image=self.code_exec_image,
                command=command,
                environment=env,
                user=_SANDBOX_UID,
                tmpfs=_sandbox_tmpfs(),
                detach=True,
                **host_config,
            )
            try:
                await self._assert_runsc(container)
                try:
                    exit_status = await asyncio.to_thread(container.wait, timeout=min(timeout_s, 30.0))
                except Exception as exc:
                    with contextlib.suppress(Exception):
                        await asyncio.to_thread(container.kill)
                    return ToolCallResult(
                        ok=False,
                        stdout="",
                        stderr=f"timeout: {exc}",
                        exit_code=124,
                        duration_ms=int((time.monotonic() - start) * 1000),
                    )
                duration_ms = int((time.monotonic() - start) * 1000)
                status_code = int(exit_status.get("StatusCode", 1))
                raw_out = await asyncio.to_thread(container.logs, stdout=True, stderr=False)
                stdout = raw_out.decode("utf-8", errors="replace")
                raw_err = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
                stderr = raw_err.decode("utf-8", errors="replace")
            finally:
                await self._remove_quietly(container)
        return ToolCallResult(
            ok=status_code == 0,
            stdout=stdout,
            stderr=stderr,
            exit_code=status_code,
            duration_ms=duration_ms,
        )


    async def cleanup_orphan_containers(self, *, max_age_s: float = 600) -> int:
        """Remove sandbox containers older than *max_age_s* seconds.

        Called periodically from the worker to catch containers whose parent
        process crashed before the finally-block removal ran.
        """
        client = self._client()
        removed = 0
        try:
            containers = await asyncio.to_thread(
                client.containers.list,
                all=True,
                filters={"label": _CONTAINER_LABEL},
            )
        except Exception:
            _log.warning("orphan cleanup: failed to list containers", exc_info=True)
            return 0
        cutoff = time.time() - max_age_s
        for c in containers:
            try:
                created = c.attrs.get("Created", "")
                if not created:
                    continue
                ts = datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if ts < cutoff:
                    await asyncio.to_thread(c.remove, force=True)
                    removed += 1
                    _log.info("orphan cleanup: removed container %s", c.short_id)
            except Exception:
                _log.debug("orphan cleanup: skip container %s", c.short_id, exc_info=True)
        return removed


def docker_runsc_sandbox_from_settings() -> DockerRunscSandbox:
    """Build the production sandbox runner from settings (composition root, K.5).

    Image references come from the ``SANDBOX_*`` pins (digest-pinned in prod);
    the egress proxy URL + shared secret are reused from the egress settings so
    a URL-source MCP server's outbound traffic is pre-signed for its project.
    """
    from app.config.settings import get_settings

    cfg = get_settings()
    try:
        secret = bytes.fromhex(cfg.egress.shared_secret) if cfg.egress.shared_secret else b""
    except ValueError:
        secret = b""
    return DockerRunscSandbox(
        mcp_image=cfg.sandbox.mcp_image,
        code_exec_image=cfg.sandbox.code_exec_image,
        egress_proxy_url=cfg.egress.proxy_url,
        egress_shared_secret=secret,
    )


__all__ = ["DockerRunscSandbox", "docker_runsc_sandbox_from_settings"]
