"""MCP binding CRUD + test (E.9).

Envelope-encrypts the optional ``auth`` material before persisting the config
JSONB. Emits ``mcp.binding_*`` and ``mcp.test_run`` audit events (R17.01).
"""

from __future__ import annotations

import base64
import time
import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.mcp_ports import SandboxRunner
from contexts.agents.domain.errors import (
    McpBindingNotFound,
    McpEgressDenied,
    McpTestFailed,
    McpTimeout,
)
from contexts.agents.domain.mcp import McpServerDraft, McpTestResult
from contexts.agents.domain.models import McpBinding
from contexts.agents.infrastructure.repositories import AgentMcpBindingRepository
from shared_kernel import audit
from shared_kernel.security import envelope as env

_AUTH_AAD_NS = b"mcp_binding_auth"


def _auth_aad(binding_id: uuid.UUID) -> bytes:
    return _AUTH_AAD_NS + b":" + str(binding_id).encode("ascii")


def _seal_auth(binding_id: uuid.UUID, auth: dict[str, Any]) -> dict[str, Any]:
    """Envelope-encrypt auth material and return a JSON-safe marker dict."""
    import json

    plaintext = json.dumps(auth, sort_keys=True).encode("utf-8")
    record = env.encrypt_envelope(plaintext, _auth_aad(binding_id))
    return {
        "__sealed__": True,
        "ciphertext": base64.b64encode(record.ciphertext).decode("ascii"),
        "nonce": base64.b64encode(record.nonce).decode("ascii"),
        "dek_wrapped": record.dek_wrapped,
        "ciphertext_hmac": base64.b64encode(record.ciphertext_hmac).decode("ascii"),
        "transit_key_version": record.transit_key_version,
        "hmac_key_version": record.hmac_key_version,
    }


def unseal_auth(binding_id: uuid.UUID, sealed: dict[str, Any]) -> dict[str, Any]:
    """Inverse of :func:`_seal_auth` — used by the sandbox runner at call time."""
    import json

    from shared_kernel.infra.vault import EnvelopeRecord

    record = EnvelopeRecord(
        ciphertext=base64.b64decode(sealed["ciphertext"]),
        nonce=base64.b64decode(sealed["nonce"]),
        dek_wrapped=sealed["dek_wrapped"],
        ciphertext_hmac=base64.b64decode(sealed["ciphertext_hmac"]),
        transit_key_version=int(sealed["transit_key_version"]),
        hmac_key_version=int(sealed["hmac_key_version"]),
    )
    plaintext = env.decrypt_envelope(record, _auth_aad(binding_id))
    return json.loads(plaintext.decode("utf-8"))


class McpBindingService:
    """MCP binding CRUD + test. Caller supplies a :class:`SandboxRunner`."""

    def __init__(self, db: AsyncSession, runner: SandboxRunner | None = None) -> None:
        self._db = db
        self._repo = AgentMcpBindingRepository(db)
        self._runner = runner

    async def list(self, agent_id: uuid.UUID) -> Sequence[McpBinding]:
        return await self._repo.list(agent_id)

    async def add(
        self,
        *,
        agent_id: uuid.UUID,
        draft: McpServerDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> McpBinding:
        # Stage 1: insert without auth so we know the row id; Stage 2: patch
        # the sealed blob in. This keeps the AAD bound to the persisted id.
        config = {k: v for k, v in (draft.config or {}).items() if k != "auth"}
        binding = await self._repo.add(
            agent_id=agent_id,
            source=draft.source,
            reference=draft.reference,
            allowed_tools=draft.allowed_tools,
            config=config,
        )
        if draft.auth:
            sealed = _seal_auth(binding.id, draft.auth)
            config_with_auth = {**config, "auth": sealed}
            # Re-insert via a second round-trip — use raw SA because the repo
            # does not expose update; cheapest correct move is a targeted UPDATE.
            from contexts.agents.infrastructure import tables as t  # noqa: PLC0415

            await self._db.execute(
                t.agent_mcp_servers.update()
                .where(t.agent_mcp_servers.c.id == binding.id)
                .values(config=config_with_auth)
            )
            binding = McpBinding(
                id=binding.id,
                agent_id=binding.agent_id,
                source=binding.source,
                reference=binding.reference,
                allowed_tools=binding.allowed_tools,
                config=config_with_auth,
                created_at=binding.created_at,
            )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="mcp.binding_created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_mcp_binding",
                resource_id=binding.id,
                metadata={
                    "agent_id": str(agent_id),
                    "source": binding.source.value,
                    "reference": binding.reference,
                    "allowed_tools": list(binding.allowed_tools),
                    "auth_sealed": bool(draft.auth),
                },
                request_id=request_id,
            ),
        )
        return binding

    async def remove(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._repo.remove(agent_id=agent_id, binding_id=binding_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="mcp.binding_deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_mcp_binding",
                resource_id=binding_id,
                metadata={"agent_id": str(agent_id)},
                request_id=request_id,
            ),
        )

    async def test(
        self,
        *,
        agent_id: uuid.UUID,
        binding_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
        timeout_s: float = 20.0,
    ) -> McpTestResult:
        if self._runner is None:
            raise McpTestFailed("sandbox runner is not configured")
        bindings = await self._repo.list(agent_id)
        binding = next((b for b in bindings if b.id == binding_id), None)
        if binding is None:
            raise McpBindingNotFound(str(binding_id))

        auth = None
        sealed = binding.config.get("auth") if isinstance(binding.config, dict) else None
        if sealed and isinstance(sealed, dict) and sealed.get("__sealed__"):
            auth = unseal_auth(binding.id, sealed)

        start = time.monotonic()
        try:
            result = await self._runner.probe(
                agent_id=agent_id,
                source=binding.source.value,
                reference=binding.reference,
                allowed_tools=binding.allowed_tools,
                auth=auth,
                project_id=project_id,
                timeout_s=timeout_s,
            )
        except McpEgressDenied:
            duration = int((time.monotonic() - start) * 1000)
            await self._emit_test_audit(
                binding, project_id, actor_user_id, actor_ip, request_id,
                ok=False, error="egress-denied", duration_ms=duration,
            )
            raise
        except McpTimeout:
            duration = int((time.monotonic() - start) * 1000)
            await self._emit_test_audit(
                binding, project_id, actor_user_id, actor_ip, request_id,
                ok=False, error="timeout", duration_ms=duration,
            )
            raise
        except Exception as exc:  # noqa: BLE001 — surface as McpTestFailed
            duration = int((time.monotonic() - start) * 1000)
            err = str(exc) or exc.__class__.__name__
            await self._emit_test_audit(
                binding, project_id, actor_user_id, actor_ip, request_id,
                ok=False, error=err, duration_ms=duration,
            )
            raise McpTestFailed(err) from exc

        await self._emit_test_audit(
            binding, project_id, actor_user_id, actor_ip, request_id,
            ok=result.ok, error=result.error, duration_ms=result.duration_ms,
        )
        return result

    async def _emit_test_audit(
        self,
        binding: McpBinding,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None,
        *,
        ok: bool,
        error: str | None,
        duration_ms: int,
    ) -> None:
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="mcp.test_run",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_mcp_binding",
                resource_id=binding.id,
                metadata={
                    "project_id": str(project_id),
                    "agent_id": str(binding.agent_id),
                    "source": binding.source.value,
                    "ok": ok,
                    "error": error,
                    "duration_ms": duration_ms,
                },
                request_id=request_id,
            ),
        )


__all__ = ["McpBindingService", "unseal_auth"]
