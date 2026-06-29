"""Agent use-cases (§22.6) — the single write path for the agents context.

Guardrails enforced here:

- **1 000 active agents / project cap** (R9.01).
- **Key Group must live in the same project** as the agent (R7.02 spirit:
  Key Groups are project-scoped; an agent pointing at another project's
  Group would silently break isolation).
- **Optimistic locking** on patch / delete via `If-Match: <version>`.
- **Audit tap** for every state-changing call.

SoC:
- The service owns the cross-table invariants (cap, key-group project check).
- Repositories own storage shape and FK-level errors.
- The router owns pydantic schemas, auth guards, and If-Match parsing.
"""

from __future__ import annotations

import re
import struct
import uuid
from collections.abc import Sequence
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.domain.errors import (
    AgentCapExceeded,
    AgentNotFound,
    AgentToolNotFound,
    AgentToolTypeImmutable,
    FileSearchNeedsKnowledge,
    GraphRagConfigOutOfProject,
    KeyGroupNoMatchingProvider,
    KeyGroupOutOfProject,
    RagConfigOutOfProject,
    ToolNotAvailable,
)
from contexts.agents.domain.models import (
    SINGLETON_TOOL_TYPES,
    Agent,
    AgentDraft,
    AgentTool,
    AgentToolType,
    ContextMode,
    PromptStrategy,
    ToolProbeResult,
)
from contexts.agents.infrastructure.repositories import (
    AgentRepository,
    AgentToolRepository,
)
from contexts.keys.interfaces.facade import KeysFacade
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from shared_kernel import audit

_AGENT_CAP_PER_PROJECT = 1000

_FUNCTION_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_FUNCTION_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
# Built-in / runtime tool names a user function must not shadow. The per-turn
# ToolRegistry is a name->Tool dict where the last entry wins, so a user function
# named e.g. `cast_approval_vote` would silently replace the real tool and route
# the model's intended call to an arbitrary egress URL. MCP tools occupy the
# `mcp__` prefix.
_RESERVED_FUNCTION_NAMES: frozenset[str] = frozenset(
    {
        "web_search",
        "code_exec",
        "file",
        "file_search",
        "update_wakeup",
        "load_prompt_section",
        "cast_approval_vote",
    }
)
_RESERVED_FUNCTION_PREFIX = "mcp__"


def _has_ref_key(obj: Any) -> bool:
    if isinstance(obj, dict):
        if "$ref" in obj:
            return True
        return any(_has_ref_key(v) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_ref_key(v) for v in obj)
    return False


def _validate_function_config(config: dict[str, Any]) -> None:
    name = config.get("name")
    if not name or not isinstance(name, str) or not _FUNCTION_NAME_RE.match(name):
        raise ValueError("function config.name is required (a-z0-9_, 1-64 chars)")
    if name in _RESERVED_FUNCTION_NAMES or name.startswith(_RESERVED_FUNCTION_PREFIX):
        raise ValueError(f"function config.name {name!r} is reserved for a built-in tool")

    desc = config.get("description")
    if not desc or not isinstance(desc, str) or len(desc) > 1000:
        raise ValueError("function config.description is required (max 1000 chars)")

    params = config.get("parameters")
    if not isinstance(params, dict) or params.get("type") != "object":
        raise ValueError("function config.parameters must be a JSON object schema")
    import json as _json

    params_size = len(_json.dumps(params, separators=(",", ":")))
    if params_size > 10_000:
        raise ValueError("function config.parameters JSON too large (max 10 KB)")
    props = params.get("properties")
    if isinstance(props, dict) and len(props) > 50:
        raise ValueError("function config.parameters.properties has too many entries (max 50)")
    if _has_ref_key(params):
        raise ValueError("function config.parameters must not use $ref")

    http = config.get("http")
    if not isinstance(http, dict):
        raise ValueError("function config.http is required")
    method = str(http.get("method", "")).upper()
    if method not in _FUNCTION_METHODS:
        raise ValueError(f"function config.http.method must be one of {sorted(_FUNCTION_METHODS)}")
    http["method"] = method

    url = http.get("url")
    if not url or not isinstance(url, str) or len(url) > 2000:
        raise ValueError("function config.http.url is required (max 2000 chars)")
    parts = urlsplit(url)
    if parts.scheme not in ("https",):
        raise ValueError("function config.http.url must use https")
    host = parts.hostname or ""
    if not host:
        raise ValueError("function config.http.url must have a hostname")
    try:
        import ipaddress

        ipaddress.ip_address(host)
        raise ValueError("function config.http.url must use a hostname, not an IP literal")
    except ValueError as exc:
        if "IP literal" in str(exc):
            raise
    headers = http.get("headers")
    if headers is not None:
        if not isinstance(headers, dict) or len(headers) > 20:
            raise ValueError("function config.http.headers must be a dict (max 20)")
        blocked = frozenset(
            {
                "authorization",
                "cookie",
                "proxy-authorization",
                "host",
                "transfer-encoding",
                "content-length",
                "content-encoding",
                "connection",
                "upgrade",
                "te",
                "trailer",
                "keep-alive",
            }
        )
        for k in headers:
            kl = k.lower()
            if kl in blocked or kl.startswith("x-smap-"):
                raise ValueError(f"function config.http.headers must not include {k}")


