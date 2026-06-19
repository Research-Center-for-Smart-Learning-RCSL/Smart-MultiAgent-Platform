"""A2A transport service (G.1 + G.2).

Wires the Redis Streams transport with scope enforcement, audit, and
broadcast fan-out. This is the single entry point for sending A2A
messages — callers never touch ``a2a_streams`` directly.

SoC:
- Scope check is delegated to ``agents.application.a2a_scope`` (E.4).
- Stream I/O is delegated to ``orchestration.infrastructure.a2a_streams``.
- Audit writes go through ``shared_kernel.audit``.
- Agent lookups use the ``AgentsFacade`` — no direct repo access.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.a2a_scope import (
    A2ACheckInput,
    evaluate,
)
from contexts.agents.interfaces.facade import Agent, AgentsFacade
from contexts.orchestration.domain.errors import A2ADeliveryFailed, A2AForbidden, A2ATimeout
from contexts.orchestration.domain.models import A2AEnvelope, A2AMessageType
from contexts.orchestration.infrastructure import a2a_rendezvous, a2a_streams
from contexts.orchestration.infrastructure.metrics import A2A_MESSAGES
from contexts.tenancy.interfaces.facade import TenancyFacade
from shared_kernel import audit

_BROADCAST_PREFIX = "broadcast:workspace"
_DEFAULT_CALL_TIMEOUT = 60.0


class A2AService:
    """Application-level A2A transport (G.1 + G.2)."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._agents = AgentsFacade(db)
        self._tenancy = TenancyFacade(db)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(
        self,
        *,
        envelope: A2AEnvelope,
        caller_attached_context_ids: frozenset[uuid.UUID] | None = None,
        callee_attached_context_ids: frozenset[uuid.UUID] | None = None,
        caller_invocation_context_id: uuid.UUID | None = None,
    ) -> str:
        """Send an A2A message with scope enforcement (G.2).

        Returns the Redis Stream entry ID.
        Raises A2AForbidden if scope check denies the call.
        """
        if envelope.to_agent.startswith(_BROADCAST_PREFIX):
            return await self._broadcast(
                envelope,
                caller_invocation_context_id=caller_invocation_context_id,
            )

        try:
            to_agent_id = uuid.UUID(envelope.to_agent)
        except ValueError as exc:
            raise A2AForbidden(f"to_agent is not a valid agent id: {envelope.to_agent!r}") from exc

        # When from_agent is None the call originates from the workflow engine
        # (a trusted internal caller) — skip caller validation and scope check.
        if envelope.from_agent is not None:
            caller = await self._require_agent(envelope.from_agent)
            callee = await self._require_agent(to_agent_id)

            await self._enforce_scope(
                caller=caller,
                callee=callee,
                callee_attached_context_ids=callee_attached_context_ids or frozenset(),
                caller_invocation_context_id=caller_invocation_context_id,
            )
        else:
            # Still validate the callee exists even for workflow calls.
            await self._require_agent(to_agent_id)

        await a2a_streams.ensure_consumer_group(to_agent_id)
        envelope_json = json.dumps(envelope.to_dict(), separators=(",", ":"))
        stream_id = await a2a_streams.xadd_envelope(to_agent_id, envelope_json)
        A2A_MESSAGES.labels(type=envelope.type.value).inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="a2a.sent",
                resource_type="a2a_message",
                resource_id=envelope.id,
                metadata={
                    "from_agent": str(envelope.from_agent) if envelope.from_agent else "workflow",
                    "to_agent": envelope.to_agent,
                    "type": envelope.type.value,
                    "correlation_id": str(envelope.correlation_id),
                    "stream_id": stream_id,
                },
            ),
        )
        return stream_id

    async def call(
        self,
        *,
        from_agent_id: uuid.UUID | None,
        to_agent_id: uuid.UUID,
        payload: dict[str, Any],
        workflow_run_id: uuid.UUID | None = None,
        timeout_seconds: float = _DEFAULT_CALL_TIMEOUT,
        caller_invocation_context_id: uuid.UUID | None = None,
        callee_attached_context_ids: frozenset[uuid.UUID] | None = None,
    ) -> dict[str, Any]:
        """Synchronous A2A call — blocks until reply or timeout (R9.15).

        Returns the reply envelope as a dict.
        Raises A2ATimeout after ``timeout_seconds``.
        """
        correlation_id = uuid.uuid4()
        envelope = A2AEnvelope(
            id=uuid.uuid4(),
            from_agent=from_agent_id,
            to_agent=str(to_agent_id),
            workflow_run_id=workflow_run_id,
            type=A2AMessageType.CALL,
            payload=payload,
            correlation_id=correlation_id,
            created_at=datetime.now(UTC),
        )

        await self.send(
            envelope=envelope,
            caller_invocation_context_id=caller_invocation_context_id,
            callee_attached_context_ids=callee_attached_context_ids,
        )

        # The background A2A consumer (app.workers) is the sole reader of inbox
        # streams and hands replies to the rendezvous. Reading the stream here
        # would race that consumer for the same consumer-group entry, so we
        # block on the rendezvous instead.
        reply = await a2a_rendezvous.await_reply(correlation_id, timeout_seconds)
        if reply is None:
            raise A2ATimeout(
                f"sync call from {from_agent_id} to {to_agent_id} "
                f"timed out after {timeout_seconds}s "
                f"(correlation_id={correlation_id})"
            )
        # A degraded reply (no agent runtime wired to serve the CALL) fails
        # fast rather than masquerading as a successful but empty answer.
        reply_payload = reply.get("payload")
        if isinstance(reply_payload, dict) and a2a_rendezvous.A2A_ERROR_KEY in reply_payload:
            raise A2ADeliveryFailed(
                f"a2a call from {from_agent_id} to {to_agent_id} could not be "
                f"served: {reply_payload[a2a_rendezvous.A2A_ERROR_KEY]} "
                f"(correlation_id={correlation_id})"
            )
        return reply

    async def notify(
        self,
        *,
        from_agent_id: uuid.UUID,
        to_agent_id: uuid.UUID,
        payload: dict[str, Any],
        workflow_run_id: uuid.UUID | None = None,
        caller_invocation_context_id: uuid.UUID | None = None,
        callee_attached_context_ids: frozenset[uuid.UUID] | None = None,
    ) -> str:
        """Fire-and-forget notification (R9.16)."""
        envelope = A2AEnvelope(
            id=uuid.uuid4(),
            from_agent=from_agent_id,
            to_agent=str(to_agent_id),
            workflow_run_id=workflow_run_id,
            type=A2AMessageType.NOTIFY,
            payload=payload,
            correlation_id=uuid.uuid4(),
            created_at=datetime.now(UTC),
        )
        return await self.send(
            envelope=envelope,
            caller_invocation_context_id=caller_invocation_context_id,
            callee_attached_context_ids=callee_attached_context_ids,
        )

    async def reply(
        self,
        *,
        from_agent_id: uuid.UUID,
        to_agent_id: uuid.UUID,
        correlation_id: uuid.UUID,
        payload: dict[str, Any],
        workflow_run_id: uuid.UUID | None = None,
    ) -> str:
        """Send a reply to a previous call (completes sync call handshake)."""
        # Validate from_agent exists and is not deleted; scope check is skipped
        # because the original CALL already passed it, but we still must confirm
        # the sender is a real, live agent to prevent reply spoofing.
        await self._require_agent(from_agent_id)
        envelope = A2AEnvelope(
            id=uuid.uuid4(),
            from_agent=from_agent_id,
            to_agent=str(to_agent_id),
            workflow_run_id=workflow_run_id,
            type=A2AMessageType.REPLY,
            payload=payload,
            correlation_id=correlation_id,
            created_at=datetime.now(UTC),
        )
        # Replies skip scope check — the original call already passed it.
        to_id = uuid.UUID(envelope.to_agent)
        await a2a_streams.ensure_consumer_group(to_id)
        envelope_json = json.dumps(envelope.to_dict(), separators=(",", ":"))
        stream_id = await a2a_streams.xadd_envelope(to_id, envelope_json)
        A2A_MESSAGES.labels(type="reply").inc()

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="a2a.sent",
                resource_type="a2a_message",
                resource_id=envelope.id,
                metadata={
                    "from_agent": str(envelope.from_agent),
                    "to_agent": envelope.to_agent,
                    "type": "reply",
                    "correlation_id": str(correlation_id),
                    "stream_id": stream_id,
                },
            ),
        )
        return stream_id

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def _broadcast(
        self,
        envelope: A2AEnvelope,
        *,
        caller_invocation_context_id: uuid.UUID | None,
    ) -> str:
        """Fan-out to all a2a-enabled agents in the caller's project.

        Each target is scope-checked individually; targets that fail
        the check are silently skipped (not errors for broadcast).
        """
        caller = await self._require_agent(envelope.from_agent)

        # Guard: broadcast must not proceed from an agent in a soft-deleted project.
        caller_project = await self._tenancy.get_project(caller.project_id, include_deleted=True)
        if caller_project is None or caller_project.deleted_at is not None:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="a2a.forbidden",
                    resource_type="a2a_message",
                    metadata={
                        "caller_agent_id": str(caller.id),
                        "to_agent": "broadcast:workspace",
                        "reason": "caller project soft-deleted",
                    },
                ),
            )
            raise A2AForbidden(f"broadcast denied: caller project {caller.project_id} is soft-deleted")

        agents = await self._agents.list_agents_for_project(caller.project_id)

        first_stream_id = ""
        delivered_count = 0
        for target in agents:
            if target.id == caller.id:
                continue
            if not target.a2a_enabled:
                continue

            # Per-target scope check (G.2).
            try:
                await self._enforce_scope(
                    caller=caller,
                    callee=target,
                    callee_attached_context_ids=frozenset(),
                    caller_invocation_context_id=caller_invocation_context_id,
                )
            except A2AForbidden:
                continue

            per_agent = A2AEnvelope(
                id=uuid.uuid4(),
                from_agent=envelope.from_agent,
                to_agent=str(target.id),
                workflow_run_id=envelope.workflow_run_id,
                type=envelope.type,
                payload=envelope.payload,
                correlation_id=envelope.correlation_id,
                created_at=envelope.created_at,
            )
            await a2a_streams.ensure_consumer_group(target.id)
            env_json = json.dumps(per_agent.to_dict(), separators=(",", ":"))
            sid = await a2a_streams.xadd_envelope(target.id, env_json)
            A2A_MESSAGES.labels(type=envelope.type.value).inc()
            delivered_count += 1
            if not first_stream_id:
                first_stream_id = sid

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="a2a.sent",
                resource_type="a2a_message",
                resource_id=envelope.id,
                metadata={
                    "from_agent": str(envelope.from_agent),
                    "to_agent": "broadcast:workspace",
                    "type": envelope.type.value,
                    "broadcast": True,
                    "delivered_count": delivered_count,
                },
            ),
        )
        return first_stream_id

    # ------------------------------------------------------------------
    # Scope enforcement (G.2)
    # ------------------------------------------------------------------

    async def _enforce_scope(
        self,
        *,
        caller: Agent,
        callee: Agent,
        callee_attached_context_ids: frozenset[uuid.UUID],
        caller_invocation_context_id: uuid.UUID | None,
    ) -> None:
        """Reuse E.4 scope checker; translate to orchestration error."""
        # _require_agent already filters agent-level deletion; we still need
        # to enforce R9.17 rule 3 ("target project not soft-deleted") because
        # project.soft_delete does NOT cascade to agents.deleted_at.
        callee_project = await self._tenancy.get_project(callee.project_id, include_deleted=True)
        callee_project_deleted = callee_project is None or callee_project.deleted_at is not None
        check = A2ACheckInput(
            caller=caller,
            callee=callee,
            callee_attached_context_ids=callee_attached_context_ids,
            caller_invocation_context_id=caller_invocation_context_id,
            callee_project_deleted=callee_project_deleted,
        )
        verdict = evaluate(check)
        if not verdict.allowed:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="a2a.forbidden",
                    resource_type="a2a_message",
                    metadata={
                        "caller_agent_id": str(caller.id),
                        "callee_agent_id": str(callee.id),
                        "reason": verdict.reason,
                    },
                ),
            )
            raise A2AForbidden(f"a2a denied: {verdict.reason} " f"(caller={caller.id}, callee={callee.id})")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _require_agent(self, agent_id: uuid.UUID) -> Agent:
        agent = await self._agents.get_agent(agent_id)
        if agent is None:
            raise A2AForbidden(f"agent {agent_id} not found")
        return agent


__all__ = ["A2AService"]
