"""Built-in ``code_exec`` tool (E.11 / R12.05).

30-second wall-clock cap; stdout / stderr / exit-code captured from the
curated ``python:3.12-slim`` gVisor container. The cap is enforced by the
SandboxRunner; this façade validates the caller's requested timeout.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.mcp_ports import SandboxRunner
from contexts.agents.domain.mcp import ToolCallResult
from shared_kernel import audit

_MAX_WALL_S = 30.0


@dataclass(frozen=True, slots=True)
class CodeExecTool:
    agent_id: uuid.UUID
    runner: SandboxRunner
    db: AsyncSession | None = None
    # When set, code runs in the room's persistent kernel so state (loaded
    # dataframes, variables) survives across calls within the chat session.
    chatroom_id: uuid.UUID | None = None

    async def run(
        self,
        source: str,
        *,
        stdin: str = "",
        timeout_s: float | None = None,
    ) -> ToolCallResult:
        if not isinstance(source, str) or not source:
            raise ValueError("source must be a non-empty string")
        # Clamp to the hard cap — accepting a caller-supplied shorter budget,
        # rejecting anything larger than R12.05's 30-second wall.
        budget = _MAX_WALL_S if timeout_s is None else min(float(timeout_s), _MAX_WALL_S)
        if budget <= 0:
            raise ValueError("timeout_s must be positive")
        result = await self.runner.run_code_exec(
            agent_id=self.agent_id,
            source=source,
            stdin=stdin,
            timeout_s=budget,
            chatroom_id=self.chatroom_id,
        )
        if self.db is not None:
            await audit.emit(
                self.db,
                audit.AuditEvent(
                    action="mcp.tool_invoked",
                    resource_type="agent",
                    resource_id=self.agent_id,
                    metadata={
                        "tool": "code_exec",
                        "exit_code": result.exit_code,
                        "duration_ms": result.duration_ms,
                        "ok": result.ok,
                    },
                ),
            )
        return result


__all__ = ["CodeExecTool"]