def _validate_mcp_config(config: dict[str, Any], *, allow_empty_allowlist: bool = False) -> None:
    src = config.get("source")
    if src not in ("url", "package"):
        raise ValueError("hosted_mcp config.source must be 'url' or 'package'")
    ref = config.get("reference")
    if not ref or not isinstance(ref, str) or len(ref) > 2000:
        raise ValueError("hosted_mcp config.reference is required (max 2000 chars)")
    allowed = config.get("allowed_tools")
    if not isinstance(allowed, list):
        raise ValueError("hosted_mcp config.allowed_tools must list at least one tool")
    # An empty allowlist produces zero runtime tools (build_agent_tools iterates the
    # allowlist), so the binding is silently inert. Reject it on create; on patch we
    # tolerate an already-empty list so legacy bindings (migration backfilled []) stay
    # editable rather than 422-ing on every edit.
    if not allowed and not allow_empty_allowlist:
        raise ValueError("hosted_mcp config.allowed_tools must list at least one tool")
    if len(allowed) > 200:
        raise ValueError("hosted_mcp config.allowed_tools must have at most 200 entries")
    if not all(isinstance(name, str) and name for name in allowed):
        raise ValueError("hosted_mcp config.allowed_tools entries must be non-empty strings")


# Sentinel for system-initiated wakeup patches (§22.6). Never maps to a real
# user row — authored-snapshot overwrites are skipped when the actor is the system.
_SYSTEM_ACTOR_ID = uuid.UUID(int=0)
# A create that supplies no wakeup config would otherwise parse as inert (R15.01:
# no trigger enabled) and the agent would never respond. Default to replying to
# every message so agents created via API / CLI / import behave like UI-created
# ones. A caller wanting a different cadence — or a deliberately inert agent —
# passes an explicit config (even an empty {}), which is respected.
_DEFAULT_WAKEUP_CONFIG: dict[str, Any] = {"triggers": {"every_n_messages": {"enabled": True, "n": 1}}}


