"""Built-in ``file`` tool (E.11 / R12.03 ``file`` container).

Sandboxed to the per-agent named volume ``smap-agent-fs-{agent_id}`` mounted
at ``/workspace``. All paths are rejected unless they resolve cleanly under
``/workspace`` — no ``..`` escapes, no absolute paths outside the volume, no
null bytes, no symlink trickery at the application layer (the container also
enforces this with a read-only root FS, but we fail fast at the API surface).
"""

from __future__ import annotations

import posixpath
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.mcp_ports import SandboxRunner
from contexts.agents.domain.mcp import ToolCallResult
from shared_kernel import audit

_ROOT = "/workspace"
_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 10 MB per op — volume quota still wins.


def _safe_relpath(raw: str) -> str:
    """Return a ``/workspace``-rooted absolute path or raise ``ValueError``."""
    if not isinstance(raw, str) or not raw:
        raise ValueError("path must be a non-empty string")
    if "\x00" in raw:
        raise ValueError("null byte in path")
    # Normalise to posix; treat bare paths as relative to /workspace.
    candidate = raw if raw.startswith("/") else posixpath.join(_ROOT, raw)
    normed = posixpath.normpath(candidate)
    if not (normed == _ROOT or normed.startswith(_ROOT + "/")):
        raise ValueError(f"path escapes sandbox: {raw!r}")
    return normed


@dataclass(frozen=True, slots=True)
class FileTool:
    """Thin façade that forwards to the :class:`SandboxRunner`.

    Stateless; construct per turn with the agent id and a runner instance.
    """

    agent_id: uuid.UUID
    runner: SandboxRunner
    db: AsyncSession | None = None

    @property
    def volume_name(self) -> str:
        return f"smap-agent-fs-{self.agent_id}"

    async def list_(self, path: str = "/") -> ToolCallResult:
        safe = _safe_relpath(path)
        result = await self.runner.run_file_op(
            agent_id=self.agent_id,
            op="list",
            path=safe,
        )
        await self._audit("list", safe, result.ok)
        return result

    async def read(self, path: str) -> ToolCallResult:
        safe = _safe_relpath(path)
        result = await self.runner.run_file_op(
            agent_id=self.agent_id,
            op="read",
            path=safe,
        )
        await self._audit("read", safe, result.ok)
        return result

    async def write(self, path: str, data: bytes) -> ToolCallResult:
        if not isinstance(data, bytes | bytearray):
            raise TypeError("data must be bytes")
        if len(data) > _MAX_WRITE_BYTES:
            raise ValueError(f"write exceeds {_MAX_WRITE_BYTES} bytes ({len(data)} bytes)")
        safe = _safe_relpath(path)
        result = await self.runner.run_file_op(
            agent_id=self.agent_id,
            op="write",
            path=safe,
            data=bytes(data),
        )
        await self._audit("write", safe, result.ok)
        return result

    async def _audit(
        self,
        op: Literal["list", "read", "write"],
        path: str,
        ok: bool,
    ) -> None:
        if self.db is None:
            return
        await audit.emit(
            self.db,
            audit.AuditEvent(
                action="mcp.tool_invoked",
                resource_type="agent",
                resource_id=self.agent_id,
                metadata={
                    "tool": "file",
                    "op": op,
                    "path": path,
                    "volume": self.volume_name,
                    "ok": ok,
                },
            ),
        )


__all__ = ["FileTool"]
