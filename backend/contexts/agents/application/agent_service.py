"""Agent use-cases (§22.6) — the single write path for the agents context.

Guardrails enforced here:

- **1 000 active agents / project cap** (R9.01).
- **Key Group must live in the same project** as the agent (R7.09a:
  Key Groups are project-scoped; an agent pointing at another project's
  Group would silently break isolation).
- **Attached RAG / Knowledge Map config must live in the same project** as
  the agent (SEC-H1) — `_assert_rag_config_in_project` /
  `_assert_knowmap_config_compatible` below.
- **Key Group must differ from the attached Knowledge Map's builder Key
  Group** (R11.01 billing/quota separation) — checked on create/attach and
  again whenever the agent's own `key_group_id` changes. [R11.11] exempts
  Concept Maps from this (no single consumer agent), but a Knowledge Map
  *does* have a well-defined consumer agent, so the split applies the same
  way it used to for GraphRAG attachment before GraphRAG became
  owner-centric.
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
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from contexts.agents.application.runtime.tool_registry import BUILTIN_TOOL_NAMES
from contexts.agents.domain.errors import (
    AgentCapExceeded,
    AgentConfigTooLarge,
    AgentNotFound,
    AgentToolNotFound,
    AgentToolTypeImmutable,
    FileSearchNeedsKnowledge,
    KeyGroupNoMatchingProvider,
    KeyGroupOutOfProject,
    KnowmapBuilderKeyGroupConflict,
    KnowmapConfigOutOfProject,
    RagConfigOutOfProject,
    ToolNotAvailable,
    WorkflowCapabilitiesInvalid,
)
from contexts.agents.domain.models import (
    SINGLETON_TOOL_TYPES,
    Agent,
    AgentDraft,
    AgentTool,
    AgentToolType,
    ContextMode,
    McpToolSpec,
    ToolProbeResult,
)
from contexts.agents.infrastructure.repositories import (
    AgentRepository,
    AgentToolRepository,
)
from contexts.keys.interfaces.facade import KeysFacade
from contexts.knowledge.interfaces.facade import KnowledgeFacade
from shared_kernel import audit
from shared_kernel.db.advisory_lock import advisory_xact_lock, knowmap_builder_lock_key
from shared_kernel.db.restore import raise_restore_conflict
from shared_kernel.json_merge import merge_json_config
from shared_kernel.validation import config_bounds_violation

_AGENT_CAP_PER_PROJECT = 1000

_FUNCTION_NAME_RE = re.compile(r"^[a-z0-9_]{1,64}$")
_FUNCTION_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE"})
# Built-in / runtime tool names a user function must not shadow. The per-turn
# ToolRegistry is a name->Tool dict, so a user function named e.g.
# `cast_approval_vote` would shadow the real tool and route the model's intended
# call to an arbitrary egress URL. Derived from the single canonical set in the
# tool registry (kept in sync by a drift test) so a new built-in is reserved
# automatically. MCP tools occupy the `mcp__` prefix.
_RESERVED_FUNCTION_NAMES = BUILTIN_TOOL_NAMES
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


# Name-legality caps for a raw MCP allowed_tools entry (Fix Design §7 piece 3,
# 2026-07-22-mcp-tool-contract defect B). Deliberately looser than the 64-char
# provider function-name limit: `.` and `:` are legal MCP and common in the
# wild (Q-3), and an upstream name over the composed-prefix budget is still
# bindable -- composition-time sanitisation (builtin_tools.py) truncates and
# appends a digest. This is a sanity/DoS bound plus a reject on characters no
# legitimate MCP tool name uses, not the provider-fit check.
_MCP_TOOL_NAME_MAX = 500
_MCP_TOOL_NAME_CONTROL_OR_WS_RE = re.compile(r"[\x00-\x20\x7f]")  # C0 controls, space, DEL


def _validate_mcp_tool_name(name: str) -> None:
    if len(name) > _MCP_TOOL_NAME_MAX:
        raise ValueError(f"hosted_mcp config.allowed_tools entry {name!r} exceeds {_MCP_TOOL_NAME_MAX} chars")
    if _MCP_TOOL_NAME_CONTROL_OR_WS_RE.search(name):
        raise ValueError(
            f"hosted_mcp config.allowed_tools entry {name!r} must not contain control "
            "characters or whitespace"
        )


def _validate_mcp_config(
    config: dict[str, Any],
    *,
    allow_empty_allowlist: bool = False,
    preexisting_allowed_tools: frozenset[str] = frozenset(),
) -> None:
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
    # Name-legality: only newly-added entries (Q-5 grandfathering) -- a stricter
    # validator must never 422 an operator editing an unrelated field on a
    # legacy row whose stored allowed_tools already contains an illegal entry
    # (the exact allow_empty_allowlist regression class, :188-189 above).
    for name in allowed:
        if name in preexisting_allowed_tools:
            continue
        _validate_mcp_tool_name(name)


# Captured MCP tool contract caps (2026-07-22-mcp-tool-contract). The per-schema
# size/property/$ref caps mirror _validate_function_config's local_function caps
# verbatim (agent_service.py:109-121) -- reuse rather than a second policy to drift
# from. The total-count/total-bytes caps are new: a captured contract comes from a
# third-party server's tools/list response, which for a legacy grandfathered row
# (empty allowlist -> capture everything) is otherwise unbounded.
_MCP_CAPTURE_MAX_TOOLS = 200
_MCP_CAPTURE_MAX_TOTAL_BYTES = 200_000
_MCP_CAPTURE_SCHEMA_MAX_BYTES = 10_000
_MCP_CAPTURE_MAX_PROPERTIES = 50
_MCP_CAPTURE_DESCRIPTION_MAX = 1000


def _validate_captured_schema(schema: dict[str, Any]) -> None:
    """Reject a captured ``inputSchema`` that violates the caps.

    Reuses ``_has_ref_key`` and the 10 KB / 50-property caps
    ``_validate_function_config`` already enforces for ``local_function``
    (:109-121) verbatim, rather than a second policy that could drift from it.
    """
    import json as _json

    size = len(_json.dumps(schema, separators=(",", ":")))
    if size > _MCP_CAPTURE_SCHEMA_MAX_BYTES:
        raise ValueError("captured tool inputSchema JSON too large (max 10 KB)")
    props = schema.get("properties")
    if isinstance(props, dict) and len(props) > _MCP_CAPTURE_MAX_PROPERTIES:
        raise ValueError("captured tool inputSchema has too many properties (max 50)")
    if _has_ref_key(schema):
        raise ValueError("captured tool inputSchema must not use $ref")


def _sanitize_captured_description(desc: str) -> str:
    """Bound length and strip control characters (the description is untrusted
    third-party text placed in a prompt-adjacent field -- a prompt-injection
    vector, not just a display string)."""
    cleaned = "".join(ch for ch in desc if ch in ("\n", "\t") or (ch >= " " and ch != "\x7f"))
    return cleaned[:_MCP_CAPTURE_DESCRIPTION_MAX]


def _sanitize_schema_strings(value: Any) -> Any:
    """Recursively strip control characters + bound length on every string
    embedded in a captured ``inputSchema`` (property descriptions, ``enum``,
    ``pattern``, ``title``, ``default``, ...) -- the same treatment
    :func:`_sanitize_captured_description` gives the top-level description.
    These are equally prompt-adjacent third-party text and were previously
    passed through unmodified while only ``description`` was sanitised.

    Dict *keys* (property names) are left untouched -- altering them would
    change what the schema actually describes, not just how it reads.
    """
    if isinstance(value, str):
        return _sanitize_captured_description(value)
    if isinstance(value, dict):
        return {k: _sanitize_schema_strings(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_schema_strings(v) for v in value]
    return value


def _sanitize_captured_tools(tools: Sequence[McpToolSpec]) -> tuple[tuple[McpToolSpec, ...], list[str]]:
    """Bound + validate a probe's captured tool contract before persisting.

    A schema that fails :func:`_validate_captured_schema` degrades that single
    tool to the permissive fallback rather than failing the whole capture --
    consistent with this fix's core principle that one bad entry must never
    brick everything (that was defect B). Total count/bytes are capped by
    truncation, not rejection, for the same reason. The total counts every
    persisted byte (name + description + schema), not schema alone, and
    ``name`` is bounded the same way a client-declared allowed_tools entry is
    (:func:`_validate_mcp_tool_name`'s cap) -- a captured name comes from the
    same untrusted server. Returns (specs, warnings) for the caller to persist
    and surface via config_warnings respectively.
    """
    import json as _json

    warnings: list[str] = []
    truncated = list(tools[:_MCP_CAPTURE_MAX_TOOLS])
    if len(tools) > _MCP_CAPTURE_MAX_TOOLS:
        warnings.append(
            f"server reported {len(tools)} tools; only the first {_MCP_CAPTURE_MAX_TOOLS} were captured"
        )

    specs: list[McpToolSpec] = []
    total_size = 0
    for spec in truncated:
        name = spec.name[:_MCP_TOOL_NAME_MAX]
        description = _sanitize_captured_description(spec.description)
        raw_schema = spec.input_schema if isinstance(spec.input_schema, dict) else {}
        schema = _sanitize_schema_strings(raw_schema)
        try:
            _validate_captured_schema(schema)
            schema_size = len(_json.dumps(schema, separators=(",", ":")))
        except ValueError:
            warnings.append(f"captured schema for tool {spec.name!r} exceeded limits; using fallback schema")
            schema = {"type": "object", "additionalProperties": True}
            schema_size = 0
        entry_size = schema_size + len(name.encode("utf-8")) + len(description.encode("utf-8"))
        if total_size + entry_size > _MCP_CAPTURE_MAX_TOTAL_BYTES:
            warnings.append(
                f"captured tool contract exceeded the total {_MCP_CAPTURE_MAX_TOTAL_BYTES}-byte cap; "
                f"tool {spec.name!r} and later entries fell back to the permissive schema"
            )
            schema = {"type": "object", "additionalProperties": True}
            entry_size = len(name.encode("utf-8")) + len(description.encode("utf-8"))
        total_size += entry_size
        specs.append(McpToolSpec(name=name, description=description, input_schema=schema))
    return tuple(specs), warnings


# Sentinel for system-initiated wakeup patches (§22.6). Never maps to a real
# user row — authored-snapshot overwrites are skipped when the actor is the system.
_SYSTEM_ACTOR_ID = uuid.UUID(int=0)
# A create that supplies no wakeup config would otherwise parse as inert (R15.01:
# no trigger enabled) and the agent would never respond. Default to replying to
# every message so agents created via API / CLI / import behave like UI-created
# ones. A caller wanting a different cadence — or a deliberately inert agent —
# passes an explicit config (even an empty {}), which is respected.
_DEFAULT_WAKEUP_CONFIG: dict[str, Any] = {"triggers": {"every_n_messages": {"enabled": True, "n": 1}}}


def _assert_config_within_bounds(value: dict[str, Any], *, field: str) -> None:
    """Re-apply `BoundedConfig`'s limits to a merged config column.

    Pydantic bounds the *request*; these columns merge, so N bounded patches would
    otherwise accumulate into an unbounded row — and every later read, serialize
    and wake-up parse of that agent pays for it.
    """
    violation = config_bounds_violation(value)
    if violation is not None:
        raise AgentConfigTooLarge(f"{field}: {violation}")


def _assert_capability_invariants(value: dict[str, Any], *, field: str) -> None:
    """A spawn-capable agent must carry a bound on its live subagents.

    Applied to the value about to be *stored*, never to the request: this column
    merges, so neither key is reliably in the patch. `{"can_create_subagent":
    true}` alone was rejected even when the row already held a bound, and
    `{"max_alive_subagents": null}` alone was accepted and deleted the bound from
    a row whose `can_create_subagent` was true — reproducing exactly the state
    migration 0073 was written to repair.
    """
    if value.get("can_create_subagent") is True and value.get("max_alive_subagents") is None:
        raise WorkflowCapabilitiesInvalid(
            f"{field}.max_alive_subagents is required when can_create_subagent is true"
        )


def _normalize_wakeup_config(merged: dict[str, Any]) -> dict[str, Any]:
    """Normalize the documented `WakeupConfig` shape within a merged
    `wakeup_config` / `wakeup_authored_snapshot` dict, so a partial PATCH persists
    a complete config rather than a fragment (Q-4/Q-5,
    2026-07-27-wakeup-config-type-validation). Root-level keys the domain model
    does not own (e.g. a designer note) pass through untouched — replacing the
    whole dict with `WakeupConfig.to_dict()`'s output would silently drop them
    (Q-3, forced by 2026-07-27-wakeup-config-key-preservation's additive merge).

    Not applied to a `replace_wakeup_config` restore: that write must land the
    authored snapshot verbatim, or the drift check `current == authored`
    (`wakeup_service.refresh_wakeup_config`) could never converge for a snapshot
    written before this normalization existed.

    Merges the normalized shape back onto `merged` (rather than a flat
    `dict.update`) so an unmodelled key nested *inside* a known container --
    e.g. a custom field under `triggers.silence_minutes` or `soft_bounds` --
    survives too. A flat update would replace those containers wholesale with
    `to_dict()`'s output, which only knows the documented sub-fields, silently
    erasing anything else nested inside them.
    """
    from contexts.orchestration.domain.models import WakeupConfig

    return merge_json_config(merged, WakeupConfig.from_dict(merged).to_dict())


def _merge_and_normalize_wakeup(
    base: dict[str, Any] | None, patch: dict[str, Any], *, replace: bool, field: str
) -> dict[str, Any]:
    """Shared merge/normalize/bounds-check for `wakeup_config` and
    `wakeup_authored_snapshot` — same rule (Q-4), different base value each
    caller supplies. A `replace` write (the G.5 restore) skips both merge and
    normalization and lands `patch` verbatim — see `_normalize_wakeup_config`.

    An empty `patch` (`{}`) is a no-op against `base` under this additive merge
    (`merge_json_config` has nothing to iterate), not a reset to blank — that is
    an accepted behavior change from the old whole-column-replace write
    (2026-07-28-wakeup-config-validation-and-patch-semantics F-5/Q-2); an
    explicit per-key `null` still clears whatever the caller wants cleared.
    """
    merged = patch if replace else _normalize_wakeup_config(merge_json_config(base, patch))
    _assert_config_within_bounds(merged, field=field)
    return merged


class AgentService:
    def __init__(self, db: AsyncSession) -> None:
        # Deferred, unlike the KeysFacade/KnowledgeFacade imports above: skills reaches
        # back into this context (`binding_service` -> `AgentsFacade` -> here), so a
        # module-level import would close a real cycle at interpreter start. The agents
        # facade defers its own `AgentService` import for the same reason.
        from contexts.skills.interfaces.facade import SkillsFacade

        self._db = db
        self._agents = AgentRepository(db)
        self._tools = AgentToolRepository(db)
        self._keys = KeysFacade(db)
        self._knowledge = KnowledgeFacade(db)
        self._skills = SkillsFacade(db)

    async def assert_key_group_in_project(self, *, key_group_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """The tenancy gate on an attached key group.

        Public rather than private because the example-pack installer has to run
        it *before* asking which providers the group carries: that question is
        answerable for any group id, so probing first and checking ownership
        second turns the refusal into an oracle for another project's provider
        inventory. One rule called from both places, rather than a second copy.
        """
        group = await self._keys.get_key_group(key_group_id)
        if group is None or group.project_id != project_id:
            raise KeyGroupOutOfProject(f"key_group {key_group_id} is not in project {project_id}")

    async def _assert_key_group_has_provider(self, *, key_group_id: uuid.UUID, model_hint: str) -> None:
        if not await self._keys.has_carried_provider_in_group(key_group_id, model_hint):
            raise KeyGroupNoMatchingProvider(
                f"key_group {key_group_id} has no carried keys for provider {model_hint!r}"
            )

    async def _assert_rag_config_in_project(self, *, rag_config_id: uuid.UUID, project_id: uuid.UUID) -> None:
        """SEC-H1 — a RAG config attached to an agent must live in the same
        project, else the agent would pull another tenant's document chunks
        into context at retrieval time (the Qdrant collection is keyed on the
        *config's* project_id). Mirrors `assert_key_group_in_project`.
        """
        cfg = await self._knowledge.get_rag_config(rag_config_id)
        if cfg is None or cfg.project_id != project_id:
            raise RagConfigOutOfProject(f"rag_config {rag_config_id} is not in project {project_id}")

    async def _assert_knowmap_config_compatible(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        project_id: uuid.UUID,
        key_group_id: uuid.UUID,
        require_exists: bool = True,
    ) -> None:
        """SEC-H1 cross-tenant guard + R11.01 builder/consumer key-group split.

        A knowmap config attached to an agent must live in the same project,
        else the agent would pull another tenant's knowledge into context.
        It must also use a different Key Group than the agent's own, so the
        agent's real-time chat inference and the config's background build
        jobs don't silently share one Key Group's rate limit/budget.

        `require_exists=False` is for re-checking a config the caller is
        *not* attaching in this call (an already-attached config the patch
        payload doesn't mention). If that config was soft-deleted out from
        under the agent, there is nothing left to protect against, so the
        check is skipped instead of turning an unrelated field edit into a
        404 — only an explicit attach must fail loudly on a bad id.
        """
        cfg = await self._knowledge.get_knowmap_config(knowmap_config_id)
        if cfg is None or cfg.project_id != project_id:
            if not require_exists:
                return
            raise KnowmapConfigOutOfProject(
                f"knowmap_config {knowmap_config_id} is not in project {project_id}"
            )
        if cfg.builder_key_group_id == key_group_id:
            raise KnowmapBuilderKeyGroupConflict(
                f"agent key_group_id must differ from its Knowledge Map config's "
                f"builder_key_group_id ({key_group_id})"
            )

    async def detach_agents_colliding_with_knowmap_builder(
        self,
        *,
        knowmap_config_id: uuid.UUID,
        new_builder_key_group_id: uuid.UUID,
        project_id: uuid.UUID,
        actor_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Detach every agent whose consumer key group equals a map's NEW builder
        key group, restoring the R11.25 isolation invariant on the config-side
        mutation path (F-14).

        Owns the agents-context write the knowledge context must not make itself:
        the knowledge config service calls this through :class:`AgentsFacade`
        inside the same update transaction, so the detaches and the builder-group
        write commit or roll back together. Serialised on the config-id advisory
        lock so a concurrent attach cannot slip a colliding agent past this
        reconciliation. Each detach is audit-logged (no key secret — the metadata
        carries only ids). Returns the detached agent ids for the caller to
        surface to the designer.
        """
        await advisory_xact_lock(self._db, knowmap_builder_lock_key(knowmap_config_id))
        detached = await self._agents.detach_from_knowmap_config(
            knowmap_config_id=knowmap_config_id,
            key_group_id=new_builder_key_group_id,
            project_id=project_id,
        )
        for agent_id in detached:
            await audit.emit(
                self._db,
                audit.AuditEvent(
                    action="agent.knowmap_detached",
                    actor_user_id=actor_user_id,
                    actor_ip=actor_ip,
                    resource_type="agent",
                    resource_id=agent_id,
                    metadata={
                        "project_id": str(project_id),
                        "knowmap_config_id": str(knowmap_config_id),
                        "builder_key_group_id": str(new_builder_key_group_id),
                        "reason": "builder_key_group_collision",
                    },
                    request_id=request_id,
                ),
            )
        return detached

    async def clear_config_bindings(
        self,
        *,
        project_id: uuid.UUID,
        rag_config_id: uuid.UUID | None = None,
        knowmap_config_id: uuid.UUID | None = None,
    ) -> list[uuid.UUID]:
        """Unbind every agent in ``project_id`` from a config being soft-deleted
        (F-18), returning the affected agent ids.

        The knowledge delete services call this through :class:`AgentsFacade`
        inside their soft-delete transaction, so the unbind and the
        ``deleted_at`` write commit or roll back together. The DB
        ``ON DELETE SET NULL`` FK only fires on a physical row DELETE, never on
        the soft-delete UPDATE, so the per-agent binding is nulled here
        explicitly.

        Nulling ``rag_config_id`` also disables each agent's HOSTED_FILE_SEARCH
        singleton, preserving the invariant *file_search enabled ⇒
        rag_config_id present* (:meth:`patch_tool` / :meth:`create` enforce it):
        an enabled File Search tool with no backing config is a worse state than
        the dangling FK this fix removes. ``knowmap_config_id`` has no dependent
        tool, so it needs no reconciliation.
        """
        unbound: list[uuid.UUID] = []
        if rag_config_id is not None:
            rag_ids = await self._agents.clear_rag_config(
                project_id=project_id,
                rag_config_id=rag_config_id,
            )
            if rag_ids:
                await self._tools.disable_file_search_for_agents(rag_ids)
            unbound.extend(rag_ids)
        if knowmap_config_id is not None:
            unbound.extend(
                await self._agents.clear_knowmap_config(
                    project_id=project_id,
                    knowmap_config_id=knowmap_config_id,
                )
            )
        return unbound

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

        await self.assert_key_group_in_project(
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
        if draft.knowmap_config_id is not None:
            # Serialise this attach against a concurrent builder-group change on
            # the same map (R11.25 / F-14): whichever commits first is fully
            # observed by the other, so an attach that loses the race sees the new
            # builder group and is rejected below. Held until this transaction
            # ends; ordered after the project-id cap lock above (only the create
            # path takes both, always in this order — no deadlock cycle).
            await advisory_xact_lock(self._db, knowmap_builder_lock_key(draft.knowmap_config_id))
            await self._assert_knowmap_config_compatible(
                knowmap_config_id=draft.knowmap_config_id,
                project_id=project_id,
                key_group_id=draft.key_group_id,
            )

        # `is not None` (not truthiness): an explicit empty {} means "inert by
        # choice" and must not be overridden by the default.
        wakeup = _normalize_wakeup_config(
            draft.wakeup_config if draft.wakeup_config is not None else _DEFAULT_WAKEUP_CONFIG
        )
        # Checked here as well as on the patch path so the invariant belongs to
        # the stored value, not to one request shape. On create the draft is the
        # whole config, so this agrees with what the route already rejected.
        _assert_capability_invariants(draft.workflow_capabilities or {}, field="workflow_capabilities")
        agent = await self._agents.create(
            project_id=project_id,
            name=draft.name.strip(),
            model_hint=draft.model_hint,
            model_id=draft.model_id,
            effort=draft.effort,
            key_group_id=draft.key_group_id,
            system_prompt=draft.system_prompt or "",
            rag_config_id=draft.rag_config_id,
            knowmap_config_id=draft.knowmap_config_id,
            context_mode=draft.context_mode or ContextMode.GENERAL,
            context_token_cap=draft.context_token_cap,
            skill_index_token_cap=draft.skill_index_token_cap,
            temperature=draft.temperature,
            top_p=draft.top_p,
            seed=draft.seed,
            a2a_enabled=bool(draft.a2a_enabled) if draft.a2a_enabled is not None else False,
            wakeup_config=wakeup,
            # Mirror the config exactly: an explicit empty {} ("inert by choice")
            # is a real authored baseline, so it must not collapse to None here
            # the way a truthiness check would.
            wakeup_authored_snapshot=wakeup,
            workflow_capabilities=draft.workflow_capabilities or {},
        )
        await self.provision_tool_singletons(
            agent.id,
            file_search_enabled=agent.rag_config_id is not None,
        )
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
            await self.assert_key_group_in_project(
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
        # SEC-H1 project guard when (re)attaching a RAG config. `clear_rag_config`
        # wins over a stale id, so only validate an actual attach (the create
        # path does the same check).
        if not draft.clear_rag_config and draft.rag_config_id is not None:
            await self._assert_rag_config_in_project(
                rag_config_id=draft.rag_config_id,
                project_id=current.project_id,
            )
        effective_knowmap_id = (
            None
            if draft.clear_knowmap_config
            else draft.knowmap_config_id
            if draft.knowmap_config_id is not None
            else current.knowmap_config_id
        )
        is_explicit_knowmap_attach = draft.knowmap_config_id is not None
        # Re-validate whenever an attach could newly introduce the R11.01
        # conflict: an explicit (re)attach, or the agent's own key_group_id
        # moving while a config is already attached. An explicit attach must
        # fail loudly on a bad id; the implicit recheck must not (see
        # `require_exists` on `_assert_knowmap_config_compatible`).
        if effective_knowmap_id is not None and (
            is_explicit_knowmap_attach or effective_kg != current.key_group_id
        ):
            # Serialise the attach/re-key against a concurrent builder-group change
            # on the same map (R11.25 / F-14) — the config-id advisory lock is held
            # through this transaction's commit, so a losing attach sees the new
            # builder and is rejected here rather than persisting a collision.
            await advisory_xact_lock(self._db, knowmap_builder_lock_key(effective_knowmap_id))
            await self._assert_knowmap_config_compatible(
                knowmap_config_id=effective_knowmap_id,
                project_id=current.project_id,
                key_group_id=effective_kg,
                require_exists=is_explicit_knowmap_attach,
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
        if draft.clear_effort:
            values["effort"] = None
        elif draft.effort is not None:
            values["effort"] = draft.effort.value
        if draft.key_group_id is not None:
            values["key_group_id"] = draft.key_group_id
        if draft.system_prompt is not None:
            values["system_prompt"] = draft.system_prompt
        if draft.clear_rag_config:
            values["rag_config_id"] = None
        elif draft.rag_config_id is not None:
            values["rag_config_id"] = draft.rag_config_id
        if draft.clear_knowmap_config:
            values["knowmap_config_id"] = None
        elif draft.knowmap_config_id is not None:
            values["knowmap_config_id"] = draft.knowmap_config_id
        if draft.context_mode is not None:
            values["context_mode"] = draft.context_mode.value
        if draft.clear_context_token_cap:
            values["context_token_cap"] = None
        elif draft.context_token_cap is not None:
            values["context_token_cap"] = draft.context_token_cap
        if draft.clear_skill_index_token_cap:
            values["skill_index_token_cap"] = None
        elif draft.skill_index_token_cap is not None:
            values["skill_index_token_cap"] = draft.skill_index_token_cap
        if "skill_index_token_cap" in values:
            # AC-6: the cap must still fit what is already bound. The DB CHECK only bounds
            # the value at 16000 and cannot see the agent's bound set, so without this the
            # write is accepted and then silently violated on every turn — [R31.14] leaves
            # no runtime truncation to absorb the overflow.
            await self._skills.ensure_index_cap_fits(agent_id, values["skill_index_token_cap"])
        if draft.clear_temperature:
            values["temperature"] = None
        elif draft.temperature is not None:
            values["temperature"] = draft.temperature
        if draft.clear_top_p:
            values["top_p"] = None
        elif draft.top_p is not None:
            values["top_p"] = draft.top_p
        if draft.clear_seed:
            values["seed"] = None
        elif draft.seed is not None:
            values["seed"] = draft.seed
        if draft.a2a_enabled is not None:
            values["a2a_enabled"] = draft.a2a_enabled
        if draft.wakeup_config is not None:
            # Additive, not replacing: the column is free-form, so a payload that
            # models only part of it (every UI editor does) would otherwise delete
            # designer keys such as `soft_bounds` (R15.08). An explicit null in the
            # payload deletes that key — see `merge_json_config`. The merge always
            # runs before normalization (Q-4): normalizing first would materialize
            # defaults for fields the caller omitted and merge over the stored
            # values, turning a partial PATCH into a silent reset. Not applied on
            # a `replace_wakeup_config` restore — see `_normalize_wakeup_config`.
            values["wakeup_config"] = _merge_and_normalize_wakeup(
                current.wakeup_config,
                draft.wakeup_config,
                replace=draft.replace_wakeup_config,
                field="wakeup_config",
            )
            # Human edit → update the authored snapshot (G.5).
            # System actor (uuid(int=0)) updates are self-modifications
            # and should NOT overwrite the authored snapshot.
            #
            # The snapshot is merged too — writing the submitted fragment is what
            # turns a dropped key into an unrecoverable loss — but it merges over
            # the *previous snapshot*, not over the live config. The live config
            # may carry runtime drift from R15.06 self-modification that has not
            # been refreshed away yet; merging that in would launder the agent's
            # own edits into the designer's baseline and R15.09 could never
            # restore them. With no snapshot yet there is no baseline to protect,
            # so the live config is the best available base.
            if actor_user_id != _SYSTEM_ACTOR_ID:
                if current.wakeup_authored_snapshot is None:
                    # No prior snapshot: the base falls back to current.wakeup_config,
                    # the exact same base (plus the same patch and replace flag)
                    # already merged into values["wakeup_config"] above -- reuse
                    # that result instead of re-running the identical
                    # merge/normalize/bounds-check chain a second time
                    # (2026-07-28-wakeup-config-validation-and-patch-semantics F-6).
                    merged_snapshot = values["wakeup_config"]
                else:
                    merged_snapshot = _merge_and_normalize_wakeup(
                        current.wakeup_authored_snapshot,
                        draft.wakeup_config,
                        replace=draft.replace_wakeup_config,
                        field="wakeup_authored_snapshot",
                    )
                # `_merge_and_normalize_wakeup`'s non-replace path always re-emits
                # WakeupConfig.to_dict()'s unconditional keys, so `merged_snapshot`
                # can never be empty -- the `or None` fallback this used to have
                # was dead code (F-5).
                values["wakeup_authored_snapshot"] = merged_snapshot
        if draft.wakeup_last_refreshed_at is not None:
            values["wakeup_last_refreshed_at"] = draft.wakeup_last_refreshed_at
        if draft.workflow_capabilities is not None:
            merged_capabilities = merge_json_config(
                current.workflow_capabilities, draft.workflow_capabilities
            )
            _assert_config_within_bounds(merged_capabilities, field="workflow_capabilities")
            _assert_capability_invariants(merged_capabilities, field="workflow_capabilities")
            values["workflow_capabilities"] = merged_capabilities

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
        # AC-38: unbind this agent's skills and soft-delete its agent-scoped ones, in this
        # transaction. 0056's FK CASCADE cannot stand in for it — a FK fires only on a
        # physical DELETE, never on the soft-delete UPDATE above, so relying on it would
        # leave every binding live and pointing at a dead agent (F-18, the trap 0054
        # exists to repair). Same shape as `clear_config_bindings`.
        unbound = await self._skills.cascade_agent_deleted(agent_id)
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="agent.deleted",
                actor_user_id=actor_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=agent_id,
                metadata={"unbound_skill_ids": [str(s) for s in unbound]},
                request_id=request_id,
            ),
        )

    async def admin_restore(
        self,
        *,
        agent_id: uuid.UUID,
        admin_user_id: uuid.UUID,
        actor_ip: str | None,
        request_id: uuid.UUID | None = None,
    ) -> bool:
        """Admin restore of a soft-deleted agent (R8.13): pure deleted_at clear,
        emitting admin.restore_resource. Restoring into a (project_id, name) slot
        a live agent has since taken raises RestoreConflict (route maps to 409)."""
        try:
            restored = await self._agents.restore(agent_id)
        except IntegrityError as exc:
            raise_restore_conflict(
                exc, unique_constraint="uq_agents_project_name_active", resource_type="agent"
            )
        if not restored:
            return False
        await audit.emit(
            self._db,
            audit.AuditEvent(
                action="admin.restore_resource",
                actor_user_id=admin_user_id,
                actor_ip=actor_ip,
                resource_type="agent",
                resource_id=agent_id,
                request_id=request_id,
            ),
        )
        return True

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
                f"singleton tool {tool_type.value} is auto-provisioned; toggle via PATCH /tools/{{id}}"
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
                    preexisting_allowed_tools=frozenset(
                        str(n) for n in existing.config.get("allowed_tools") or []
                    ),
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
                f"singleton tool {existing.tool_type.value} cannot be deleted; disable via PATCH"
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
        For hosted_mcp this is also a re-capture (Fix Design §7 piece 2): a
        successful probe overwrites the server-written tool contract so "Test"
        is the explicit refresh path Q-1 chose over an implicit turn-time fetch.
        """
        if tool.tool_type == AgentToolType.LOCAL_FUNCTION:
            result = await self._probe_function(agent, tool)
        elif tool.tool_type == AgentToolType.HOSTED_MCP:
            result = await self._probe_mcp(agent, tool, runner)
            if result.ok:
                captured, warnings = _sanitize_captured_tools(result.tools)
                await self._tools.capture_mcp_tools(agent_id=agent.id, tool_id=tool.id, tools=captured)
                result = replace(result, warnings=tuple(warnings))
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

        from contexts.agents.application.egress import EgressOutcome
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
            return ToolProbeResult(ok=False, error=f"host {host} is not on the project egress allowlist")

        auth, unsealable = resolve_tool_auth(tool)
        if unsealable:
            return ToolProbeResult(ok=False, error="stored credentials could not be unsealed")

        proxy = egress_proxy_client_from_settings()
        headers = dict(http_cfg.get("headers") or {})
        start = time.monotonic()
        try:
            status, resp_headers, body = await proxy.request(
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
        outcome = EgressOutcome.from_proxy_response(status, resp_headers, body)
        # Only a 2xx counts as a passing test; a 3xx/4xx/5xx is reachable but not
        # a pass (a 3xx body is empty by construction — the egress proxy never
        # follows redirects — and e.g. a 401 means the configured credential is
        # being rejected).
        if outcome.ok:
            error = None
        elif 300 <= status < 400:
            error = f"HTTP {status} — {outcome.redirect_detail}"
        else:
            error = f"HTTP {status}"
        return ToolProbeResult(ok=outcome.ok, status=status, duration_ms=duration, error=error)

    async def _probe_mcp(self, agent: Agent, tool: AgentTool, runner: Any) -> ToolProbeResult:
        import time

        from contexts.agents.application.runtime.builtin_tools import (
            function_egress_allowed,
            resolve_tool_auth,
        )

        cfg = tool.config or {}

        # Mirrors _probe_function's pre-check. Package-sourced bindings have no
        # single knowable host (the installed server decides what it calls at
        # runtime, all still gated by the proxy allowlist), so only a
        # url-sourced reference can be checked ahead of time.
        if cfg.get("source", "url") == "url":
            reference = str(cfg.get("reference", ""))
            host, allowed = await function_egress_allowed(
                self._db, project_id=agent.project_id, url=reference
            )
            if host and not allowed:
                return ToolProbeResult(ok=False, error=f"host {host} is not on the project egress allowlist")

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
            tools=tuple(result.tools),
            duration_ms=result.duration_ms,
            error=result.error,
        )

    async def provision_tool_singletons(
        self,
        agent_id: uuid.UUID,
        *,
        file_search_enabled: bool = False,
    ) -> None:
        """Called on agent create — idempotent insertion of the 4 singleton rows."""
        await self._tools.provision_singletons(
            agent_id=agent_id,
            web_search=True,
            code_interpreter=False,
            file_workspace=True,
            file_search_enabled=file_search_enabled,
        )


__all__ = ["AgentService"]
