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
    SandboxReconcileError,
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
# Where the per-agent volume is mounted in every sandbox container that gets one.
# This must equal `file_tool._ROOT` or a staged path resolves to nothing; nothing
# enforces that agreement (see 2026-07-17-sandbox-guest-container-tests). The
# kernel no longer takes part: it addresses only its session mount, so the pair
# that used to need coordinating here is now the pair below. Other `/workspace`
# literals in this module predate the constant and are not in this fix's scope.
_VOLUME_ROOT = "/workspace"
# Where a chatroom's own session volume is mounted. Deliberately NOT under
# `_VOLUME_ROOT`: the kernel runs arbitrary code, so a room boundary inside a
# shared mount is unenforceable ([R12.03b]). A separate root also fails closed --
# an unmounted session is an absent directory, where a nested one would silently
# expose whatever the per-agent volume happens to hold at that path.
_SESSION_ROOT = "/session"
# Host/kernel contract version; must equal `kernel.PROTOCOL_VERSION` in the
# code-exec image. Neither direction of a mismatched deploy degrades safely, so
# the host refuses a stamp it does not recognise rather than reading the wrong
# paths (2026-07-19-session-dir-room-isolation Q-7).
_KERNEL_PROTOCOL_VERSION = 2
# Where per-room session state used to live on the per-agent volume, before
# [R12.03b] moved it to its own mount. Nothing writes here any more; the name
# survives only so the one-shot repair knows what to clear.
_LEGACY_SESSIONS_SUBDIR = "sessions"
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
    from shared_kernel.storage.sanitize import safe_input_name

    return safe_input_name(filename)


def _safe_staged_name(filename: str, *, preserve_tree: bool) -> str:
    """The staged name for *filename*: a bare basename, or a validated subtree."""
    if not preserve_tree:
        return _safe_input_name(filename)
    from shared_kernel.storage.sanitize import safe_workspace_relpath

    return safe_workspace_relpath(filename)


def _disambiguate(name: str, taken: set[str]) -> str:
    """Suffix the basename until *name* is unused, leaving its directory alone."""
    import posixpath

    if name not in taken:
        return name
    parent, _, base = name.rpartition("/")
    stem, dot, ext = base.rpartition(".")
    n = 1
    while True:
        candidate = f"{stem}-{n}{dot}{ext}" if dot else f"{base}-{n}"
        full = posixpath.join(parent, candidate) if parent else candidate
        if full not in taken:
            return full
        n += 1


def _staged_members(
    rel_dir: str,
    files: Sequence[StagedFile],
    *,
    preserve_tree: bool = False,
) -> list[tuple[StagedFile, str]]:
    """Pair each stageable file with the tar member name it will be written to.

    Split out from the tar build so the manifest-cache hit can report paths
    without materialising (and discarding) an archive of every file's bytes.

    A file whose name cannot be validated is **skipped and logged**, not raised:
    callers swallow staging faults to keep the turn alive, so raising would turn
    one unstageable row into a silent, permanent loss of every workspace file the
    agent has. One bad row should cost one file.
    """
    import posixpath

    rel_dir = rel_dir.strip("/")
    named: list[tuple[StagedFile, str]] = []
    for f in files:
        try:
            named.append((f, _safe_staged_name(f.filename, preserve_tree=preserve_tree)))
        except ValueError:
            _log.warning(
                "skipping unstageable path %r for %s: fails workspace path validation",
                f.filename,
                rel_dir,
            )

    # A name some other file needs as a directory is not available as a file name:
    # `reports` and `reports/q1.csv` are both legal uploads, and emitting a file
    # member and a dir member for the same path makes extraction fail.
    reserved: set[str] = set()
    for _f, name in named:
        acc = ""
        for part in name.split("/")[:-1]:
            acc = posixpath.join(acc, part) if acc else part
            reserved.add(acc)

    out: list[tuple[StagedFile, str]] = []
    seen: set[str] = set()
    for f, raw_name in named:
        name = _disambiguate(raw_name, seen | reserved)
        seen.add(name)
        out.append((f, posixpath.join(rel_dir, name)))
    return out


