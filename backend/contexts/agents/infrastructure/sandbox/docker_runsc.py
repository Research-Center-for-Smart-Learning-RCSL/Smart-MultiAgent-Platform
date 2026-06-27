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

The ``code_exec`` tool has two paths: the default run-and-burn ``python -c``
above, and — when a call carries a ``chatroom_id`` — a persistent per-session
kernel (Code Interpreter). The kernel registry + its security trade-offs are
documented at the ``_KERNELS`` block below.
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
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Literal

from contexts.agents.domain.errors import (
    McpEgressDenied,
    McpTimeout,
    SandboxRuntimeViolation,
)
from contexts.agents.domain.mcp import McpTestResult, StagedFile, ToolCallResult

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


def _safe_input_name(filename: str) -> str:
    """Reduce a user filename to a flat, traversal-safe basename."""
    base = filename.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(c for c in base if c.isprintable() and c not in '"\\:*?<>|').strip()
    cleaned = cleaned.lstrip(".") or "file"
    return cleaned[:200]


def _tar_staged_inputs(rel_dir: str, files: Sequence[StagedFile]) -> tuple[bytes, list[str]]:
    """Tar stream that creates *rel_dir* (owned by the sandbox uid) and drops the
    files into it. Returns (archive, staged_relative_paths)."""
    import io
    import posixpath
    import tarfile

    rel_dir = rel_dir.strip("/")
    buf = io.BytesIO()
    staged: list[str] = []
    with tarfile.open(fileobj=buf, mode="w") as tar:
        acc = ""
        for part in rel_dir.split("/"):
            acc = posixpath.join(acc, part) if acc else part
            d = tarfile.TarInfo(name=acc + "/")
            d.type = tarfile.DIRTYPE
            d.mode = 0o700
            d.uid = d.gid = _SANDBOX_UID
            tar.addfile(d)
        seen: set[str] = set()
        for f in files:
            name = _safe_input_name(f.filename)
            # Disambiguate collisions after sanitising.
            if name in seen:
                stem, _, ext = name.rpartition(".")
                name = f"{stem or name}-{len(seen)}{('.' + ext) if stem else ''}"
            seen.add(name)
            info = tarfile.TarInfo(name=posixpath.join(rel_dir, name))
            info.size = len(f.data)
            info.mode = 0o600
            info.uid = info.gid = _SANDBOX_UID
            tar.addfile(info, io.BytesIO(f.data))
            staged.append(posixpath.join("inputs", name))
    return buf.getvalue(), staged


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


_docker_client_instance: Any | None = None


def _docker_client() -> Any:
    """Process-wide docker client (lazy-imported so unit tests don't need the SDK).

    Cached because the worker rebuilds the sandbox runner every turn and the
    reaper ticks every minute — recreating ``docker.from_env()`` each time is
    pure waste (mirrors the cached MinIO client)."""
    global _docker_client_instance
    if _docker_client_instance is None:
        import docker

        _docker_client_instance = docker.from_env()
    return _docker_client_instance


# --------------------------------------------------------------------------- #
# Live-kernel registry (Code-Interpreter session path).                        #
#                                                                              #
# The default ``code_exec`` path is run-and-burn (`python -c`, fresh container #
# per call). When a call carries a ``chatroom_id`` we instead keep ONE         #
# long-lived gVisor container per (agent, room) running ``kernel.py``, so the  #
# Python namespace persists across calls within a chat session. This           #
# consciously evolves the run-and-burn property to                             #
# *ephemeral-per-session-with-idle-reaping*: all other isolation is preserved  #
# (gVisor, network_mode=none, read-only root, non-root uid, cap_drop ALL,      #
# per-session container isolation, no secrets in env), and a kernel is removed #
# once idle past ``_KERNEL_IDLE_S``. The registry is module-global because the #
# ``DockerRunscSandbox`` instance is rebuilt every turn (frozen, stateless),   #
# so per-instance state would never be reused.                                 #
# --------------------------------------------------------------------------- #
_MAX_LIVE_KERNELS = 16
_KERNEL_IDLE_S = 900.0
_KERNEL_LABEL = "smap.kernel"
_KERNELS: dict[str, _KernelHandle] = {}
_kernels_guard = asyncio.Lock()