class AgentService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._agents = AgentRepository(db)
        self._tools = AgentToolRepository(db)
        self._keys = KeysFacade(db)
        self._knowledge = KnowledgeFacade(db)

    async def _assert_key_group_in_project(self, *, key_group_id: uuid.UUID, project_id: uuid.UUID) -> None:
        group = await self._keys.get_key_group(key_group_id)
        if group is None or group.project_id != project_id:
            raise KeyGroupOutOfProject(f"key_group {key_group_id} is not in project {project_id}")

    async def _assert_key_group_has_provider(
        self, *, key_group_id: uuid.UUID, model_hint: str
    ) -> None:
        if not await self._keys.has_carried_provider_in_group(key_group_id, model_hint):
            raise KeyGroupNoMatchingProvider(
                f"key_group {key_group_id} has no carried keys for provider {model_hint!r}"
            )

    async def _assert_rag_config_in_project(self, *, rag_config_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """SEC-H1 — a RAG config attached to an agent must live in the same
        project, else the agent would pull another tenant's document chunks
        into context at retrieval time (the Qdrant collection is keyed on the
        *config's* project_id). Mirrors `_assert_key_group_in_project`.
        """
        cfg = await self._knowledge.get_rag_config(rag_config_id)
        if cfg is None or cfg.project_id != project_id:
            raise RagConfigOutOfProject(f"rag_config {rag_config_id} is not in project {project_id}")

    async def _assert_graphrag_config_in_project(
        self, *, graphrag_config_id: uuid.UUID, project_id: uuid.UUID
    ) -> None:
        """SEC-H1 — same cross-tenant guard for an attached GraphRAG config."""
        cfg = await self._knowledge.get_graphrag_config(graphrag_config_id)
        if cfg is None or cfg.project_id != project_id:
            raise GraphRagConfigOutOfProject(
                f"graphrag_config {graphrag_config_id} is not in project {project_id}"
            )

    async def create(
        self,
        *,
        project_id: uuid.UUID,
        draft: AgentDraft,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Agent:
        # R9.01 cap — serialise concurrent creates for the same project so
        # the count check and INSERT are atomic. The advisory lock is released
        # automatically when this transaction commits or rolls back.
        lock_id = struct.unpack(">q", project_id.bytes[:8])[0]
        await self._db.execute(sa.text("SELECT pg_advisory_xact_lock(:id)").bindparams(id=lock_id))
        count = await self._agents.count_active(project_id)
        if count >= _AGENT_CAP_PER_PROJECT:
            raise AgentCapExceeded(f"project {project_id} has {count} agents (cap={_AGENT_CAP_PER_PROJECT})")

        if draft.name is None or not draft.name.strip():
            raise ValueError("name is required")
        if draft.model_hint is None:
            raise ValueError("model_hint is required")
        if draft.key_group_id is None:
            raise ValueError("key_group_id is required")

        await self._assert_key_group_in_project(
            key_group_id=draft.key_group_id,
            project_id=project_id,
        )
        await self._assert_key_group_has_provider(
            key_group_id=draft.key_group_id,
            model_hint=draft.model_hint.value,
        )
        if draft.rag_config_id is not None:
            await self._assert_rag_config_in_project(
                rag_config_id=draft.rag_config_id,
                project_id=project_id,
            )
        if draft.graphrag_config_id is not None:
            await self._assert_graphrag_config_in_project(
                graphrag_config_id=draft.graphrag_config_id,
                project_id=project_id,
            )

        # `is not None` (not truthiness): an explicit empty {} means "inert by
        # choice" and must not be overridden by the default.
        wakeup = draft.wakeup_config if draft.wakeup_config is not None else _DEFAULT_WAKEUP_CONFIG
        agent = await self._agents.create(
            project_id=project_id,
            name=draft.name.strip(),
            model_hint=draft.model_hint,
            model_id=draft.model_id,
            key_group_id=draft.key_group_id,
            system_prompt=draft.system_prompt or "",
            prompt_strategy=draft.prompt_strategy or PromptStrategy.FULL,
            rag_config_id=draft.rag_config_id,
            graphrag_config_id=draft.graphrag_config_id,
            context_mode=draft.context_mode or ContextMode.GENERAL,
            context_token_cap=draft.context_token_cap,
            a2a_enabled=bool(draft.a2a_enabled) if draft.a2a_enabled is not None else False,
            wakeup_config=wakeup,
            # Mirror the config exactly: an explicit empty {} ("inert by choice")
            # is a real authored baseline, so it must not collapse to None here
            # the way a truthiness check would.
            wakeup_authored_snapshot=wakeup,
            workflow_capabilities=draft.workflow_capabilities or {},
        )
        await self.provision_tool_singletons(agent.id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.created",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=agent.id,
                metadata={
                    "project_id": str(project_id),
                    "name": agent.name,
                    "model_hint": agent.model_hint.value,
                    "prompt_strategy": agent.prompt_strategy.value,
                    "context_mode": agent.context_mode.value,
                },
                request_id=request_id,
            ),
        )
        return agent

    async def get(self, agent_id: uuid.UUID) -> Agent:
        agent = await self._agents.get(agent_id)
        if agent is None:
            raise AgentNotFound(str(agent_id))
        return agent

    async def list_for_project(
        self,
        project_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> Sequence[Agent]:
        return await self._agents.list_for_project(
            project_id,
            limit=limit,
            offset=offset,
        )

    async def patch(
        self,
        *,
        agent_id: uuid.UUID,
        draft: AgentDraft,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> Agent:
        current = await self.get(agent_id)

        # If the key group is being swapped, validate project membership.
        new_kg = draft.key_group_id
        if new_kg is not None and new_kg != current.key_group_id:
            await self._assert_key_group_in_project(
                key_group_id=new_kg,
                project_id=current.project_id,
            )
        # If key_group or model_hint changed, validate provider availability.
        effective_kg = new_kg if new_kg is not None else current.key_group_id
        effective_hint = draft.model_hint.value if draft.model_hint is not None else current.model_hint.value
        if new_kg is not None or draft.model_hint is not None:
            await self._assert_key_group_has_provider(
                key_group_id=effective_kg,
                model_hint=effective_hint,
            )
        # SEC-H1 — same project guard when (re)attaching a RAG / GraphRAG
        # config. `clear_*` wins over a stale id, so only validate an actual
        # attach (the create path does the same check).
        if not draft.clear_rag_config and draft.rag_config_id is not None:
            await self._assert_rag_config_in_project(
                rag_config_id=draft.rag_config_id,
                project_id=current.project_id,
            )
        if not draft.clear_graphrag_config and draft.graphrag_config_id is not None:
            await self._assert_graphrag_config_in_project(
                graphrag_config_id=draft.graphrag_config_id,
                project_id=current.project_id,
            )

        values: dict[str, Any] = {}
        if draft.name is not None:
            values["name"] = draft.name.strip()
        if draft.model_hint is not None:
            values["model_hint"] = draft.model_hint.value
        if draft.clear_model_id:
            values["model_id"] = None
        elif draft.model_id is not None:
            values["model_id"] = draft.model_id
        if draft.key_group_id is not None:
            values["key_group_id"] = draft.key_group_id
        if draft.system_prompt is not None:
            values["system_prompt"] = draft.system_prompt
        if draft.prompt_strategy is not None:
            values["prompt_strategy"] = draft.prompt_strategy.value
        if draft.clear_rag_config:
            values["rag_config_id"] = None
        elif draft.rag_config_id is not None:
            values["rag_config_id"] = draft.rag_config_id
        if draft.clear_graphrag_config:
            values["graphrag_config_id"] = None
        elif draft.graphrag_config_id is not None:
            values["graphrag_config_id"] = draft.graphrag_config_id
        if draft.context_mode is not None:
            values["context_mode"] = draft.context_mode.value
        if draft.clear_context_token_cap:
            values["context_token_cap"] = None
        elif draft.context_token_cap is not None:
            values["context_token_cap"] = draft.context_token_cap
        if draft.a2a_enabled is not None:
            values["a2a_enabled"] = draft.a2a_enabled
        if draft.wakeup_config is not None:
            values["wakeup_config"] = draft.wakeup_config
            # Human edit → update the authored snapshot (G.5).
            # System actor (uuid(int=0)) updates are self-modifications
            # and should NOT overwrite the authored snapshot.
            if actor_user_id != _SYSTEM_ACTOR_ID:
                values["wakeup_authored_snapshot"] = draft.wakeup_config if draft.wakeup_config else None
        if draft.workflow_capabilities is not None:
            values["workflow_capabilities"] = draft.workflow_capabilities

        updated = await self._agents.patch(
            agent_id=agent_id,
            expected_version=expected_version,
            values=values,
        )
        if not values:
            # DOM-9: an empty patch set no recognised fields — repo.patch
            # validated the version and returned the row unchanged, with no
            # UPDATE and no version bump. Mirror ChatroomService.patch: emit
            # no `agent.edited` audit row, since nothing was actually edited.
            return updated
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.edited",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=updated.id,
                metadata={"fields": sorted(values.keys())},
                request_id=request_id,
            ),
        )
        return updated

    async def soft_delete(
        self,
        *,
        agent_id: uuid.UUID,
        expected_version: int,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self._agents.soft_delete(
            agent_id=agent_id,
            expected_version=expected_version,
        )
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=agent_id,
                request_id=request_id,
            ),
        )

    # ------------------------------------------------------------------
    # Unified agent tools surface (Phase A)
    # ------------------------------------------------------------------

    async def list_tools(
        self,
        agent_id: uuid.UUID,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentTool]:
        await self.get(agent_id)
        return list(await self._tools.list(agent_id, limit=limit, offset=offset))

    async def add_tool(
        self,
        *,
        agent_id: uuid.UUID,
        tool_type: AgentToolType,
        display_name: str | None = None,
        config: dict[str, Any] | None = None,
        auth: dict[str, Any] | None = None,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> AgentTool:
        await self.get(agent_id)

        if tool_type in SINGLETON_TOOL_TYPES:
            raise AgentToolTypeImmutable(
                f"singleton tool {tool_type.value} is auto-provisioned; " f"toggle via PATCH /tools/{{id}}"
            )
        if tool_type == AgentToolType.LOCAL_SHELL:
            raise ToolNotAvailable("local_shell is not available yet")

        tool_config = dict(config or {})

        if tool_type == AgentToolType.HOSTED_MCP:
            _validate_mcp_config(tool_config)

        if tool_type == AgentToolType.LOCAL_FUNCTION:
            _validate_function_config(tool_config)

        tool = await self._tools.add(
            agent_id=agent_id,
            tool_type=tool_type,
            enabled=True,
            display_name=display_name,
            config=tool_config,
        )

        if auth:
            from contexts.agents.application.tool_auth import seal_tool_auth

            sealed = seal_tool_auth(tool.id, auth)
            tool_config["auth"] = sealed
            tool = await self._tools.patch(
                agent_id=agent_id,
                tool_id=tool.id,
                config=tool_config,
            )

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.tool_added",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_tool",
                resource_id=tool.id,
                metadata={
                    "agent_id": str(agent_id),
                    "tool_type": tool_type.value,
                    "display_name": display_name,
                    "auth_set": auth is not None,
                },
                request_id=request_id,
            ),
        )
        return tool

    async def patch_tool(
        self,
        *,
        agent_id: uuid.UUID,
        tool_id: uuid.UUID,
        enabled: bool | None = None,
        display_name: str | None = None,
        clear_display_name: bool = False,
        config: dict[str, Any] | None = None,
        auth: dict[str, Any] | None = None,
        clear_auth: bool = False,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> AgentTool:
        agent = await self.get(agent_id)
        existing = await self._tools.get(agent_id=agent_id, tool_id=tool_id)
        if existing is None:
            raise AgentToolNotFound(str(tool_id))

        if (
            existing.tool_type == AgentToolType.HOSTED_FILE_SEARCH
            and enabled is True
            and agent.rag_config_id is None
        ):
            raise FileSearchNeedsKnowledge(
                "attach a knowledge source (RAG config) to the agent before enabling File Search"
            )

        patch_config = dict(config) if config is not None else None

        # Merge onto the stored config so a partial patch (e.g. allowed_tools only)
        # never drops sealed auth or other persisted fields, then re-validate.
        if patch_config is not None and existing.tool_type in (
            AgentToolType.LOCAL_FUNCTION,
            AgentToolType.HOSTED_MCP,
        ):
            merged = {**existing.config, **patch_config}
            sealed_auth = merged.pop("auth", None)
            if existing.tool_type == AgentToolType.LOCAL_FUNCTION:
                _validate_function_config(merged)
            else:
                _validate_mcp_config(
                    merged,
                    allow_empty_allowlist=not existing.config.get("allowed_tools"),
                )
            # Preserve the stored credential unless the caller replaces or clears it.
            if sealed_auth is not None and not clear_auth:
                merged["auth"] = sealed_auth
            patch_config = merged
        elif clear_auth and patch_config is None:
            # Clear-only patch: drop the stored credential without touching other config.
            patch_config = {k: v for k, v in existing.config.items() if k != "auth"}

        if auth:
            from contexts.agents.application.tool_auth import seal_tool_auth

            sealed = seal_tool_auth(existing.id, auth)
            if patch_config is None:
                patch_config = dict(existing.config)
            patch_config["auth"] = sealed

        tool = await self._tools.patch(
            agent_id=agent_id,
            tool_id=tool_id,
            enabled=enabled,
            display_name=display_name,
            clear_display_name=clear_display_name,
            config=patch_config,
        )

        changed: list[str] = []
        if enabled is not None:
            changed.append("enabled")
        if display_name is not None or clear_display_name:
            changed.append("display_name")
        if config is not None or auth or clear_auth:
            changed.append("config")

        if changed:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="agent.tool_updated",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="agent_tool",
                    resource_id=tool_id,
                    metadata={
                        "agent_id": str(agent_id),
                        "tool_type": existing.tool_type.value,
                        "fields": changed,
                    },
                    request_id=request_id,
                ),
            )
        return tool

    async def remove_tool(
        self,
        *,
        agent_id: uuid.UUID,
        tool_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> None:
        await self.get(agent_id)
        existing = await self._tools.get(agent_id=agent_id, tool_id=tool_id)
        if existing is None:
            raise AgentToolNotFound(str(tool_id))
        if existing.is_singleton():
            raise AgentToolTypeImmutable(
                f"singleton tool {existing.tool_type.value} cannot be deleted; " f"disable via PATCH"
            )
        await self._tools.remove(agent_id=agent_id, tool_id=tool_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.tool_removed",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_tool",
                resource_id=tool_id,
                metadata={
                    "agent_id": str(agent_id),
                    "tool_type": existing.tool_type.value,
                },
                request_id=request_id,
            ),
        )

    async def probe_tool(
        self,
        *,
        agent: Agent,
        tool: AgentTool,
        runner: Any,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> ToolProbeResult:
        """Reachability/test probe for a hosted_mcp or local_function tool.

        Houses the egress + auth-resolution + probe orchestration (kept out of the
        route, which only knows facades) and audits the test action either way.
        """
        if tool.tool_type == AgentToolType.LOCAL_FUNCTION:
            result = await self._probe_function(agent, tool)
        elif tool.tool_type == AgentToolType.HOSTED_MCP:
            result = await self._probe_mcp(agent, tool, runner)
        else:
            raise ToolNotAvailable("only hosted_mcp and local_function tools can be tested")

        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.tool_tested",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent_tool",
                resource_id=tool.id,
                metadata={
                    "agent_id": str(agent.id),
                    "tool_type": tool.tool_type.value,
                    "ok": result.ok,
                },
                request_id=request_id,
            ),
        )
        return result

    async def _probe_function(self, agent: Agent, tool: AgentTool) -> ToolProbeResult:
        import time

        from contexts.agents.application.runtime.builtin_tools import (
            _auth_pair,
            function_egress_allowed,
            resolve_tool_auth,
        )
        from contexts.agents.infrastructure.egress_client import egress_proxy_client_from_settings

        cfg = tool.config or {}
        http_cfg = cfg.get("http", {})
        url = str(http_cfg.get("url", ""))
        method = str(http_cfg.get("method", "GET")).upper()

        host, allowed = await function_egress_allowed(self._db, project_id=agent.project_id, url=url)
        if not allowed:
            return ToolProbeResult(
                ok=False, error=f"host {host} is not on the project egress allowlist"
            )

        auth, unsealable = resolve_tool_auth(tool)
        if unsealable:
            return ToolProbeResult(ok=False, error="stored credentials could not be unsealed")

        proxy = egress_proxy_client_from_settings()
        headers = dict(http_cfg.get("headers") or {})
        start = time.monotonic()
        try:
            status, _h, _b = await proxy.request(
                method=method,
                url=url,
                project_id=agent.project_id,
                headers=headers,
                upstream_auth=_auth_pair(auth),
                timeout_s=15.0,
            )
        except Exception as exc:
            return ToolProbeResult(
                ok=False,
                duration_ms=int((time.monotonic() - start) * 1000),
                error=str(exc) or exc.__class__.__name__,
            )
        duration = int((time.monotonic() - start) * 1000)
        # Only a 2xx/3xx counts as a passing test; a 4xx/5xx is reachable but a
        # failure (e.g. 401 = the configured credential is being rejected).
        ok = 200 <= status < 400
        return ToolProbeResult(
            ok=ok, status=status, duration_ms=duration, error=None if ok else f"HTTP {status}"
        )

    async def _probe_mcp(self, agent: Agent, tool: AgentTool, runner: Any) -> ToolProbeResult:
        import time

        from contexts.agents.application.runtime.builtin_tools import resolve_tool_auth

        cfg = tool.config or {}
        auth, unsealable = resolve_tool_auth(tool)
        if unsealable:
            return ToolProbeResult(ok=False, error="stored credentials could not be unsealed")

        start = time.monotonic()
        try:
            result = await runner.probe(
                agent_id=agent.id,
                source=cfg.get("source", "url"),
                reference=cfg.get("reference", ""),
                allowed_tools=cfg.get("allowed_tools", []),
                auth=auth,
                project_id=agent.project_id,
                timeout_s=20.0,
            )
        except Exception as exc:
            return ToolProbeResult(
                ok=False,
                duration_ms=int((time.monotonic() - start) * 1000),
                error=str(exc) or exc.__class__.__name__,
            )
        return ToolProbeResult(
            ok=result.ok,
            tool_names=tuple(result.tool_names),
            duration_ms=result.duration_ms,
            error=result.error,
        )

    async def provision_tool_singletons(self, agent_id: uuid.UUID) -> None:
        """Called on agent create — idempotent insertion of the 4 singleton rows."""
        await self._tools.provision_singletons(
            agent_id=agent_id,
            web_search=True,
            code_interpreter=False,
            file_workspace=True,
            file_search_enabled=False,
        )


__all__ = ["AgentService"]