def _tar_staged_inputs(
    rel_dir: str,
    files: Sequence[StagedFile],
    *,
    preserve_tree: bool = False,
) -> tuple[bytes, list[str]]:
    """Tar stream that creates *rel_dir* (owned by the sandbox uid) and drops the
    files into it.

    Returns (archive, staged_paths), where each staged path is the tar member
    name — i.e. relative to the extraction root, which every caller mounts at
    ``/workspace``. Callers turn that into what their consumer needs; do not
    assume a prefix here, because the two callers do not share one.

    With *preserve_tree*, a file's name may carry directories and they are
    recreated under *rel_dir* (workspace files, whose layout is the designer's).
    Without it the name is flattened to a basename (chat attachments, which have
    no meaningful tree and whose names are far less trusted).

    *rel_dir* must be a non-empty relative path; an empty one would emit a member
    that chowns the extraction root itself.
    """
    import io
    import posixpath
    import tarfile

    rel_dir = rel_dir.strip("/")
    if not rel_dir:
        raise ValueError("rel_dir must be a non-empty relative path")
    buf = io.BytesIO()
    staged: list[str] = []
    made_dirs: set[str] = set()

    def _mkdirs(tar: tarfile.TarFile, path: str) -> None:
        # Every intermediate component gets its own member, not just the leaf's
        # parent. On the persistent volume the agent's own code_exec could have
        # left a symlink where a directory belongs; a DIRTYPE member at each level
        # makes the extractor replace it. Do not "optimise" this to leaf-only.
        acc = ""
        for part in path.split("/"):
            acc = posixpath.join(acc, part) if acc else part
            if acc in made_dirs:
                continue
            made_dirs.add(acc)
            d = tarfile.TarInfo(name=acc + "/")
            d.type = tarfile.DIRTYPE
            d.mode = 0o700
            d.uid = d.gid = _SANDBOX_UID
            tar.addfile(d)

    with tarfile.open(fileobj=buf, mode="w") as tar:
        _mkdirs(tar, rel_dir)
        for f, member in _staged_members(rel_dir, files, preserve_tree=preserve_tree):
            parent = posixpath.dirname(member)
            if parent:
                _mkdirs(tar, parent)
            info = tarfile.TarInfo(name=member)
            info.size = len(f.data)
            info.mode = 0o600
            info.uid = info.gid = _SANDBOX_UID
            tar.addfile(info, io.BytesIO(f.data))
            staged.append(member)
    return buf.getvalue(), staged


def _agent_volume_name(agent_id: uuid.UUID) -> str:
    """The per-agent volume: `file`-tool state, `agent-files/`, `skills/` ([R12.03])."""
    return f"smap-agent-fs-{agent_id}"


def _session_volume_name(agent_id: uuid.UUID, chatroom_id: uuid.UUID) -> str:
    """The per-(agent, chatroom) volume: staged `inputs/`, generated `outputs/`.

    Separate from the agent volume so that one chatroom's attachments and
    artifacts are not reachable from another ([R12.03b]). Note `agent_fs_gc`
    parses both names — a change here needs a change there, or the volumes leak.
    """
    return f"smap-agent-session-{agent_id}-{chatroom_id}"


def _session_abspath(member: str) -> str:
    """Absolute form of a session-relative tar member, for the model's note."""
    import posixpath

    return posixpath.join(_SESSION_ROOT, member)


def _workspace_abspath(member: str) -> str:
    """Absolute form of a tar member name, for the note the model reads.

    Relative is ambiguous across the tools that share this volume: the kernel runs
    with its cwd at the session dir while the ``file`` tool roots relative paths at
    ``/workspace``, so the same string reaches two different files. Absolute is the
    one form both resolve identically — and the form the skills block already uses.
    """
    import posixpath

    return posixpath.join(_VOLUME_ROOT, member)


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

# Sandbox runtime readiness gate (R12.05). The healthy/skipped verdict from the
# supervisor probe is cached process-wide for this TTL so a burst of spawns
# shares one round-trip; the frozen runner is rebuilt every turn, so this lives
# at module scope alongside the other process caches (_KERNELS, docker client).
_SUPERVISOR_TIMEOUT_S = 3.0
_RUNTIME_READY_TTL_S = 10.0
_runtime_ready_at: float | None = None


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