# Workspace-file manifest cache — keyed by agent_id (not per-session: persisted
# files are identical across all sessions for one agent). Avoids a container
# spawn on the hot path when files haven't changed.
_WORKSPACE_MANIFESTS: dict[uuid.UUID, str] = {}


@dataclass(slots=True)
class _KernelHandle:
    container_id: str
    last_used: float
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class _KernelGone(Exception):
    """The kernel container vanished between lookup and exec (crash/OOM/reap)."""


def _session_key(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"{agent_id}:{chatroom_id}"


def _kernel_container_name(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    return f"smap-kernel-{agent_id}-{chatroom_id}"


def _ms_since(start: float) -> int:
    return int((time.monotonic() - start) * 1000)


def _is_running(container: Any) -> bool:
    try:
        state = (container.attrs.get("State") or {}) if container.attrs else {}
        return bool(state.get("Running")) or container.status == "running"
    except Exception:
        return False


def _get_container_quietly(client: Any, ref: str) -> Any | None:
    """Fetch + refresh a container by id/name; ``None`` if it's gone."""
    try:
        container = client.containers.get(ref)
        container.reload()
        return container
    except Exception:
        return None


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
        return _docker_client()

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
        chatroom_id: uuid.UUID | None = None,
    ) -> ToolCallResult:
        # Session path: a chat turn reuses a persistent kernel so in-memory
        # state survives across calls. Headless turns (A2A, no room) keep the
        # original run-and-burn behaviour below.
        if chatroom_id is not None:
            return await self._run_code_exec_kernel(
                agent_id=agent_id,
                chatroom_id=chatroom_id,
                source=source,
                stdin=stdin,
                timeout_s=timeout_s,
            )
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

    # ----------------------------------------------------------------- #
    # Live-kernel session path                                          #
    # ----------------------------------------------------------------- #

    async def _run_code_exec_kernel(
        self,
        *,
        agent_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        source: str,
        stdin: str,
        timeout_s: float,
    ) -> ToolCallResult:
        client = self._client()
        session = _session_key(agent_id, chatroom_id)
        start = time.monotonic()
        budget = min(float(timeout_s), 30.0)
        restarted = False
        for attempt in (1, 2):
            handle, status = await self._get_or_create_kernel(
                client, agent_id=agent_id, chatroom_id=chatroom_id
            )
            # Only flag a restart when a kernel that previously held state had to
            # be recreated — never on the first call of a session ("created").
            if status == "recreated":
                restarted = True
            try:
                async with handle.lock, _get_semaphore():
                    exec_out = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._kernel_exec_call, client, handle.container_id, source, stdin, budget
                        ),
                        timeout=budget + 5.0,
                    )
                handle.last_used = time.time()
            except TimeoutError:
                await self._reset_kernel(client, agent_id=agent_id, chatroom_id=chatroom_id)
                return ToolCallResult(
                    ok=False,
                    stdout="",
                    stderr=f"timeout: kernel exec exceeded {budget:.0f}s",
                    exit_code=124,
                    duration_ms=_ms_since(start),
                    metadata={"session": session},
                )
            except _KernelGone:
                await self._discard_kernel_key(agent_id, chatroom_id)
                if attempt == 2:
                    return ToolCallResult(
                        ok=False,
                        stdout="",
                        stderr="kernel unavailable",
                        exit_code=1,
                        duration_ms=_ms_since(start),
                        metadata={"session": session},
                    )
                # The kernel we were using just died — its state is gone.
                restarted = True
                continue
            return self._reply_to_result(exec_out, restarted=restarted, start=start, session=session)
        # Loop always returns above; satisfy the type checker.
        raise AssertionError("unreachable")

    def _kernel_exec_call(
        self, client: Any, container_id: str, source: str, stdin: str, timeout_s: float
    ) -> tuple[int, bytes, bytes]:
        """Blocking exec of the messenger into a live kernel (runs in a thread)."""
        container = _get_container_quietly(client, container_id)
        if container is None or not _is_running(container):
            raise _KernelGone(container_id)
        env = {
            "SMAP_KERNEL_CODE": source,
            "SMAP_KERNEL_STDIN": stdin,
            "SMAP_KERNEL_TIMEOUT": str(int(timeout_s)),
        }
        try:
            result = container.exec_run(
                ["python", "/opt/kernel/client.py"],
                environment=env,
                user=_SANDBOX_UID,
                demux=True,
            )
        except Exception as exc:  # NotFound/APIError → treat as a dead kernel
            raise _KernelGone(container_id) from exc
        stdout_b, stderr_b = result.output if result.output else (b"", b"")
        return int(result.exit_code or 0), stdout_b or b"", stderr_b or b""

    def _reply_to_result(
        self, exec_out: tuple[int, bytes, bytes], *, restarted: bool, start: float, session: str
    ) -> ToolCallResult:
        exit_code, stdout_b, stderr_b = exec_out
        raw = stdout_b.decode("utf-8", errors="replace")
        err = stderr_b.decode("utf-8", errors="replace")
        try:
            reply = json.loads(raw)
        except ValueError:
            return ToolCallResult(
                ok=False,
                stdout="",
                stderr=f"kernel returned non-JSON: {raw[:512]} {err[:256]}".strip(),
                exit_code=exit_code or 1,
                duration_ms=_ms_since(start),
                metadata={"session": session, "restarted": restarted},
            )
        ok = bool(reply.get("ok"))
        # The restart signal rides in metadata (not concatenated into stdout) so
        # the kernel's own output stays clean; the tool layer surfaces it.
        return ToolCallResult(
            ok=ok,
            stdout=str(reply.get("stdout", "")),
            stderr=str(reply.get("stderr", "")),
            exit_code=0 if ok else 1,
            duration_ms=_ms_since(start),
            metadata={
                "session": session,
                "restarted": restarted,
                "artifacts": list(reply.get("artifacts") or []),
            },
        )

    async def _get_or_create_kernel(
        self, client: Any, *, agent_id: uuid.UUID, chatroom_id: uuid.UUID
    ) -> tuple[_KernelHandle, str]:
        """Return (handle, status) — status is "reused", "created" (first kernel
        for this session), or "recreated" (a prior kernel had died)."""
        key = _session_key(agent_id, chatroom_id)
        name = _kernel_container_name(agent_id, chatroom_id)
        async with _kernels_guard:
            had_dead_kernel = False
            handle = _KERNELS.get(key)
            if handle is not None:
                container = await asyncio.to_thread(_get_container_quietly, client, handle.container_id)
                if container is not None and _is_running(container):
                    handle.last_used = time.time()
                    return handle, "reused"
                _KERNELS.pop(key, None)
                had_dead_kernel = True  # a tracked kernel that held state has gone
            # Adopt a kernel another worker/process may already be running.
            existing = await asyncio.to_thread(_get_container_quietly, client, name)
            if existing is not None:
                if _is_running(existing):
                    handle = _KernelHandle(container_id=existing.id, last_used=time.time())
                    _KERNELS[key] = handle
                    return handle, "reused"
                await self._remove_quietly(existing)
                had_dead_kernel = True
            await self._evict_if_full(client)
            container = await self._create_kernel(
                client, agent_id=agent_id, chatroom_id=chatroom_id, name=name
            )
            handle = _KernelHandle(container_id=container.id, last_used=time.time())
            _KERNELS[key] = handle
            return handle, ("recreated" if had_dead_kernel else "created")

    async def _create_kernel(
        self, client: Any, *, agent_id: uuid.UUID, chatroom_id: uuid.UUID, name: str
    ) -> Any:
        volume = f"smap-agent-fs-{agent_id}"
        host_config = self._base_host_config()
        # No network, persistent per-agent volume at /workspace, kernel label so
        # the smap.sandbox orphan sweep never reaps a live kernel.
        host_config["network_mode"] = "none"
        host_config["volumes"] = {volume: {"bind": "/workspace", "mode": "rw"}}
        host_config["labels"] = {
            _KERNEL_LABEL: "1",
            "smap.kernel.session": _session_key(agent_id, chatroom_id),
        }
        container = await asyncio.to_thread(
            client.containers.run,
            image=self.code_exec_image,
            command=["python", "/opt/kernel/kernel.py"],
            environment={"SMAP_AGENT_ID": str(agent_id), "SMAP_KERNEL_ROOM": str(chatroom_id)},
            user=_SANDBOX_UID,
            tmpfs={"/tmp": f"size={_TMP_TMPFS_BYTES}"},  # noqa: S108 — in-container tmpfs
            detach=True,
            name=name,
            **host_config,
        )
        try:
            await self._assert_runsc(container)
        except Exception:
            await self._remove_quietly(container)
            raise
        return container

    async def _evict_if_full(self, client: Any) -> None:
        """Evict the least-recently-used kernel when at capacity (holds guard)."""
        if len(_KERNELS) < _MAX_LIVE_KERNELS:
            return
        victim_key = min(_KERNELS, key=lambda k: _KERNELS[k].last_used)
        handle = _KERNELS.pop(victim_key, None)
        if handle is None:
            return
        container = await asyncio.to_thread(_get_container_quietly, client, handle.container_id)
        if container is not None:
            await self._remove_quietly(container)

    async def _discard_kernel_key(self, agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> None:
        async with _kernels_guard:
            _KERNELS.pop(_session_key(agent_id, chatroom_id), None)

    async def reset_kernel(self, *, agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> None:
        """Tear down a session's kernel (next call starts fresh, state lost)."""
        await self._reset_kernel(self._client(), agent_id=agent_id, chatroom_id=chatroom_id)

    async def _reset_kernel(self, client: Any, *, agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> None:
        async with _kernels_guard:
            handle = _KERNELS.pop(_session_key(agent_id, chatroom_id), None)
        ref = handle.container_id if handle is not None else _kernel_container_name(agent_id, chatroom_id)
        container = await asyncio.to_thread(_get_container_quietly, client, ref)
        if container is not None:
            await self._remove_quietly(container)

    async def stage_kernel_inputs(
        self,
        *,
        agent_id: uuid.UUID,
        chatroom_id: uuid.UUID,
        files: Sequence[StagedFile],
    ) -> list[str]:
        """Copy user-uploaded files into the session's kernel inputs dir.

        Writes ``/workspace/sessions/{room}/inputs/{file}`` on the per-agent
        volume via a short-lived (no-network) container's ``put_archive`` — the
        same volume the live kernel mounts, so ``code_exec`` can read them.
        Returns the workspace-relative paths actually staged (e.g. ``inputs/x``).
        """
        if not files:
            return []
        client = self._client()
        volume = f"smap-agent-fs-{agent_id}"
        rel_dir = f"sessions/{chatroom_id}/inputs"
        archive, staged = _tar_staged_inputs(rel_dir, files)
        host_config = self._base_host_config()
        host_config["network_mode"] = "none"
        host_config["volumes"] = {volume: {"bind": "/workspace", "mode": "rw"}}
        async with _get_semaphore():
            container = await asyncio.to_thread(
                client.containers.create,
                image=self.code_exec_image,
                command=["true"],
                user=_SANDBOX_UID,
                tmpfs={"/tmp": f"size={_TMP_TMPFS_BYTES}"},  # noqa: S108 — in-container tmpfs
                **host_config,
            )
            try:
                await self._assert_runsc(container)
                # put_archive extracts into the mounted volume; no need to run.
                await asyncio.to_thread(container.put_archive, "/workspace", archive)
            finally:
                await self._remove_quietly(container)
        return staged

    async def stage_agent_workspace_files(
        self,
        *,
        agent_id: uuid.UUID,
        files: Sequence[StagedFile],
        manifest_sha: str,
    ) -> list[str]:
        """Materialise the agent's persisted files under ``/workspace/agent-files/``.

        Idempotent: if the in-memory manifest cache shows the volume already
        has this ``manifest_sha``, returns immediately (no container spawn).
        After a successful write the cache is updated.
        """
        if not files:
            return []

        cached = _WORKSPACE_MANIFESTS.get(agent_id)
        if cached == manifest_sha:
            return [f"agent-files/{_safe_input_name(f.filename)}" for f in files]

        client = self._client()
        volume = f"smap-agent-fs-{agent_id}"
        rel_dir = "agent-files"
        archive, _raw_staged = _tar_staged_inputs(rel_dir, files)

        host_config = self._base_host_config()
        host_config["network_mode"] = "none"
        host_config["volumes"] = {volume: {"bind": "/workspace", "mode": "rw"}}
        async with _get_semaphore():
            container = await asyncio.to_thread(
                client.containers.create,
                image=self.code_exec_image,
                command=["true"],
                user=_SANDBOX_UID,
                tmpfs={"/tmp": f"size={_TMP_TMPFS_BYTES}"},  # noqa: S108 — in-container tmpfs
                **host_config,
            )
            try:
                await self._assert_runsc(container)
                await asyncio.to_thread(container.put_archive, "/workspace", archive)
            finally:
                await self._remove_quietly(container)

        _WORKSPACE_MANIFESTS[agent_id] = manifest_sha
        # _tar_staged_inputs hardcodes "inputs/" prefix in its returned paths;
        # rebuild with the correct "agent-files/" prefix.
        staged = [f"agent-files/{_safe_input_name(f.filename)}" for f in files]
        return staged

    async def _remove_aged_containers(
        self, *, label: str, max_age_s: float, skip_ids: frozenset[str] = frozenset()
    ) -> int:
        """Remove labelled containers older than *max_age_s*, skipping tracked ids.

        Shared by the sandbox-orphan and kernel-orphan sweeps so the listing,
        timestamp parse, and force-remove stay in one place."""
        client = self._client()
        try:
            containers = await asyncio.to_thread(
                client.containers.list, all=True, filters={"label": label}
            )
        except Exception:
            _log.warning("orphan cleanup: failed to list containers (label=%s)", label, exc_info=True)
            return 0
        cutoff = time.time() - max_age_s
        removed = 0
        for c in containers:
            try:
                if c.id in skip_ids:
                    continue
                created = c.attrs.get("Created", "")
                if not created:
                    continue
                ts = datetime.datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if ts < cutoff:
                    await asyncio.to_thread(c.remove, force=True)
                    removed += 1
                    _log.info("orphan cleanup: removed container %s (label=%s)", c.short_id, label)
            except Exception:
                _log.debug("orphan cleanup: skip container %s", c.short_id, exc_info=True)
        return removed

    async def cleanup_orphan_kernels(self, *, max_age_s: float = 3600) -> int:
        """Backstop sweep: remove kernel containers older than *max_age_s* that
        are no longer tracked (parent process crashed before idle-reaping)."""
        tracked = frozenset(h.container_id for h in _KERNELS.values())
        return await self._remove_aged_containers(
            label=_KERNEL_LABEL, max_age_s=max_age_s, skip_ids=tracked
        )

    async def cleanup_orphan_containers(self, *, max_age_s: float = 600) -> int:
        """Remove ephemeral sandbox containers whose parent process crashed
        before the finally-block removal ran."""
        return await self._remove_aged_containers(label=_CONTAINER_LABEL, max_age_s=max_age_s)


async def _reap_idle_kernels_once(idle_s: float) -> int:
    """Remove kernels idle past *idle_s*. Returns the count removed."""
    now = time.time()
    async with _kernels_guard:
        stale = [(k, h) for k, h in _KERNELS.items() if now - h.last_used > idle_s]
        for k, _ in stale:
            _KERNELS.pop(k, None)
    if not stale:
        return 0
    client = _docker_client()
    for _, handle in stale:
        container = await asyncio.to_thread(_get_container_quietly, client, handle.container_id)
        if container is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.remove, force=True)
    _log.info("kernel reaper: removed %d idle kernel(s)", len(stale))
    return len(stale)


async def reap_idle_kernels(*, interval_s: float = 60.0, idle_s: float = _KERNEL_IDLE_S) -> None:
    """Long-running reaper loop — started by the worker, cancelled on shutdown."""
    while True:
        await asyncio.sleep(interval_s)
        with contextlib.suppress(Exception):
            await _reap_idle_kernels_once(idle_s)


async def shutdown_all_kernels() -> None:
    """Remove every live kernel container (worker shutdown)."""
    async with _kernels_guard:
        handles = list(_KERNELS.values())
        _KERNELS.clear()
    if not handles:
        return
    client = _docker_client()
    for handle in handles:
        container = await asyncio.to_thread(_get_container_quietly, client, handle.container_id)
        if container is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.remove, force=True)


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


__all__ = [
    "DockerRunscSandbox",
    "docker_runsc_sandbox_from_settings",
    "reap_idle_kernels",
    "shutdown_all_kernels",
]