# Volume reconciliation (2026-07-16-agent-workspace-volume-reconcile). The
# tree-preserving stagers used to `put_archive` the survivors over the existing
# tree and record a manifest sha in a per-process dict — an overlay that could
# add but never remove, so a file deleted from the source of truth kept its bytes
# on the volume, and a cache that returned paths without touching Docker, so it
# could not notice a volume removed out of band. Both are gone. Staging now
# reconciles **in place**: `put_archive` overlays the staged files onto the live
# subtree (add/overwrite), then a real container prunes whatever the staged set no
# longer contains, so the subtree equals the set every turn ([R12.03a]).
#
# Overlay-then-prune (not stage-a-copy-then-swap) is what keeps this cheap and
# safe: the staged files go straight onto the volume with no second copy, so peak
# volume and worker memory stay ~1x (a full staging tar would be ~2x of a 100 MB
# volume and 128 MiB of worker heap), and the subtree is never emptied, so a
# reconcile that fails partway degrades to "some stale files linger", never to
# "the agent's files vanished". The desired-path set is carried in a tiny manifest
# file put beside the archive; the prune reads it and removes the rest.
_RECONCILE_MANIFEST_PREFIX = ".smap-reconcile-"
_RECONCILE_TIMEOUT_S = 30.0

# Executed as `python -c _RECONCILE` inside the sandbox container. Reads its
# target from the environment so the same source can be exercised directly, on a
# real filesystem, by test_workspace_volume_reconcile.py without a Docker daemon.
#
# The one real danger here is deleting data outside the reconciled subtree — the
# agent's own `file`-tool state and per-room dirs share the volume and have no
# other copy (§9). The prune defends on three fronts, all covered by AC-6: it
# refuses to walk a symlinked subtree root (os.walk would follow it and prune the
# target); os.walk does not descend into a symlinked subdir; and os.unlink removes
# a symlink entry itself, never its target. So nothing outside the subtree is ever
# reached, whatever the agent's own code_exec left on the volume.
_RECONCILE = r"""
import os, sys, warnings

warnings.filterwarnings("ignore")  # keep stderr clean; failures still print

root = os.environ["SMAP_RECONCILE_ROOT"]
subdir = os.environ["SMAP_RECONCILE_SUBDIR"].strip("/")
manifest = os.environ["SMAP_RECONCILE_MANIFEST"]
if not subdir:
    sys.exit("reconcile: empty subdir")

target = os.path.join(root, subdir)

try:
    with open(manifest, encoding="utf-8") as fh:
        desired = {line.rstrip("\n") for line in fh if line.strip()}

    # The staged files were already extracted into `target`, over any existing tree.
    # If `target` is a symlink, that overlay wrote through it to elsewhere on the
    # volume; never walk a symlinked top -- os.walk follows a symlinked root and would
    # prune the target's contents. Replace it with a real empty dir. The misplaced
    # files converge next turn; only an agent that planted the symlink is affected.
    if os.path.islink(target):
        os.unlink(target)
    os.makedirs(target, exist_ok=True)

    # Prune: remove every entry under the subtree whose path is not in the staged set.
    # os.walk does not descend into a symlinked subdir (followlinks defaults False) and
    # os.unlink never follows a symlink to its target, so nothing outside is touched.
    for dirpath, dirnames, filenames in os.walk(target, topdown=False):
        for nm in filenames:
            full = os.path.join(dirpath, nm)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            if rel not in desired:
                try:
                    os.unlink(full)
                except OSError:
                    pass
        for nm in dirnames:
            full = os.path.join(dirpath, nm)
            if os.path.islink(full):
                try:
                    os.unlink(full)  # a symlinked "dir" is never a desired file
                except OSError:
                    pass
            else:
                try:
                    os.rmdir(full)  # only succeeds once empty (no desired file remains)
                except OSError:
                    pass
finally:
    try:
        os.unlink(manifest)
    except OSError:
        pass
"""


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
    supervisor_url: str = ""

    def _client(self) -> Any:
        """Lazy-import the docker SDK so unit tests don't need it installed."""
        return _docker_client()

    async def _ensure_runtime_ready(self) -> None:
        """Sandbox runtime readiness gate (R12.05).

        Before spawning, ask the mcp-sandbox-supervisor's ``/healthz`` whether
        the gVisor ``runsc`` runtime is registered host-wide. A missing runtime
        then surfaces once, with the supervisor's actionable detail, instead of
        a cryptic Docker ``APIError`` on every spawn. The healthy/skipped verdict
        is cached for a short window so a burst of spawns shares one probe.

        Posture (deliberate): this gate is **not** the security boundary —
        :meth:`_assert_runsc` already fails closed per container. So it must
        never become a single point of failure: an *unreachable* supervisor
        fails **open** (warn + proceed); only an explicit ``503`` verdict (runsc
        not registered / daemon down) fails **closed**. Disabled when no
        ``supervisor_url`` is configured.
        """
        if not self.supervisor_url:
            return
        global _runtime_ready_at
        now = time.monotonic()
        if _runtime_ready_at is not None and now - _runtime_ready_at < _RUNTIME_READY_TTL_S:
            return
        healthy, detail = await self._probe_supervisor()
        if healthy is False:
            # Explicit unhealthy verdict — fail closed with the actionable
            # detail. Not cached, so the operator installing gVisor is picked
            # up on the very next spawn rather than after the TTL.
            raise SandboxRuntimeViolation(f"sandbox runtime not ready: {detail}")
        # healthy is True (ok) or None (unreachable → fail open). Either way we
        # proceed; cache the decision so a window of spawns shares one probe.
        _runtime_ready_at = now

    async def _probe_supervisor(self) -> tuple[bool | None, str]:
        """GET the supervisor health endpoint. Returns ``(verdict, detail)`` where
        verdict is ``True`` (healthy), ``False`` (explicit unhealthy → fail closed),
        or ``None`` (unreachable / unexpected → fail open)."""
        import httpx

        url = self.supervisor_url.rstrip("/") + "/healthz"
        try:
            async with httpx.AsyncClient(timeout=_SUPERVISOR_TIMEOUT_S) as client:
                resp = await client.get(url)
        except Exception as exc:
            _log.warning("sandbox readiness gate: supervisor %s unreachable (%s); failing open", url, exc)
            return None, str(exc)
        detail = ""
        if resp.status_code != 200:
            try:
                payload = resp.json()
                detail = str(payload.get("detail") or "") if isinstance(payload, dict) else ""
            except ValueError:
                detail = ""
            if not detail:
                detail = (resp.text or "").strip()
        return self._classify_supervisor_status(resp.status_code, detail)

    @staticmethod
    def _classify_supervisor_status(status_code: int, detail: str) -> tuple[bool | None, str]:
        """Map a supervisor HTTP status to a gate verdict (pure; unit-tested).

        Only the supervisor's defined ``503`` blocks; any other non-200 (e.g. a
        misrouted URL) fails open so a misconfiguration cannot wedge sandboxing.
        """
        if status_code == 200:
            return True, "ok"
        if status_code == 503:
            return False, detail or "gVisor runsc runtime not registered on the Docker host"
        _log.warning(
            "sandbox readiness gate: supervisor returned unexpected HTTP %s; failing open",
            status_code,
        )
        return None, f"unexpected supervisor status HTTP {status_code}"

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

    async def _create_verified(self, client: Any, **create_kwargs: Any) -> Any:
        """Create the sandbox container, confirm it is on gVisor, and return it
        still in the CREATED state for the caller to ``start``.

        Asserting the runtime BEFORE the workload starts closes the window where
        untrusted code could execute on a non-runsc runtime. The daemon already
        rejects an *unknown* runtime at create time (so a missing gVisor never
        runs anything), but this is defense in depth against a silent runtime
        substitution and against the previous order, where some paths started
        the container and only then verified the runtime.
        """
        container = await asyncio.to_thread(client.containers.create, **create_kwargs)
        try:
            await self._assert_runsc(container)
        except Exception:
            await self._remove_quietly(container)
            raise
        return container

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
        await self._ensure_runtime_ready()
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
            container = await self._create_verified(
                client,
                image=self.mcp_image,
                command=["probe"],
                environment=env,
                user=_SANDBOX_UID,
                tmpfs=_sandbox_tmpfs(),
                **host_config,
            )
            try:
                await asyncio.to_thread(container.start)
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
        await self._ensure_runtime_ready()
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
            container = await self._create_verified(
                client,
                image=self.mcp_image,
                command=["invoke"],
                environment=env,
                user=_SANDBOX_UID,
                tmpfs=_sandbox_tmpfs(),
                **host_config,
            )
            try:
                await asyncio.to_thread(container.start)
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
        await self._ensure_runtime_ready()
        async with _get_semaphore():
            client = self._client()
            volume = _agent_volume_name(agent_id)
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
                container = await self._create_verified(
                    client,
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
                container = await self._create_verified(
                    client,
                    image=self.mcp_image,
                    command=["file"],
                    environment=env,
                    user=_SANDBOX_UID,
                    **host_config,
                )
                try:
                    await asyncio.to_thread(container.start)
                except Exception:
                    await self._remove_quietly(container)
                    raise
            try:
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
        await self._ensure_runtime_ready()
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
            container = await self._create_verified(
                client,
                image=self.code_exec_image,
                command=command,
                environment=env,
                user=_SANDBOX_UID,
                tmpfs=_sandbox_tmpfs(),
                **host_config,
            )
            try:
                await asyncio.to_thread(container.start)
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
        """Blocking exec of the messenger into a live kernel (runs in a thread).

        The thread is bounded by the caller, not by a client read timeout: the
        Docker SDK's ``exec_run`` reads the exec output over a hijacked stream
        that ignores the client ``timeout`` (verified empirically — a bounded
        client did NOT interrupt a long exec). The real bound is the enclosing
        ``asyncio.wait_for`` firing at ``budget + 5s`` and ``_reset_kernel``
        force-removing the container, which unblocks this exec within ~0.2s.
        """
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
        protocol = reply.get("protocol")
        if protocol != _KERNEL_PROTOCOL_VERSION:
            # A stale image on either side is otherwise silent: the host stages
            # inputs where the kernel does not look, or the kernel looks at a
            # mount the host did not bind, and both present to the user as "the
            # agent ignored my file". Refuse instead of answering from the wrong
            # paths (Q-7). Nothing in CI runs a container, so this assertion is
            # the only thing standing between a tag drift and silent data loss.
            _log.error(
                "code-exec kernel protocol mismatch: image reports %r, backend expects %r",
                protocol,
                _KERNEL_PROTOCOL_VERSION,
            )
            return ToolCallResult(
                ok=False,
                stdout="",
                stderr=(
                    "code_exec is unavailable: the sandbox image and the server disagree "
                    "on their interface. This is a deployment fault, not a problem with "
                    "your code; the administrator needs to align the smap/code-exec image "
                    "with the running server."
                ),
                exit_code=1,
                duration_ms=_ms_since(start),
                metadata={"session": session, "restarted": restarted, "protocol_mismatch": True},
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
        host_config = self._base_host_config()
        # No network, kernel label so the smap.sandbox orphan sweep never reaps a
        # live kernel. Two volumes: the agent's own state at /workspace, shared
        # across its rooms, and THIS room's session at /session. The split is the
        # room boundary -- this container runs arbitrary code, so no path check
        # inside a single shared mount could hold ([R12.03b]).
        #
        # The agent volume is READ-ONLY here and nowhere else. This kernel writes
        # nothing to it (kernel.py addresses only /session and /tmp), so the write
        # bind served agent-authored code alone -- and that is precisely what let
        # an agent copy room-scoped data onto agent-scoped storage and read it back
        # in another room. Writes to agent state go through the `file` tool, which
        # owns that region ([R12.03a]) and whose staged os.replace cannot plant the
        # symlink that path-based guards here would then have to survive.
        host_config["network_mode"] = "none"
        host_config["volumes"] = {
            _agent_volume_name(agent_id): {"bind": _VOLUME_ROOT, "mode": "ro"},
            _session_volume_name(agent_id, chatroom_id): {"bind": _SESSION_ROOT, "mode": "rw"},
        }
        host_config["labels"] = {
            _KERNEL_LABEL: "1",
            "smap.kernel.session": _session_key(agent_id, chatroom_id),
        }
        container = await self._create_verified(
            client,
            image=self.code_exec_image,
            command=["python", "/opt/kernel/kernel.py"],
            # No room id: the kernel no longer derives a path from one, so passing
            # it would only invite a second, unenforced notion of which room this is.
            environment={"SMAP_AGENT_ID": str(agent_id)},
            user=_SANDBOX_UID,
            tmpfs={"/tmp": f"size={_TMP_TMPFS_BYTES}"},  # noqa: S108 — in-container tmpfs
            name=name,
            **host_config,
        )
        try:
            await asyncio.to_thread(container.start)
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
        """Copy user-uploaded files into this chatroom's own session volume.

        Writes ``/session/inputs/{file}`` via a short-lived (no-network)
        container's ``put_archive`` — the same volume the live kernel binds at
        ``/session``, so ``code_exec`` can read them.

        Mounts only the session volume, never the per-agent one: an attachment
        belongs to the room it was uploaded in, and the room's members are not
        the agent's other rooms' members ([R12.03b]).

        Returns absolute paths (``/session/inputs/x``). They go into a note the
        model reads, and the kernel runs with its cwd at ``/session`` while the
        ``file`` tool roots relative paths at ``/workspace`` — so only an
        absolute path means the same file to both.
        """
        if not files:
            return []
        await self._ensure_runtime_ready()
        client = self._client()
        volume = _session_volume_name(agent_id, chatroom_id)
        archive, staged = _tar_staged_inputs("inputs", files)
        host_config = self._base_host_config()
        host_config["network_mode"] = "none"
        host_config["volumes"] = {volume: {"bind": _SESSION_ROOT, "mode": "rw"}}
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
                await asyncio.to_thread(container.put_archive, _SESSION_ROOT, archive)
            finally:
                await self._remove_quietly(container)
        return [_session_abspath(p) for p in staged]

    async def _stage_tree(
        self,
        *,
        agent_id: uuid.UUID,
        rel_dir: str,
        files: Sequence[StagedFile],
    ) -> list[str]:
        """Reconcile ``/workspace/{rel_dir}/`` on the agent's volume to equal *files*.

        Shared by the two tree-preserving stagers (agent workspace files and skill
        scripts), which differ only in which subtree they own. That subtree is a
        **projection** of a source of truth — the ``agent_workspace_files`` rows, or the
        bound-skill set — so staging reconciles rather than overlays ([R12.03a]): a file
        no longer in *files* is removed from the volume, not merely omitted from the note.

        Reconciles **in place**, not by clear-and-replace. ``put_archive`` overlays the
        staged files straight onto the live subtree (add/overwrite), then a real container
        runs ``_RECONCILE``, which prunes whatever the staged set no longer contains. A
        second copy is never staged, so peak volume and worker memory stay ~1x, and the
        subtree is never emptied — a reconcile that fails partway leaves stale files, never
        an empty tree. The desired-path set travels in a tiny manifest put beside the
        archive. A non-zero exit raises, so the caller degrades the turn.

        Not cached: the retired manifest cache returned paths without touching Docker, so
        it could neither prune a dropped file (FU-19) nor notice a volume removed out of
        band (FU-6). Every call reconciles. See
        docs/tasks/2026-07-16-agent-workspace-volume-reconcile/spec.md.

        Returns absolute paths — see ``stage_kernel_inputs`` for why relative would be
        ambiguous here.
        """
        if not files:
            return []

        await self._ensure_runtime_ready()
        client = self._client()
        volume = _agent_volume_name(agent_id)
        archive, raw_staged = _tar_staged_inputs(rel_dir=rel_dir, files=files, preserve_tree=True)
        # The prune needs the desired-path set. Carry it in a tiny manifest (KBs, not the
        # 128 MiB archive) put beside the files; a per-call uuid name keeps concurrent
        # stagers of the same volume from colliding on it.
        manifest_name = f"{_RECONCILE_MANIFEST_PREFIX}{uuid.uuid4().hex}.manifest"
        manifest_tar = _tar_single_file(manifest_name, ("\n".join(raw_staged) + "\n").encode())

        host_config = self._base_host_config()
        host_config["network_mode"] = "none"
        host_config["volumes"] = {volume: {"bind": _VOLUME_ROOT, "mode": "rw"}}
        async with _get_semaphore():
            container = await self._create_verified(
                client,
                image=self.code_exec_image,
                command=["python", "-c", _RECONCILE],
                environment={
                    "SMAP_RECONCILE_ROOT": _VOLUME_ROOT,
                    "SMAP_RECONCILE_SUBDIR": rel_dir.strip("/"),
                    "SMAP_RECONCILE_MANIFEST": f"{_VOLUME_ROOT}/{manifest_name}",
                },
                user=_SANDBOX_UID,
                tmpfs={"/tmp": f"size={_TMP_TMPFS_BYTES}"},  # noqa: S108 — in-container tmpfs
                **host_config,
            )
            try:
                # Both put_archives run before start; the volume is mounted at create, so
                # both survive it. The files overlay the live subtree (in place, no second
                # copy), and the manifest lands at the volume root for the prune to read.
                await asyncio.to_thread(container.put_archive, _VOLUME_ROOT, archive)
                await asyncio.to_thread(container.put_archive, _VOLUME_ROOT, manifest_tar)
                await asyncio.to_thread(container.start)
                await self._await_reconcile(container, rel_dir)
            finally:
                await self._remove_quietly(container)

        return [_workspace_abspath(p) for p in raw_staged]

    async def purge_legacy_session_dirs(self, *, agent_id: uuid.UUID) -> None:
        """Empty ``/workspace/sessions/`` on one agent's volume.

        One-shot repair for data written before session state moved to its own
        per-room volume ([R12.03b]). Nothing writes there any more, which is
        exactly why nothing will clean it up: the tree would otherwise stay
        readable from every room the agent serves, for as long as the agent
        lives. See `docs/tasks/2026-07-19-session-dir-room-isolation`.

        Deliberately reuses ``_RECONCILE`` with an **empty manifest** rather than
        introducing a second deletion path. "Make this subtree equal the empty
        set" is precisely a reconcile, and that script is already audited for the
        one danger here — escaping the subtree via a symlinked root, a symlinked
        subdir, or a ``..`` member (see its comment block, and AC-6 of
        `2026-07-16-agent-workspace-volume-reconcile`). A fresh `rm -rf` aimed at
        agent-authored data would have to re-earn that scrutiny.

        Idempotent: a volume with no ``sessions/`` reconciles an empty subtree to
        empty and exits 0. Raises ``SandboxReconcileError`` on timeout or
        non-zero exit, so a caller sweeping many volumes can count failures
        rather than assume success.
        """
        await self._ensure_runtime_ready()
        client = self._client()
        volume = _agent_volume_name(agent_id)
        manifest_name = f"{_RECONCILE_MANIFEST_PREFIX}{uuid.uuid4().hex}.manifest"
        # An empty manifest: nothing under `sessions/` is desired. The script
        # reads a blank line as no entry, so a bare newline is the empty set.
        manifest_tar = _tar_single_file(manifest_name, b"\n")

        host_config = self._base_host_config()
        host_config["network_mode"] = "none"
        host_config["volumes"] = {volume: {"bind": _VOLUME_ROOT, "mode": "rw"}}
        async with _get_semaphore():
            container = await self._create_verified(
                client,
                image=self.code_exec_image,
                command=["python", "-c", _RECONCILE],
                environment={
                    "SMAP_RECONCILE_ROOT": _VOLUME_ROOT,
                    "SMAP_RECONCILE_SUBDIR": _LEGACY_SESSIONS_SUBDIR,
                    "SMAP_RECONCILE_MANIFEST": f"{_VOLUME_ROOT}/{manifest_name}",
                },
                user=_SANDBOX_UID,
                tmpfs={"/tmp": f"size={_TMP_TMPFS_BYTES}"},  # noqa: S108 — in-container tmpfs
                **host_config,
            )
            try:
                await asyncio.to_thread(container.put_archive, _VOLUME_ROOT, manifest_tar)
                await asyncio.to_thread(container.start)
                await self._await_reconcile(container, _LEGACY_SESSIONS_SUBDIR)
            finally:
                await self._remove_quietly(container)

    async def _await_reconcile(self, container: Any, rel_dir: str) -> None:
        """Wait on the reconcile container; raise on timeout or non-zero exit.

        Both failure paths raise :class:`SandboxReconcileError` so the caller's
        best-effort swallow degrades the turn uniformly; the caller's ``finally``
        removes the container either way."""
        try:
            exit_status = await asyncio.to_thread(container.wait, timeout=_RECONCILE_TIMEOUT_S)
        except Exception as exc:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(container.kill)
            raise SandboxReconcileError(f"reconcile of {rel_dir!r} timed out: {exc}") from exc
        status_code = int(exit_status.get("StatusCode", 1))
        if status_code != 0:
            raw_err = await asyncio.to_thread(container.logs, stdout=False, stderr=True)
            stderr = raw_err.decode("utf-8", errors="replace").strip()
            raise SandboxReconcileError(f"reconcile of {rel_dir!r} failed (exit {status_code}): {stderr}")

    async def stage_agent_workspace_files(
        self,
        *,
        agent_id: uuid.UUID,
        files: Sequence[StagedFile],
        manifest_sha: str,
    ) -> list[str]:
        """Reconcile the agent's persisted files under ``/workspace/agent-files/``.

        The subtree is made equal to *files* on every call: a file dropped from the set
        is removed from the volume, not merely omitted from the note ([R12.03a]).

        ``manifest_sha`` is the caller's description of the intended set. It is accepted
        but no longer gates staging — the retired manifest cache could not observe the
        volume, so it pruned nothing (FU-19) and could assert a set the volume did not
        hold (FU-6). It stays in the signature as the natural key for a *verified* cache
        should FU-2 add one.

        Returns absolute paths (``/workspace/agent-files/x``) — see ``stage_kernel_inputs``
        for why relative would be ambiguous here.
        """
        return await self._stage_tree(agent_id=agent_id, rel_dir="agent-files", files=files)

    async def stage_skill_files(
        self,
        *,
        agent_id: uuid.UUID,
        files: Sequence[StagedFile],
        manifest_sha: str,
    ) -> list[str]:
        """Materialise bound skills' scripts under ``/workspace/skills/`` ([R31.22]).

        Each file's name must already be ``{skill_name}/{path_within_skill}``, so a
        script stored at ``scripts/fill.py`` lands at
        ``/workspace/skills/pdf-fill/scripts/fill.py``. Keeping the bundle's own layout
        under the skill root is what makes SKILL.md's relative references resolve: the
        body says "run scripts/fill.py", and it does, from the skill's directory.

        The skill name is safe as a directory component by construction —
        ``SKILL_NAME_RE`` is lowercase alphanumerics and hyphens — and the file path was
        validated at upload; ``_staged_members`` re-validates both anyway, since this
        function cannot see where its input came from.

        Skills and agent files reconcile disjoint subtrees (``skills/`` and
        ``agent-files/``) of one volume, so staging one never disturbs the other (AC-21):
        each ``_RECONCILE`` clears only its own subtree.

        **Reconciled, not additive.** A script for an unbound or quarantined skill is
        dropped from *files* upstream in ``_stage_skill_scripts``, and the reconcile then
        removes it from the volume — closing FU-38, the additive-overlay leak where an
        unbound skill's scripts stayed on disk and reachable from ``code_exec``. The
        readability gate in ``_stage_skill_scripts`` stays a write-time gate; what changed
        is that a file omitted from the staged set no longer lingers.

        ``manifest_sha`` is accepted but no longer gates staging — see
        ``stage_agent_workspace_files`` for why it is retained.

        Returns absolute paths — see ``stage_kernel_inputs`` for why relative would be
        ambiguous here.
        """
        return await self._stage_tree(agent_id=agent_id, rel_dir="skills", files=files)

    async def _remove_aged_containers(
        self, *, label: str, max_age_s: float, skip_ids: frozenset[str] = frozenset()
    ) -> int:
        """Remove labelled containers older than *max_age_s*, skipping tracked ids.

        Shared by the sandbox-orphan and kernel-orphan sweeps so the listing,
        timestamp parse, and force-remove stay in one place."""
        client = self._client()
        try:
            containers = await asyncio.to_thread(client.containers.list, all=True, filters={"label": label})
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
        return await self._remove_aged_containers(label=_KERNEL_LABEL, max_age_s=max_age_s, skip_ids=tracked)

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
        supervisor_url=cfg.sandbox.supervisor_url,
    )


__all__ = [
    "DockerRunscSandbox",
    "docker_runsc_sandbox_from_settings",
    "reap_idle_kernels",
    "shutdown_all_kernels",
]
